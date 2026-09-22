"""the engine loop.

what it does each tick:
  1. if a position is open -> settle it (paper: check the just-closed candle;
     live: check hyperliquid). on close, record duration and set the cooldown.
  2. cooldown (john's rule): the time the *next* trade waits equals the time the
     *last* trade took to hit its TP/SL. duration-in = duration-out.
  3. rotation (john's rule): after each closed trade, switch the active symbol
     ETH <-> DOGE, so the bot alternates between the two.
  4. otherwise analyze the active symbol's last closed candles, and if the read
     gives a clear winner+echo signal, open a sized position with SL/TP.

only one position at a time. blank/unclear reads = no trade, wait for context.
"""
from __future__ import annotations

import datetime
import threading
import time
from typing import Optional

from .analysis import NO_TRADE, analyze
from .broker import ClosedTrade, make_broker
from .config import CONFIG, Config
from .logutil import get_logger
from .market import Candle, MarketData
from .notify import BLUE, GREEN, RED, money, make_notifier
from .reflect import reflect
from .state import BotState, TradeRecord, load_state, save_state
from .strategy import TradePlan, build_plan


class Engine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.log = get_logger(level=cfg.log_level)
        self.market = MarketData(testnet=cfg.testnet)
        self.broker = make_broker(cfg, self.market)
        self.state = load_state(cfg.state_path)
        self._sz_dec: dict = {}
        self._lock = threading.Lock()
        self._flatten_req = False
        self._last_reflection = None
        self._consecutive_losses = 0
        self._peak_sizing_base = 0.0
        if not cfg.symbols:
            raise ValueError("no symbols configured")
        self.state.rotation_idx %= len(cfg.symbols)

        # scoreboard reset: if RESET_ID changed, wipe pnl / win-rate / compounding
        self._maybe_reset_stats()

        # initialise the compounding sizing base on first run
        if self.state.sizing_base <= 0:
            base = self.broker.equity()
            self.state.sizing_base = base
            self.state.start_base = base
        self._peak_sizing_base = max(self.state.sizing_base, self.state.start_base)

        # paper mode: re-seed equity from the persisted sizing base so paper
        # sizing stays consistent across restarts (cfg.paper_equity is first-boot only)
        if hasattr(self.broker, "_equity") and self.state.sizing_base > 0:
            self.broker._equity = self.state.sizing_base

        # resume tracking any position that was open before a restart
        self._restore_open_position()

        # discord (bot with commands, or webhook alerts, or silent)
        self.notifier = make_notifier(cfg, self)
        self.notifier.start()

    def _maybe_reset_stats(self) -> None:
        """wipe the scoreboard (pnl, win/loss, compounding, trade history) when
        the RESET_ID env var differs from what's stored -- so changing RESET_ID
        and redeploying gives a clean track record exactly once. an open live
        position and the rotation are left untouched."""
        if self.cfg.reset_id == self.state.reset_id:
            return
        self.log.info("resetting scoreboard (reset_id %r -> %r)",
                      self.state.reset_id, self.cfg.reset_id)
        self.state.realized_pnl = 0.0
        self.state.wins = 0
        self.state.losses = 0
        self.state.banked_reserve = 0.0
        self.state.sizing_base = 0.0   # re-initialised from equity below
        self.state.start_base = 0.0
        self.state.last_trade_duration = 0.0
        self.state.trades = []
        self.state.learned = {}   # forget self-learned rules on a fresh scoreboard
        self.state.daily_loss_usd = 0.0
        self.state.reset_id = self.cfg.reset_id
        # a reset is an acknowledgment of what came before, not a clean escape:
        # require a real pause before the next entry.
        self.state.cooldown_until = time.time() + (self.cfg.min_cooldown_sec * 6)
        self.log.info("scoreboard wiped. mandatory cooldown applied before next entry.")
        save_state(self.cfg.state_path, self.state)

    def _restore_open_position(self) -> None:
        """after a restart (railway redeploy), re-adopt the open position so the
        bot keeps managing it instead of opening a second one. first from the
        persisted state, then reconciled against the exchange in live mode in
        case the state was lost or stale."""
        d = self.state.open_position
        if d:
            try:
                self.broker.restore_position(d)
            except Exception as e:
                self.log.warning("could not restore persisted position: %s", e)
        if hasattr(self.broker, "sync_from_exchange"):
            try:
                self.broker.sync_from_exchange()
            except Exception as e:
                self.log.warning("exchange position sync failed: %s", e)
        # keep persisted state consistent with whatever we actually hold now
        pos = self.broker.any_position()
        if pos is None:
            self.state.open_position = None
        else:
            self.state.open_position = self._position_dict(pos)

    # -- helpers -------------------------------------------------------------
    def active_symbol(self) -> str:
        return self.cfg.symbols[self.state.rotation_idx % len(self.cfg.symbols)]

    def _advance_rotation(self) -> None:
        self.state.rotation_idx = (self.state.rotation_idx + 1) % len(self.cfg.symbols)

    def sz_decimals(self, symbol: str) -> int:
        if symbol not in self._sz_dec:
            self._sz_dec[symbol] = self.market.sz_decimals(symbol)
        return self._sz_dec[symbol]

    @staticmethod
    def _position_dict(pos) -> dict:
        """serialise an open Position for state.json (restart persistence)."""
        return {
            "symbol": pos.symbol, "side": pos.side, "is_buy": pos.is_buy,
            "size": pos.size, "entry": pos.entry, "stop_loss": pos.stop_loss,
            "take_profit": pos.take_profit, "opened_at": pos.opened_at,
        }

    def _cooldown_from(self, closed: ClosedTrade) -> float:
        if self.cfg.cooldown_mode == "none":
            return 0.0
        base = closed.duration_sec  # "trade_duration" mode
        return max(self.cfg.min_cooldown_sec, min(self.cfg.max_cooldown_sec, base))

    def _latest_closed_candle(self, symbol: str) -> Optional[Candle]:
        cs = self.market.closed_candles(symbol, self.cfg.interval, 1)
        return cs[-1] if cs else None

    # -- lifecycle steps -----------------------------------------------------
    def _apply_compounding(self, pnl: float) -> tuple:
        """compound COMPOUND_FRAC of any profit into the sizing base, bank the
        rest; losses come fully out of the sizing base. returns (compounded, banked)."""
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
            # the ceiling is the sabbath of the sizing base: everything above it
            # is banked, never recycled into risk.
            if self.cfg.max_sizing_base > 0 and self.state.sizing_base > self.cfg.max_sizing_base:
                overflow = self.state.sizing_base - self.cfg.max_sizing_base
                self.state.sizing_base = self.cfg.max_sizing_base
                self.state.banked_reserve += overflow
                banked += overflow
        return compounded, banked

    def _check_daily_loss_cap(self, closed: ClosedTrade) -> None:
        """session-level circuit breaker: once the day's realized loss reaches
        MAX_DAILY_LOSS_PCT of the sizing base, pause new entries until a human
        resumes. know the condition of your flocks (Prov 27:23)."""
        today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        if self.state.daily_session_date != today:
            self.state.daily_session_date = today
            self.state.daily_loss_usd = 0.0
        if closed.pnl < 0:
            self.state.daily_loss_usd += abs(closed.pnl)
        cap = self.state.sizing_base * self.cfg.max_daily_loss_pct
        if cap > 0 and self.state.daily_loss_usd >= cap and not self.state.paused:
            self.state.paused = True
            self.log.warning("daily loss cap hit (%.2f >= %.2f) -- paused", self.state.daily_loss_usd, cap)
            self.notifier.send_embed(
                "🛑 daily loss cap reached — session paused",
                [("daily loss", money(self.state.daily_loss_usd), True),
                 ("cap", money(cap), True),
                 ("action", "no new entries until you /resume", False)],
                RED, ping=True,
            )

    def _finish_trade(self, closed: ClosedTrade) -> None:
        # pull the entry-time read features (stashed at open) for reflection
        feats = (self.state.open_position or {}).get("features", {}) if self.state.open_position else {}
        self.state.open_position = None  # flat again -> nothing to resume on restart
        self.state.record(TradeRecord(
            symbol=closed.symbol, side=closed.side, entry=closed.entry, exit=closed.exit,
            pnl=closed.pnl, reason=closed.reason, opened_at=closed.opened_at,
            closed_at=closed.closed_at, duration_sec=closed.duration_sec,
            strength=float(feats.get("strength", 0.0)), net_force=float(feats.get("net_force", 0.0)),
            passion=float(feats.get("passion", 0.0)), conflict=float(feats.get("conflict", 0.0)),
            echo_len=int(feats.get("echo_len", 0)), rr=float(feats.get("rr", 0.0)),
            risk_usd=float(feats.get("risk_usd", 0.0)),
        ))
        compounded, banked = self._apply_compounding(closed.pnl)
        if closed.pnl < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0
        self._peak_sizing_base = max(self._peak_sizing_base, self.state.sizing_base)
        drawdown = ((self._peak_sizing_base - self.state.sizing_base) / self._peak_sizing_base
                     if self._peak_sizing_base > 0 else 0.0)
        if (self.cfg.max_consecutive_losses > 0 and
                self._consecutive_losses >= self.cfg.max_consecutive_losses):
            self.state.paused = True
            self.log.warning("loss circuit breaker hit (%d consecutive losses) -- paused",
                             self._consecutive_losses)
        if self.cfg.max_drawdown_pct > 0 and drawdown >= self.cfg.max_drawdown_pct:
            self.state.paused = True
            self.log.warning("drawdown circuit breaker hit (%.2f%% >= %.2f%%) -- paused",
                             drawdown * 100, self.cfg.max_drawdown_pct * 100)
        self._check_daily_loss_cap(closed)
        cd = self._cooldown_from(closed)
        self.state.cooldown_until = time.time() + cd
        self._advance_rotation()
        self.log.info(
            "settled %s %s pnl=%.4f dur=%.0fs -> cooldown %.0fs, next=%s (base=%.2f banked=%.2f W/L=%d/%d)",
            closed.symbol, closed.reason, closed.pnl, closed.duration_sec, cd,
            self.active_symbol(), self.state.sizing_base, self.state.banked_reserve,
            self.state.wins, self.state.losses,
        )
        self._alert_exit(closed, compounded, banked, cd)
        save_state(self.cfg.state_path, self.state)
        self._run_reflection()

    def _run_reflection(self) -> None:
        """runs automatically after every closed trade. it re-derives its rule
        from the LATEST full history each time and REPLACES the learned set --
        so it self-corrects both ways: it adopts a rule when the data supports
        one, changes the threshold as data grows, and drops the rule on its own
        when the data no longer justifies it. no manual step is ever involved."""
        # no reflection noise before there is real data to reflect on
        if len(self.state.trades) < self.cfg.min_trades_to_learn:
            return
        try:
            r = reflect(self.state.trades, self.cfg)
        except Exception as e:
            self.log.warning("reflection failed: %s", e)
            return
        self._last_reflection = r
        self.log.info("[reflect] %s", r.summary)
        if not self.cfg.self_learn:
            # report-only: surface the idea, never silently change its own rules.
            if r.proposal is not None:
                self.log.info("[reflect] proposal (SELF_LEARN off, not adopted): %s", r.proposal.human())
            return

        old = dict(self.state.learned)
        new = {r.proposal.key: r.proposal.threshold} if r.proposal else {}
        if new == old:
            return  # nothing changed this cycle

        # describe what it changed
        if r.proposal is None:
            change = f"dropped rule ({', '.join(old)}) — no longer justified by the data"
            rule_line = "back to base rules"
        elif not old:
            change = f"adopted: {r.proposal.human()}"
            rule_line = r.proposal.human()
        else:
            change = f"changed rule: {', '.join(old)} → {r.proposal.human()}"
            rule_line = r.proposal.human()

        # alert first, adopt second. never silently change your own rules.
        fields = [
            ("reviewed", f"{r.n} trades", True),
            ("win rate", f"{r.winrate:.0f}%", True),
            ("expectancy", f"{r.expectancy:+.2f}R", True),
            ("change", change, False),
            ("active rule", rule_line, False),
            ("its critique", r.worst_pattern, False),
        ]
        self.notifier.send_embed("🧠 rule change — REVIEW (auto-adopted, SELF_LEARN on)",
                                 fields, BLUE, ping=True)
        self.state.learned = new
        save_state(self.cfg.state_path, self.state)
        self.log.info("[reflect] AUTO %s", change)

    def _settle_open(self) -> None:
        pos = self.broker.any_position()
        if pos is None:
            return
        sym = pos.symbol
        candle = self._latest_closed_candle(sym)
        closed = self.broker.settle(sym, candle)
        if closed is None:
            return
        self._finish_trade(closed)

    def _handle_flatten(self) -> None:
        with self._lock:
            if not self._flatten_req:
                return
            self._flatten_req = False
        pos = self.broker.any_position()
        if pos is None:
            return
        price = self.market.last_price(pos.symbol, self.cfg.interval)
        closed = self.broker.force_close(pos.symbol, price)
        if closed is not None:
            self._finish_trade(closed)

    def _maybe_enter(self) -> None:
        now = time.time()
        if self.state.paused:
            return
        # trading-hours window (UTC): outside it, the algorithm rests. 12 on / 12 off.
        hour = datetime.datetime.now(datetime.timezone.utc).hour
        if not (self.cfg.session_start_utc_hour <= hour < self.cfg.session_end_utc_hour):
            return
        if now < self.state.cooldown_until:
            return
        if self.broker.any_position() is not None:
            return

        symbol = self.active_symbol()
        candles = self.market.closed_candles(symbol, self.cfg.interval, self.cfg.history_candles)
        if len(candles) < max(self.cfg.war_window, self.cfg.atr_lookback) + 2:
            self.log.info("waiting for candles on %s (%d)", symbol, len(candles))
            return

        read = analyze(symbol, candles, self.cfg, self.state.learned)
        if read.signal == NO_TRADE:
            self.log.info("%s no-trade: %s", symbol, read.reason)
            return

        # size off the compounding base, not raw account value
        base = self.state.sizing_base
        plan = build_plan(read, base, self.sz_decimals(symbol), self.cfg)
        if plan is None:
            self.log.info("%s signal %s but no valid plan (sizing_base=%.4f, live_equity=%.4f)",
                          symbol, read.signal, base, self.broker.equity())
            return

        self.log.info("%s SIGNAL %s | %s", symbol, read.signal, read.reason)
        opened = self.broker.open(plan)
        if opened:
            pos = self.broker.any_position()
            # persist the open trade so a restart resumes it instead of
            # double-opening on the rotated symbol
            d = self._position_dict(pos) if pos else {}
            # stash the entry-time read so the reflection loop can learn from it
            d["features"] = {
                "strength": read.conviction, "net_force": read.dominance,
                "passion": read.passion, "conflict": read.conflict,
                "echo_len": read.echo.length, "rr": plan.rr,
                "risk_usd": plan.sl_dist * plan.size,  # $ at the stop, for real R
            }
            self.state.open_position = d or None
            self._alert_entry(plan)
            save_state(self.cfg.state_path, self.state)

    # -- alerts --------------------------------------------------------------
    def _alert_entry(self, plan: TradePlan) -> None:
        est_profit = plan.tp_dist * plan.size
        est_loss = plan.sl_dist * plan.size
        # money actually committed to the trade = margin = notional / leverage
        margin = plan.notional / self.cfg.leverage if self.cfg.leverage else plan.notional
        total = self.state.wins + self.state.losses
        wr = (self.state.wins / total * 100) if total else 0.0
        arrow = "🟢 LONG" if plan.is_buy else "🔴 SHORT"
        fields = [
            ("symbol", f"{plan.symbol}  {self.cfg.leverage}x", True),
            ("side", arrow, True),
            ("conviction", f"{plan.conviction:.2f}", True),
            ("entry", f"{plan.entry_ref:.6g}", True),
            ("amount entered", f"{money(margin)} margin", True),
            ("size / notional", f"{plan.size} ({money(plan.notional)})", True),
            ("reward : risk", f"{plan.rr:.2f} : 1", True),
            ("take profit", f"{plan.take_profit:.6g}  (+{money(est_profit)})", True),
            ("stop loss", f"{plan.stop_loss:.6g}  (-{money(est_loss)})", True),
            ("sizing base", money(self.state.sizing_base), True),
            ("win rate", f"{wr:.0f}%  ({self.state.wins}W / {self.state.losses}L)", True),
            ("read", plan.reason, False),
        ]
        self.notifier.send_embed("📈 trade entered", fields, GREEN, ping=True)

    def _alert_exit(self, closed: ClosedTrade, compounded: float, banked: float, cd: float) -> None:
        won = closed.pnl >= 0
        head = "✅ TP hit" if closed.reason == "TP" else ("🛑 SL hit" if closed.reason == "SL" else "⏹️ flattened")
        total = self.state.wins + self.state.losses
        wr = (self.state.wins / total * 100) if total else 0.0
        fields = [
            ("symbol", f"{closed.symbol} {closed.side}", True),
            ("result", head, True),
            ("trade pnl", money(closed.pnl), True),
            ("entry → exit", f"{closed.entry:.6g} → {closed.exit:.6g}", True),
            ("held", f"{closed.duration_sec/60:.0f}m", True),
            ("cooldown", f"{cd/60:.0f}m", True),
            ("compounded", money(compounded if won else 0.0), True),
            ("banked", money(banked), True),
            ("sizing base", money(self.state.sizing_base), True),
            ("realized pnl", money(self.state.realized_pnl), True),
            ("banked total", money(self.state.banked_reserve), True),
            ("win rate", f"{wr:.0f}%", True),
            ("record", f"{self.state.wins}W / {self.state.losses}L  ({total} trades)", True),
        ]
        self.notifier.send_embed(f"{head} — {money(closed.pnl)}", fields,
                                 GREEN if won else RED, ping=True)

    # -- command surface (called from the discord bot thread) ---------------
    def set_paused(self, value: bool) -> None:
        with self._lock:
            self.state.paused = value
        save_state(self.cfg.state_path, self.state)

    def request_flatten(self) -> None:
        with self._lock:
            self._flatten_req = True

    def snapshot(self) -> dict:
        with self._lock:
            s = self.state
            total = s.wins + s.losses
            roi = ((s.sizing_base + s.banked_reserve - s.start_base) / s.start_base * 100
                   if s.start_base > 0 else 0.0)
            return {
                "mode": "LIVE" if self.cfg.is_live else "PAPER",
                "active_symbol": self.active_symbol(),
                "paused": s.paused,
                "sizing_base": s.sizing_base,
                "banked_reserve": s.banked_reserve,
                "start_base": s.start_base,
                "realized_pnl": s.realized_pnl,
                "wins": s.wins,
                "losses": s.losses,
                "winrate": (s.wins / total * 100) if total else 0.0,
                "roi": roi,
                "cooldown_remaining": max(0.0, s.cooldown_until - time.time()),
                "consecutive_losses": self._consecutive_losses,
                "drawdown_pct": ((self._peak_sizing_base - s.sizing_base) / self._peak_sizing_base * 100
                                  if self._peak_sizing_base > 0 else 0.0),
            }

    def reflection_report(self) -> dict:
        """compute the current self-review on demand (for the /reflect command)."""
        r = reflect(self.state.trades, self.cfg)
        return {
            "n": r.n, "winrate": r.winrate, "expectancy": r.expectancy,
            "wins": r.wins, "losses": r.losses, "summary": r.summary,
            "critique": r.worst_pattern,
            "learned": dict(self.state.learned),
            "proposal": r.proposal.human() if r.proposal else None,
        }

    def position_report(self) -> Optional[dict]:
        pos = self.broker.any_position()
        if pos is None:
            return None
        mark = self.market.last_price(pos.symbol, self.cfg.interval)
        direction = 1 if pos.is_buy else -1
        unreal = (mark - pos.entry) * pos.size * direction
        return {
            "symbol": pos.symbol, "side": pos.side, "entry": pos.entry, "mark": mark,
            "size": pos.size, "sl": pos.stop_loss, "tp": pos.take_profit,
            "unrealized": unreal, "held_sec": time.time() - pos.opened_at,
        }

    # -- main loop -----------------------------------------------------------
    def tick(self) -> None:
        self._handle_flatten()
        self._settle_open()
        self._maybe_enter()

    def run(self) -> None:
        self.log.info("john algro bot starting | mode=%s | symbols=%s | interval=%s | lev=%dx",
                      "LIVE" if self.cfg.is_live else "PAPER", ",".join(self.cfg.symbols),
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
        """a truth alert (not a trade alert): tell the operator when the engine
        is failing on data/feed errors, throttled to once every 10 minutes so an
        outage does not spam. decisions on false/stale data are the road to ruin."""
        now = time.time()
        if now - getattr(self, "_last_feed_alert", 0.0) < 600:
            return
        self._last_feed_alert = now
        try:
            self.notifier.send_embed(
                "⚠️ data / engine error — operator should check",
                [("error", str(err)[:300], False),
                 ("note", "the bot skipped this cycle; it will keep retrying", False)],
                RED, ping=True,
            )
        except Exception:
            pass


def main() -> None:
    Engine(CONFIG).run()


if __name__ == "__main__":
    main()
