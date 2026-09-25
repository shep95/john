"""execution layer.

two brokers behind one interface:
  - PaperBroker: fills against closed candles with the same rules as the
                 simulated engines (stop first when a candle touches both).
  - LiveBroker : real orders on hyperliquid with on-exchange reduce-only TP/SL.

interface: equity() · any_position() · pending() · open_market(plan, price)
           place_limit(plan) · cancel_pending(symbol) · on_candle(symbol, candle)
           poll() · force_close(symbol, price) · restore_position(d)
on_candle / poll return a list of ClosedTrade for positions that resolved.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional

from .logutil import get_logger
from .market import Candle
from .sizing import TradePlan

log = get_logger()

TAKER_FEE = 0.00045   # hyperliquid base taker fee
MAKER_FEE = 0.00015   # hyperliquid base maker fee (resting limit fills)


@dataclass
class Position:
    symbol: str
    side: str
    is_buy: bool
    size: float
    entry: float
    stop_loss: float
    take_profit: float
    opened_at: float
    risk_usd: float = 0.0


@dataclass
class ClosedTrade:
    symbol: str
    side: str
    entry: float
    exit: float
    size: float
    pnl: float
    reason: str        # TP / SL / TIME / FLAT
    opened_at: float
    closed_at: float
    risk_usd: float = 0.0

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.closed_at - self.opened_at)

    @property
    def r(self) -> float:
        return self.pnl / self.risk_usd if self.risk_usd > 0 else 0.0


def position_dict(p: Position) -> dict:
    return {"symbol": p.symbol, "side": p.side, "is_buy": p.is_buy, "size": p.size,
            "entry": p.entry, "stop_loss": p.stop_loss, "take_profit": p.take_profit,
            "opened_at": p.opened_at, "risk_usd": p.risk_usd}


def position_from_dict(d: dict) -> Position:
    return Position(d["symbol"], d["side"], bool(d["is_buy"]), float(d["size"]), float(d["entry"]),
                    float(d["stop_loss"]), float(d["take_profit"]), float(d["opened_at"]),
                    float(d.get("risk_usd", 0.0)))


# ---------------------------------------------------------------------------
class PaperBroker:
    def __init__(self, cfg):
        self.cfg = cfg
        self._equity = cfg.paper_equity
        self.pos: Optional[Position] = None
        self.limit: Optional[TradePlan] = None

    def equity(self) -> float:
        return self._equity

    def any_position(self) -> Optional[Position]:
        return self.pos

    def pending(self) -> Optional[TradePlan]:
        return self.limit

    def open_market(self, plan: TradePlan, price: float) -> bool:
        self.pos = Position(plan.symbol, plan.side, plan.is_buy, plan.size, price,
                            plan.stop_loss, plan.take_profit, time.time(), plan.risk_usd)
        self._equity -= plan.size * price * TAKER_FEE
        log.info("[paper] OPEN %s %s size=%s entry=%.6g sl=%.6g tp=%.6g rr=%.2f",
                 plan.symbol, plan.side, plan.size, price, plan.stop_loss, plan.take_profit, plan.rr)
        return True

    def place_limit(self, plan: TradePlan) -> bool:
        self.limit = plan
        log.info("[paper] LIMIT %s %s size=%s @ %.6g sl=%.6g tp=%.6g",
                 plan.symbol, plan.side, plan.size, plan.entry_ref, plan.stop_loss, plan.take_profit)
        return True

    def cancel_pending(self, symbol: str) -> None:
        if self.limit is not None and self.limit.symbol == symbol:
            log.info("[paper] cancel limit %s", symbol)
            self.limit = None

    def _close(self, px: float, reason: str) -> ClosedTrade:
        p = self.pos
        d = 1 if p.is_buy else -1
        pnl = (px - p.entry) * p.size * d - p.size * px * TAKER_FEE
        self._equity += pnl
        closed = ClosedTrade(p.symbol, p.side, p.entry, px, p.size, pnl, reason,
                             p.opened_at, time.time(), p.risk_usd)
        log.info("[paper] CLOSE %s %s pnl=%.4f (%.2fR) equity=%.2f", p.symbol, reason, pnl, closed.r, self._equity)
        self.pos = None
        return closed

    def on_candle(self, symbol: str, c: Candle) -> List[ClosedTrade]:
        out: List[ClosedTrade] = []
        lim = self.limit
        if lim is not None and lim.symbol == symbol and self.pos is None:
            touched = c.low <= lim.entry_ref if lim.is_buy else c.high >= lim.entry_ref
            past_sl = c.open <= lim.stop_loss if lim.is_buy else c.open >= lim.stop_loss
            if past_sl:
                self.limit = None
            elif touched:
                gapped = c.open <= lim.entry_ref if lim.is_buy else c.open >= lim.entry_ref
                px = c.open if gapped else lim.entry_ref
                self.pos = Position(lim.symbol, lim.side, lim.is_buy, lim.size, px,
                                    lim.stop_loss, lim.take_profit, time.time(), lim.risk_usd)
                self._equity -= lim.size * px * MAKER_FEE
                self.limit = None
                log.info("[paper] limit filled %s @ %.6g", symbol, px)
                # filled and stopped inside one candle: bars can't show the order, the stop counts
                if (c.low <= self.pos.stop_loss) if self.pos.is_buy else (c.high >= self.pos.stop_loss):
                    out.append(self._close(self.pos.stop_loss, "SL"))
                return out
        p = self.pos
        if p is None or p.symbol != symbol:
            return out
        if p.is_buy:
            if c.open <= p.stop_loss or c.open >= p.take_profit:
                out.append(self._close(c.open, "SL" if c.open <= p.stop_loss else "TP"))
            elif c.low <= p.stop_loss:
                out.append(self._close(p.stop_loss, "SL"))
            elif c.high >= p.take_profit:
                out.append(self._close(p.take_profit, "TP"))
        else:
            if c.open >= p.stop_loss or c.open <= p.take_profit:
                out.append(self._close(c.open, "SL" if c.open >= p.stop_loss else "TP"))
            elif c.high >= p.stop_loss:
                out.append(self._close(p.stop_loss, "SL"))
            elif c.low <= p.take_profit:
                out.append(self._close(p.take_profit, "TP"))
        return out

    def poll(self) -> List[ClosedTrade]:
        return []

    def force_close(self, symbol: str, price: float, reason: str = "FLAT") -> Optional[ClosedTrade]:
        if self.pos is None or self.pos.symbol != symbol:
            return None
        return self._close(price, reason)

    def restore_position(self, d: dict) -> None:
        self.pos = position_from_dict(d)
        log.info("[paper] restored open %s %s from state", d["symbol"], d["side"])


# ---------------------------------------------------------------------------
class LiveBroker:
    def __init__(self, cfg, market):
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from hyperliquid.info import Info
        from hyperliquid.utils import constants

        base = constants.TESTNET_API_URL if cfg.testnet else constants.MAINNET_API_URL
        self.cfg = cfg
        self.market = market
        self.wallet = Account.from_key(cfg.private_key)
        self.address = cfg.account_address or self.wallet.address
        self.info = Info(base, skip_ws=True)
        self.exchange = Exchange(self.wallet, base, account_address=self.address)
        self._leverage_set = set()
        self.pos: Optional[Position] = None
        self.limit: Optional[TradePlan] = None
        self.limit_oid: Optional[int] = None
        log.info("[live] hyperliquid ready acct=%s...%s testnet=%s",
                 self.address[:6], self.address[-4:], cfg.testnet)

    # -- account -------------------------------------------------------------
    def equity(self) -> float:
        """usable account value in USDC. unified accounts report 0 under
        marginSummary, so fall back through crossMarginSummary, withdrawable and
        finally the spot USDC balance. never sizes off a guessed balance."""
        try:
            st = self.info.user_state(self.address)
        except Exception as e:
            log.warning("[live] equity read failed: %s", e)
            return 0.0
        ms = st.get("marginSummary", {}) or {}
        cms = st.get("crossMarginSummary", {}) or {}
        for v in (ms.get("accountValue"), cms.get("accountValue"), st.get("withdrawable")):
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if fv > 0:
                return fv
        try:
            for b in self.info.spot_user_state(self.address).get("balances", []):
                if str(b.get("coin", "")).upper() == "USDC":
                    fv = float(b.get("total", 0.0) or 0.0)
                    if fv > 0:
                        return fv
        except Exception as e:
            log.warning("[live] spot_user_state read failed: %s", e)
        log.warning("[live] equity resolved to 0 (share USDC to the perps/unified account)")
        return 0.0

    def _raw_position(self, symbol: str):
        st = self.info.user_state(self.address)
        for ap in st.get("assetPositions", []):
            pos = ap.get("position", {})
            if pos.get("coin", "").upper() == symbol.upper():
                szi = float(pos.get("szi", 0.0))
                if abs(szi) > 0:
                    return pos, szi
        return None, 0.0

    def any_position(self) -> Optional[Position]:
        return self.pos

    def pending(self) -> Optional[TradePlan]:
        return self.limit

    def _set_leverage(self, symbol: str) -> None:
        if symbol in self._leverage_set:
            return
        try:
            self.exchange.update_leverage(self.cfg.leverage, symbol, is_cross=self.cfg.cross_margin)
            self._leverage_set.add(symbol)
        except Exception as e:
            log.warning("[live] update_leverage %s failed: %s", symbol, e)

    @staticmethod
    def _statuses(res) -> list:
        try:
            return res["response"]["data"]["statuses"]
        except Exception:
            return []

    def _ok(self, res) -> bool:
        if not isinstance(res, dict) or res.get("status") != "ok":
            return False
        return not any(isinstance(s, dict) and s.get("error") for s in self._statuses(res))

    def _place_triggers(self, symbol: str, is_buy: bool, size: float, sl: float, tp: float) -> bool:
        ok = True
        for tpsl, trig in (("sl", sl), ("tp", tp)):
            try:
                ot = {"trigger": {"triggerPx": trig, "isMarket": True, "tpsl": tpsl}}
                r = self.exchange.order(symbol, not is_buy, size, trig, ot, reduce_only=True)
                if not self._ok(r):
                    ok = False
                    log.error("[live] %s trigger rejected: %s", tpsl.upper(), r)
            except Exception as e:
                ok = False
                log.error("[live] %s trigger error: %s", tpsl, e)
        return ok

    def _emergency_close(self, symbol: str) -> None:
        log.error("[live] protection failed, closing %s", symbol)
        try:
            self.exchange.market_close(symbol)
        except Exception as e:
            log.error("[live] emergency close failed: %s", e)
        self._cancel_orders(symbol)

    # -- entries -------------------------------------------------------------
    def open_market(self, plan: TradePlan, price: float) -> bool:
        self._set_leverage(plan.symbol)
        try:
            res = self.exchange.market_open(plan.symbol, plan.is_buy, plan.size, None, self.cfg.slippage)
        except Exception as e:
            log.error("[live] open error: %s", e)
            return False
        if not self._ok(res):
            log.error("[live] open rejected: %s", res)
            return False
        fill_px, filled = price, plan.size
        for s in self._statuses(res):
            f = s.get("filled") if isinstance(s, dict) else None
            if f:
                fill_px = float(f.get("avgPx", price))
                filled = float(f.get("totalSz", plan.size))
        if not self._place_triggers(plan.symbol, plan.is_buy, filled, plan.stop_loss, plan.take_profit):
            # an unprotected position is not acceptable
            self._emergency_close(plan.symbol)
            return False
        self.pos = Position(plan.symbol, plan.side, plan.is_buy, filled, fill_px, plan.stop_loss,
                            plan.take_profit, time.time(), filled * abs(fill_px - plan.stop_loss))
        log.info("[live] OPEN %s %s size=%s @ %.6g sl=%.6g tp=%.6g", plan.symbol, plan.side,
                 filled, fill_px, plan.stop_loss, plan.take_profit)
        return True

    def place_limit(self, plan: TradePlan) -> bool:
        self._set_leverage(plan.symbol)
        try:
            res = self.exchange.order(plan.symbol, plan.is_buy, plan.size, plan.entry_ref,
                                      {"limit": {"tif": "Gtc"}}, reduce_only=False)
        except Exception as e:
            log.error("[live] limit error: %s", e)
            return False
        if not self._ok(res):
            log.error("[live] limit rejected: %s", res)
            return False
        self.limit, self.limit_oid = plan, None
        for s in self._statuses(res):
            if isinstance(s, dict) and s.get("resting"):
                self.limit_oid = s["resting"].get("oid")
            elif isinstance(s, dict) and s.get("filled"):
                # the limit crossed and filled immediately
                self._adopt_fill(float(s["filled"].get("avgPx", plan.entry_ref)),
                                 float(s["filled"].get("totalSz", plan.size)))
                return True
        log.info("[live] LIMIT %s %s size=%s @ %.6g oid=%s", plan.symbol, plan.side, plan.size,
                 plan.entry_ref, self.limit_oid)
        return True

    def cancel_pending(self, symbol: str) -> None:
        if self.limit is None or self.limit.symbol != symbol:
            return
        if self.limit_oid is not None:
            try:
                self.exchange.cancel(symbol, self.limit_oid)
            except Exception as e:
                log.warning("[live] cancel %s failed: %s", symbol, e)
        # a partial fill may already be a position: protect it before forgetting the order
        self._check_limit_filled()
        self.limit, self.limit_oid = None, None

    def _adopt_fill(self, px: float, size: float) -> None:
        plan = self.limit
        self.limit, self.limit_oid = None, None
        if not self._place_triggers(plan.symbol, plan.is_buy, size, plan.stop_loss, plan.take_profit):
            self._emergency_close(plan.symbol)
            return
        self.pos = Position(plan.symbol, plan.side, plan.is_buy, size, px, plan.stop_loss,
                            plan.take_profit, time.time(), size * abs(px - plan.stop_loss))
        log.info("[live] limit filled %s @ %.6g size=%s — protection placed", plan.symbol, px, size)

    def _check_limit_filled(self) -> None:
        plan = self.limit
        if plan is None or self.pos is not None:
            return
        try:
            pos, szi = self._raw_position(plan.symbol)
        except Exception:
            return
        if abs(szi) > 0:
            entry = float(pos.get("entryPx", plan.entry_ref) or plan.entry_ref)
            # partial fill: cancel the remainder and keep (and protect) what filled
            if self.limit_oid is not None:
                try:
                    self.exchange.cancel(plan.symbol, self.limit_oid)
                except Exception:
                    pass
            self._adopt_fill(entry, abs(szi))

    # -- lifecycle -----------------------------------------------------------
    def on_candle(self, symbol: str, c: Candle) -> List[ClosedTrade]:
        return []  # the exchange manages stops; poll() detects closure

    def poll(self) -> List[ClosedTrade]:
        self._check_limit_filled()
        p = self.pos
        if p is None:
            return []
        try:
            _, szi = self._raw_position(p.symbol)
        except Exception:
            return []
        if abs(szi) > 0:
            return []
        exit_px, pnl, closed_at = self._closing_fill_summary(p)
        reason = "TP" if abs(exit_px - p.take_profit) <= abs(exit_px - p.stop_loss) else "SL"
        self._cancel_orders(p.symbol)
        self.pos = None
        log.info("[live] CLOSE %s %s exit=%.6g pnl=%.4f", p.symbol, reason, exit_px, pnl)
        return [ClosedTrade(p.symbol, p.side, p.entry, exit_px, p.size, pnl, reason,
                            p.opened_at, closed_at, p.risk_usd)]

    def _closing_fill_summary(self, p: Position):
        opened_ms = int(p.opened_at * 1000)
        exit_px, realized, last_ms, got = None, 0.0, None, False
        try:
            for f in self.info.user_fills(self.address):
                if f.get("coin", "").upper() != p.symbol.upper() or int(f.get("time", 0)) < opened_ms:
                    continue
                cpnl = float(f.get("closedPnl", 0.0) or 0.0)
                if cpnl == 0.0:
                    continue
                realized += cpnl - float(f.get("fee", 0.0) or 0.0)
                t_ms = int(f.get("time", 0))
                if last_ms is None or t_ms >= last_ms:
                    last_ms, exit_px = t_ms, float(f.get("px", 0.0) or 0.0)
                got = True
        except Exception as e:
            log.warning("[live] user_fills read failed, approximating exit: %s", e)
        if not got or not exit_px:
            exit_px = self.market.last_price(p.symbol, self.cfg.interval)
            d = 1 if p.is_buy else -1
            return exit_px, (exit_px - p.entry) * p.size * d, time.time()
        return exit_px, realized, (last_ms / 1000.0 if last_ms else time.time())

    def _cancel_orders(self, symbol: str) -> None:
        try:
            for o in self.info.open_orders(self.address):
                if o.get("coin", "").upper() == symbol.upper():
                    self.exchange.cancel(symbol, o["oid"])
        except Exception as e:
            log.warning("[live] cancel orders %s failed: %s", symbol, e)

    def force_close(self, symbol: str, price: float, reason: str = "FLAT") -> Optional[ClosedTrade]:
        p = self.pos
        if p is None or p.symbol != symbol:
            return None
        try:
            res = self.exchange.market_close(symbol)
        except Exception as e:
            log.error("[live] force_close error: %s", e)
            return None
        if not self._ok(res):
            log.error("[live] force_close rejected: %s", res)
            return None
        self._cancel_orders(symbol)
        exit_px, pnl, closed_at = self._closing_fill_summary(p)
        self.pos = None
        return ClosedTrade(p.symbol, p.side, p.entry, exit_px, p.size, pnl, reason,
                           p.opened_at, closed_at, p.risk_usd)

    # -- restart recovery ----------------------------------------------------
    def restore_position(self, d: dict) -> None:
        self.pos = position_from_dict(d)
        self._leverage_set.add(self.pos.symbol)
        log.info("[live] restored open %s %s from state", d["symbol"], d["side"])

    def _trigger_levels(self, symbol: str, is_buy: bool, entry: float):
        sl = tp = entry
        try:
            for o in self.info.open_orders(self.address):
                if o.get("coin", "").upper() != symbol.upper():
                    continue
                raw = o.get("triggerPx") or (o.get("trigger") or {}).get("triggerPx")
                if raw is None:
                    continue
                trig = float(raw)
                if (trig >= entry) == is_buy:
                    tp = trig
                else:
                    sl = trig
        except Exception:
            pass
        return sl, tp

    def sync_from_exchange(self) -> None:
        """reconcile with the exchange after a restart: drop a persisted position
        that is gone, or adopt one the bot is not tracking."""
        if self.pos is not None:
            try:
                _, szi = self._raw_position(self.pos.symbol)
                if abs(szi) == 0:
                    log.warning("[live] persisted %s position is gone on the exchange", self.pos.symbol)
                    self.pos = None
            except Exception:
                pass
            return
        for sym in self.cfg.symbols:
            try:
                pos, szi = self._raw_position(sym)
            except Exception:
                continue
            if abs(szi) <= 0:
                continue
            entry = float(pos.get("entryPx", 0.0) or 0.0)
            is_buy = szi > 0
            sl, tp = self._trigger_levels(sym, is_buy, entry)
            if sl == entry:
                log.error("[live] adopted %s position has NO stop on the exchange — protect or flatten it", sym)
            self.pos = Position(sym, "LONG" if is_buy else "SHORT", is_buy, abs(szi), entry, sl, tp,
                                time.time(), abs(szi) * abs(entry - sl))
            log.warning("[live] adopted untracked %s position (size=%.6g entry=%.6g sl=%.6g tp=%.6g)",
                        sym, abs(szi), entry, sl, tp)
            return
        # flat on every symbol: a resting entry left over from before the restart
        # could fill with no protection attached, so clear the book.
        for sym in self.cfg.symbols:
            self._cancel_orders(sym)


def make_broker(cfg, market):
    if cfg.is_live:
        return LiveBroker(cfg, market)
    return PaperBroker(cfg)
