"""execution layer.

two brokers behind one interface:
  - PaperBroker: simulates fills against the live 5m candles (default, safe).
  - LiveBroker : real orders on hyperliquid with 5x isolated leverage and
                 on-exchange reduce-only TP/SL trigger orders.

both expose: equity(), has_position(), open(plan), settle(symbol, candle).
`settle` returns a ClosedTrade when the position resolves (TP/SL), else None.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from .logutil import get_logger
from .market import Candle
from .strategy import TradePlan

log = get_logger()

TAKER_FEE = 0.00045  # ~hyperliquid taker, used only for paper pnl realism


@dataclass
class Position:
    symbol: str
    side: str
    is_buy: bool
    size: float
    entry: float
    stop_loss: float
    take_profit: float
    opened_at: float  # epoch seconds


@dataclass
class ClosedTrade:
    symbol: str
    side: str
    entry: float
    exit: float
    size: float
    pnl: float
    reason: str        # TP / SL / FLAT
    opened_at: float
    closed_at: float

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.closed_at - self.opened_at)


# ---------------------------------------------------------------------------
class PaperBroker:
    def __init__(self, cfg):
        self.cfg = cfg
        self._equity = cfg.paper_equity
        self.pos: Optional[Position] = None

    def equity(self) -> float:
        return self._equity

    def has_position(self, symbol: str) -> bool:
        return self.pos is not None and self.pos.symbol == symbol

    def any_position(self) -> Optional[Position]:
        return self.pos

    def open(self, plan: TradePlan) -> bool:
        self.pos = Position(
            symbol=plan.symbol, side=plan.side, is_buy=plan.is_buy, size=plan.size,
            entry=plan.entry_ref, stop_loss=plan.stop_loss, take_profit=plan.take_profit,
            opened_at=time.time(),
        )
        self._equity -= plan.notional * TAKER_FEE
        log.info(
            "[paper] OPEN %s %s size=%s entry=%.6g sl=%.6g tp=%.6g rr=%.2f conv=%.2f",
            plan.symbol, plan.side, plan.size, plan.entry_ref, plan.stop_loss,
            plan.take_profit, plan.rr, plan.conviction,
        )
        return True

    def settle(self, symbol: str, candle: Optional[Candle]) -> Optional[ClosedTrade]:
        if self.pos is None or self.pos.symbol != symbol or candle is None:
            return None
        p = self.pos
        hit_reason = None
        exit_px = None
        if p.is_buy:
            # conservative: if both touched in one candle, assume SL first
            if candle.low <= p.stop_loss:
                hit_reason, exit_px = "SL", p.stop_loss
            elif candle.high >= p.take_profit:
                hit_reason, exit_px = "TP", p.take_profit
        else:
            if candle.high >= p.stop_loss:
                hit_reason, exit_px = "SL", p.stop_loss
            elif candle.low <= p.take_profit:
                hit_reason, exit_px = "TP", p.take_profit
        if hit_reason is None:
            return None

        direction = 1 if p.is_buy else -1
        pnl = (exit_px - p.entry) * p.size * direction
        pnl -= p.size * exit_px * TAKER_FEE  # exit fee
        self._equity += pnl
        closed = ClosedTrade(
            symbol=p.symbol, side=p.side, entry=p.entry, exit=exit_px, size=p.size,
            pnl=pnl, reason=hit_reason, opened_at=p.opened_at, closed_at=time.time(),
        )
        log.info("[paper] CLOSE %s %s pnl=%.4f equity=%.2f dur=%.0fs",
                 p.symbol, hit_reason, pnl, self._equity, closed.duration_sec)
        self.pos = None
        return closed

    def force_close(self, symbol: str, price: float) -> Optional[ClosedTrade]:
        if self.pos is None or self.pos.symbol != symbol:
            return None
        p = self.pos
        direction = 1 if p.is_buy else -1
        pnl = (price - p.entry) * p.size * direction - p.size * price * TAKER_FEE
        self._equity += pnl
        closed = ClosedTrade(p.symbol, p.side, p.entry, price, p.size, pnl, "FLAT",
                             p.opened_at, time.time())
        log.info("[paper] FLATTEN %s pnl=%.4f", symbol, pnl)
        self.pos = None
        return closed


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
        self._open_meta: dict = {}  # symbol -> (opened_at, plan)
        log.info("[live] hyperliquid ready acct=%s...%s testnet=%s",
                 self.address[:6], self.address[-4:], cfg.testnet)

    def equity(self) -> float:
        try:
            st = self.info.user_state(self.address)
            return float(st["marginSummary"]["accountValue"])
        except Exception as e:
            log.warning("[live] equity read failed: %s", e)
            return self.cfg.paper_equity

    def _raw_position(self, symbol: str):
        st = self.info.user_state(self.address)
        for ap in st.get("assetPositions", []):
            pos = ap.get("position", {})
            if pos.get("coin", "").upper() == symbol.upper():
                szi = float(pos.get("szi", 0.0))
                if abs(szi) > 0:
                    return pos, szi
        return None, 0.0

    def has_position(self, symbol: str) -> bool:
        try:
            _, szi = self._raw_position(symbol)
            return abs(szi) > 0
        except Exception:
            return symbol in self._open_meta

    def any_position(self) -> Optional[Position]:
        for sym in self._open_meta:
            if self.has_position(sym):
                _, plan = self._open_meta[sym]
                return Position(sym, plan.side, plan.is_buy, plan.size, plan.entry_ref,
                                plan.stop_loss, plan.take_profit, self._open_meta[sym][0])
        return None

    def _set_leverage(self, symbol: str):
        if symbol in self._leverage_set:
            return
        try:
            self.exchange.update_leverage(self.cfg.leverage, symbol, is_cross=self.cfg.cross_margin)
            self._leverage_set.add(symbol)
        except Exception as e:
            log.warning("[live] update_leverage %s failed: %s", symbol, e)

    def open(self, plan: TradePlan) -> bool:
        self._set_leverage(plan.symbol)
        try:
            res = self.exchange.market_open(
                plan.symbol, plan.is_buy, plan.size, None, self.cfg.slippage
            )
            log.info("[live] market_open %s: %s", plan.symbol, res)
            ok = res.get("status") == "ok"
            if not ok:
                log.error("[live] open rejected: %s", res)
                return False
        except Exception as e:
            log.error("[live] open error: %s", e)
            return False

        # on-exchange reduce-only TP/SL trigger orders (auto-managed by hyperliquid)
        for tpsl, trig in (("tp", plan.take_profit), ("sl", plan.stop_loss)):
            try:
                ot = {"trigger": {"triggerPx": trig, "isMarket": True, "tpsl": tpsl}}
                r = self.exchange.order(plan.symbol, not plan.is_buy, plan.size, trig, ot, reduce_only=True)
                log.info("[live] %s trigger @ %.6g: %s", tpsl.upper(), trig, r.get("status"))
            except Exception as e:
                log.error("[live] %s trigger error: %s", tpsl, e)

        self._open_meta[plan.symbol] = (time.time(), plan)
        return True

    def settle(self, symbol: str, candle: Optional[Candle]) -> Optional[ClosedTrade]:
        if symbol not in self._open_meta:
            return None
        try:
            _, szi = self._raw_position(symbol)
        except Exception:
            return None
        if abs(szi) > 0:
            return None  # still open, exchange manages TP/SL

        opened_at, plan = self._open_meta.pop(symbol)
        exit_px = self.market.last_price(symbol, self.cfg.interval)
        # infer reason by which level is closer to the exit
        reason = "TP" if abs(exit_px - plan.take_profit) <= abs(exit_px - plan.stop_loss) else "SL"
        direction = 1 if plan.is_buy else -1
        pnl = (exit_px - plan.entry_ref) * plan.size * direction
        closed = ClosedTrade(symbol, plan.side, plan.entry_ref, exit_px, plan.size, pnl,
                             reason, opened_at, time.time())
        log.info("[live] CLOSE %s %s exit=%.6g ~pnl=%.4f dur=%.0fs",
                 symbol, reason, exit_px, pnl, closed.duration_sec)
        self._cancel_triggers(symbol)
        return closed

    def _cancel_triggers(self, symbol: str) -> None:
        try:
            open_orders = self.info.open_orders(self.address)
            for o in open_orders:
                if o.get("coin", "").upper() == symbol.upper():
                    self.exchange.cancel(symbol, o["oid"])
        except Exception:
            pass

    def force_close(self, symbol: str, price: float) -> Optional[ClosedTrade]:
        if symbol not in self._open_meta:
            return None
        try:
            self.exchange.market_close(symbol)
        except Exception as e:
            log.error("[live] force_close error: %s", e)
            return None
        opened_at, plan = self._open_meta.pop(symbol)
        exit_px = self.market.last_price(symbol, self.cfg.interval)
        direction = 1 if plan.is_buy else -1
        pnl = (exit_px - plan.entry_ref) * plan.size * direction
        self._cancel_triggers(symbol)
        return ClosedTrade(symbol, plan.side, plan.entry_ref, exit_px, plan.size, pnl,
                           "FLAT", opened_at, time.time())


def make_broker(cfg, market):
    if cfg.is_live:
        return LiveBroker(cfg, market)
    return PaperBroker(cfg)
