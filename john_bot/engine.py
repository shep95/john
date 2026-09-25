"""the engine loop.

startup: replay the last WARMUP_CANDLES closed candles of every symbol through
the shepherd engine, so the pattern memory and the validation record exist
before the first decision.

each tick:
  1. settle anything the broker closed (paper: on the new candle; live: exchange).
  2. feed every newly closed candle to that symbol's shepherd engine.
  3. follow the selected simulated engine (projection or framework):
       open    → market entry          pending → resting limit entry
       cancel  → cancel the limit      exit(time) → close at market
     stops and targets are resting on the exchange, so they need no action.
  4. real orders go out only while the validation gate is open and no circuit
     breaker has tripped. with the gate closed, signals are logged, not traded.

one position at a time across all symbols.
"""
from __future__ import annotations

import datetime
import math
import threading
import time
from typing import Dict, List, Optional

from .broker import ClosedTrade, make_broker, position_dict
from .config import CONFIG, Config
from .gate import GateResult, evaluate
from .logutil import get_logger
from .market import Candle, MarketData
from .notify import BLUE, GREEN, GREY, RED, make_notifier, money
from .shepherd import Event, Reading, Shepherd
from .sizing import TradePlan, build_plan
from .state import BotState, TradeRecord, load_state, save_state


class Engine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.log = get_logger(level=cfg.log_level)
        if not cfg.symbols:
            raise ValueError("no symbols configured")
        self.market = MarketData(testnet=cfg.testnet)
        self.broker = make_broker(cfg, self.market)
        self.state = load_state(cfg.state_path)
        self._lock = threading.Lock()
        self._flatten_req = False
        self._sz_dec: Dict[str, int] = {}
        self.shepherds: Dict[str, Shepherd] = {}
        self.last_open_ms: Dict[str, int] = {}
        self.last_close: Dict[str, float] = {}
        self._gate_open: Dict[str, bool] = {}

        self._maybe_reset_stats()
        if self.state.sizing_base <= 0:
            base = self.broker.equity()
            self.state.sizing_base = base
            self.state.start_base = base
            self.state.peak_base = base
        if hasattr(self.broker, "_equity") and self.state.sizing_base > 0:
            self.broker._equity = self.state.sizing_base

        self._restore_open_position()
        self._warmup()
        self.notifier = make_notifier(cfg, self)
        self.notifier.start()

    # -- setup ---------------------------------------------------------------
    def _maybe_reset_stats(self) -> None:
        if self.cfg.reset_id == self.state.reset_id:
            return
        self.log.info("resetting scoreboard (reset_id %r -> %r)", self.state.reset_id, self.cfg.reset_id)
        keep_pos = self.state.open_position
        self.state = BotState(reset_id=self.cfg.reset_id, open_position=keep_pos)
        save_state(self.cfg.state_path, self.state)

    def _restore_open_position(self) -> None:
        if self.state.open_position:
            try:
                self.broker.restore_position(self.state.open_position)
            except Exception as e:
                self.log.warning("could not restore persisted position: %s", e)
        if hasattr(self.broker, "sync_from_exchange"):
            try:
                self.broker.sync_from_exchange()
            except Exception as e:
                self.log.warning("exchange position sync failed: %s", e)
        pos = self.broker.any_position()
        self.state.open_position = position_dict(pos) if pos else None

    def _warmup(self) -> None:
        for sym in self.cfg.symbols:
            sh = Shepherd(self.cfg.params, primary=self.cfg.engine, ap=self.cfg.ash_params)
            candles = self.market.closed_candles(sym, self.cfg.interval, self.cfg.warmup_candles)
            for c in candles:
                sh.update(c)
            self.shepherds[sym] = sh
            if candles:
                self.last_open_ms[sym] = candles[-1].open_time
                self.last_close[sym] = candles[-1].close
            g = evaluate(sh, self.cfg)
            self._gate_open[sym] = g.open
            self.log.info("[warmup] %s %s: replayed %d candles · %s", sym, self.cfg.interval, len(candles), g.summary())

    # -- helpers -------------------------------------------------------------
    def sz_decimals(self, symbol: str) -> int:
        if symbol not in self._sz_dec:
            self._sz_dec[symbol] = self.market.sz_decimals(symbol)
        return self._sz_dec[symbol]

    def gate(self, symbol: str) -> GateResult:
        return evaluate(self.shepherds[symbol], self.cfg)

    def _utc_today(self) -> str:
        return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")

    def _entry_block(self) -> Optional[str]:
        """live-account circuit breakers. these never touch the simulated record."""
        s = self.state
        if s.paused:
            return "paused (manual or drawdown) — /resume to continue"
        if time.time() < s.pause_until:
            return f"losing-streak pause for {(s.pause_until - time.time()) / 60:.0f}m"
        if s.daily_session_date == self._utc_today():
            cap = s.sizing_base * self.cfg.max_daily_loss_pct
            if cap > 0 and s.daily_loss_usd >= cap:
                return "daily loss cap reached — resumes next UTC day"
        return None

    # -- trade lifecycle -----------------------------------------------------
    def _apply_compounding(self, pnl: float):
        with self._lock:
            if pnl >= 0:
                compounded = self.cfg.compound_frac * pnl
                banked = pnl - compounded
                self.state.sizing_base += compounded
                self.state.banked_reserve += banked
            else:
                compounded, banked = pnl, 0.0
                self.state.sizing_base += pnl
            self.state.sizing_base = max(self.state.sizing_base, self.cfg.min_sizing_base)
            if self.cfg.max_sizing_base > 0 and self.state.sizing_base > self.cfg.max_sizing_base:
                overflow = self.state.sizing_base - self.cfg.max_sizing_base
                self.state.sizing_base = self.cfg.max_sizing_base
                self.state.banked_reserve += overflow
                banked += overflow
        return compounded, banked

    def _finish_trade(self, closed: ClosedTrade) -> None:
        s = self.state
        s.open_position = None
        s.record(TradeRecord(closed.symbol, closed.side, closed.entry, closed.exit, closed.pnl,
                             closed.reason, closed.opened_at, closed.closed_at, closed.duration_sec,
                             closed.r, closed.risk_usd))
        compounded, banked = self._apply_compounding(closed.pnl)

        today = self._utc_today()
        if s.daily_session_date != today:
            s.daily_session_date, s.daily_loss_usd = today, 0.0
        if closed.pnl < 0:
            s.daily_loss_usd += abs(closed.pnl)
            s.consecutive_losses += 1
        elif closed.pnl > 0:
            s.consecutive_losses = 0

        s.peak_base = max(s.peak_base, s.sizing_base)
        drawdown = (s.peak_base - s.sizing_base) / s.peak_base if s.peak_base > 0 else 0.0
        if self.cfg.max_drawdown_pct > 0 and drawdown >= self.cfg.max_drawdown_pct and not s.paused:
            s.paused = True
            self.notifier.send_embed("🛑 drawdown breaker — paused", [
                ("drawdown", f"{drawdown*100:.1f}%", True),
                ("action", "no new entries until /resume", False)], RED, ping=True)
        if (self.cfg.max_consecutive_losses > 0 and s.consecutive_losses >= self.cfg.max_consecutive_losses):
            s.pause_until = time.time() + self.cfg.loss_streak_pause_sec
            s.consecutive_losses = 0
            self.notifier.send_embed("⏸️ losing streak — timed pause", [
                ("pause", f"{self.cfg.loss_streak_pause_sec/3600:.1f}h", True)], RED, ping=True)

        self.log.info("settled %s %s pnl=%.4f (%.2fR) base=%.2f banked=%.2f W/L=%d/%d",
                      closed.symbol, closed.reason, closed.pnl, closed.r, s.sizing_base,
                      s.banked_reserve, s.wins, s.losses)
        self._alert_exit(closed, compounded, banked)
        save_state(self.cfg.state_path, s)

    def _enter(self, sym: str, ev: Event, reading: Reading) -> None:
        if self.broker.any_position() is not None or self.broker.pending() is not None:
            self.log.info("%s %s signal skipped: already in a trade or holding a limit", sym, ev.kind)
            return
        g = self.gate(sym)
        if self.cfg.is_live and not g.open:
            # the record has not earned real money: log the signal, trade nothing
            self.log.info("%s %s %s signal NOT traded · %s", sym, "LONG" if ev.dir == 1 else "SHORT",
                          ev.kind, g.summary())
            return
        block = self._entry_block()
        if block:
            self.log.info("%s signal skipped: %s", sym, block)
            return
        kind = "limit" if ev.kind == "pending" else "market"
        plan = build_plan(sym, ev.dir, ev.price, ev.sl, ev.tp, self.state.sizing_base,
                          self.sz_decimals(sym), self.cfg, kind=kind,
                          reason=f"{self.cfg.engine} · {reading.state_name} · p↑ {reading.p_up:.2f}")
        if plan is None:
            return
        ok = self.broker.place_limit(plan) if kind == "limit" else self.broker.open_market(plan, ev.price)
        if ok:
            pos = self.broker.any_position()
            self.state.open_position = position_dict(pos) if pos else None
            save_state(self.cfg.state_path, self.state)
            self._alert_entry(plan, reading, g)

    def _follow(self, sym: str, reading: Reading, fresh: bool) -> None:
        for ev in reading.events:
            if ev.engine != self.cfg.engine:
                continue
            if ev.kind in ("open", "pending"):
                # after downtime the bot catches up several candles at once; an entry
                # signal from an old candle is a price that no longer exists
                if fresh:
                    self._enter(sym, ev, reading)
                else:
                    self.log.info("%s stale %s signal from a caught-up candle ignored", sym, ev.kind)
            elif ev.kind == "cancel":
                self.broker.cancel_pending(sym)
            elif ev.kind == "exit" and ev.code == 2:
                pos = self.broker.any_position()
                if pos is not None and pos.symbol == sym:
                    closed = self.broker.force_close(sym, reading.close, reason="TIME")
                    if closed:
                        self._finish_trade(closed)

    def _process_symbol(self, sym: str) -> None:
        after = self.last_open_ms.get(sym, 0)
        new = self.market.closed_since(sym, self.cfg.interval, after) if after else \
            self.market.closed_candles(sym, self.cfg.interval, self.cfg.warmup_candles)
        sh = self.shepherds[sym]
        span = self.market.interval_ms(self.cfg.interval)
        now_ms = int(time.time() * 1000)
        for i, c in enumerate(new):
            fresh = i == len(new) - 1 and c.close_time >= now_ms - span
            for closed in self.broker.on_candle(sym, c):
                self._finish_trade(closed)
            reading = sh.update(c)
            self.last_open_ms[sym] = c.open_time
            self.last_close[sym] = c.close
            self._log_reading(sym, reading)
            self._follow(sym, reading, fresh)
            g = evaluate(sh, self.cfg)
            if self._gate_open.get(sym) is not g.open:
                self._gate_open[sym] = g.open
                self.notifier.send_embed(f"{'🟢' if g.open else '⚪'} {sym} gate {'OPEN' if g.open else 'CLOSED'}",
                                         [("record", g.summary(), False)], GREEN if g.open else GREY)

    def _log_reading(self, sym: str, r: Reading) -> None:
        pj = r.projection
        call = {1: "long", -1: "short"}.get(pj.call, "no call")
        plan = pj.msg or pj.mode or "—"
        self.log.info("%s %s · %s · vel %+.2f · p↑ %s · projection %s (%s) · gate %s", sym,
                      datetime.datetime.fromtimestamp(r.time_ms / 1000, datetime.timezone.utc).strftime("%m-%d %H:%M"),
                      r.state_name, r.velocity if not math.isnan(r.velocity) else 0.0,
                      "—" if math.isnan(r.p_up) else f"{r.p_up:.2f}", call, plan, r.gate or "clear")

    def _handle_flatten(self) -> None:
        with self._lock:
            if not self._flatten_req:
                return
            self._flatten_req = False
        pos = self.broker.any_position()
        if pos is not None:
            price = self.market.last_price(pos.symbol, self.cfg.interval)
            closed = self.broker.force_close(pos.symbol, price)
            if closed is not None:
                self._finish_trade(closed)
        lim = self.broker.pending()
        if lim is not None:
            self.broker.cancel_pending(lim.symbol)

    # -- alerts --------------------------------------------------------------
    def _alert_entry(self, plan: TradePlan, r: Reading, g: GateResult) -> None:
        margin = plan.notional / self.cfg.leverage if self.cfg.leverage else plan.notional
        verb = "📈 trade entered" if plan.kind == "market" else "📌 limit placed"
        self.notifier.send_embed(verb, [
            ("symbol", f"{plan.symbol} {self.cfg.leverage}x · {self.cfg.interval}", True),
            ("side", "🟢 LONG" if plan.is_buy else "🔴 SHORT", True),
            ("mode", "LIVE" if self.cfg.is_live else "PAPER", True),
            ("entry", f"{plan.entry_ref:.6g}", True),
            ("stop", f"{plan.stop_loss:.6g}  (-{money(plan.risk_usd)})", True),
            ("target", f"{plan.take_profit:.6g}  ({plan.rr:.2f}R)", True),
            ("size / margin", f"{plan.size} · {money(margin)}", True),
            ("record", g.summary(), False),
            ("read", plan.reason, False),
        ], GREEN, ping=True)

    def _alert_exit(self, closed: ClosedTrade, compounded: float, banked: float) -> None:
        head = {"TP": "✅ target", "SL": "🛑 stop", "TIME": "⏱️ time stop"}.get(closed.reason, "⏹️ flattened")
        total = self.state.wins + self.state.losses
        self.notifier.send_embed(f"{head} — {money(closed.pnl)} ({closed.r:+.2f}R)", [
            ("symbol", f"{closed.symbol} {closed.side}", True),
            ("entry → exit", f"{closed.entry:.6g} → {closed.exit:.6g}", True),
            ("held", f"{closed.duration_sec/60:.0f}m", True),
            ("compounded", money(compounded if closed.pnl >= 0 else 0.0), True),
            ("banked", money(banked), True),
            ("sizing base", money(self.state.sizing_base), True),
            ("realized pnl", money(self.state.realized_pnl), True),
            ("record", f"{self.state.wins}W / {self.state.losses}L ({total})", True),
        ], GREEN if closed.pnl >= 0 else RED, ping=True)

    # -- command surface (called from the discord thread) --------------------
    def set_paused(self, value: bool) -> None:
        with self._lock:
            self.state.paused = value
            if not value:
                self.state.pause_until = 0.0
                self.state.peak_base = self.state.sizing_base  # resume resets the drawdown reference
        save_state(self.cfg.state_path, self.state)

    def request_flatten(self) -> None:
        with self._lock:
            self._flatten_req = True

    def snapshot(self) -> dict:
        with self._lock:
            s = self.state
            total = s.wins + s.losses
            roi = ((s.sizing_base + s.banked_reserve - s.start_base) / s.start_base * 100) if s.start_base > 0 else 0.0
            gates = {sym: self._gate_open.get(sym, False) for sym in self.cfg.symbols}
            return {
                "mode": "LIVE" if self.cfg.is_live else "PAPER",
                "symbols": ",".join(self.cfg.symbols), "interval": self.cfg.interval,
                "engine": self.cfg.engine, "paused": s.paused or time.time() < s.pause_until,
                "block": self._entry_block(), "gates": gates,
                "sizing_base": s.sizing_base, "banked_reserve": s.banked_reserve,
                "start_base": s.start_base, "realized_pnl": s.realized_pnl,
                "wins": s.wins, "losses": s.losses,
                "winrate": (s.wins / total * 100) if total else 0.0, "roi": roi,
            }

    def validation_report(self) -> Dict[str, dict]:
        out = {}
        for sym, sh in self.shepherds.items():
            out[sym] = {"validation": sh.validation(), "gate": evaluate(sh, self.cfg)}
        return out

    def position_report(self) -> Optional[dict]:
        pos = self.broker.any_position()
        if pos is None:
            return None
        mark = self.market.last_price(pos.symbol, self.cfg.interval)
        d = 1 if pos.is_buy else -1
        return {"symbol": pos.symbol, "side": pos.side, "entry": pos.entry, "mark": mark,
                "size": pos.size, "sl": pos.stop_loss, "tp": pos.take_profit,
                "unrealized": (mark - pos.entry) * pos.size * d, "held_sec": time.time() - pos.opened_at}

    # -- main loop -----------------------------------------------------------
    def tick(self) -> None:
        self._handle_flatten()
        for closed in self.broker.poll():
            self._finish_trade(closed)
        for sym in self.cfg.symbols:
            self._process_symbol(sym)

    def run(self) -> None:
        self.log.info("john bot starting | mode=%s | engine=%s | symbols=%s | interval=%s | lev=%dx",
                      "LIVE" if self.cfg.is_live else "PAPER", self.cfg.engine, ",".join(self.cfg.symbols),
                      self.cfg.interval, self.cfg.leverage)
        self.log.info("config: %s", self.cfg.redacted())
        while True:
            try:
                self.tick()
            except KeyboardInterrupt:
                self.log.info("shutdown requested")
                break
            except Exception as e:
                self.log.exception("tick error: %s", e)
                self._alert_feed_trouble(e)
            time.sleep(self.cfg.poll_seconds)

    def _alert_feed_trouble(self, err: Exception) -> None:
        now = time.time()
        if now - getattr(self, "_last_feed_alert", 0.0) < 600:
            return
        self._last_feed_alert = now
        try:
            self.notifier.send_embed("⚠️ data / engine error — operator should check", [
                ("error", str(err)[:300], False),
                ("note", "the bot skipped this cycle; it will keep retrying", False)], RED, ping=True)
        except Exception:
            pass


def main() -> None:
    Engine(CONFIG).run()


if __name__ == "__main__":
    main()
