"""turn an engine event into a sized order plan.

the engine decides direction, stop and target in price terms. this module only
decides how much: risk RISK_FRAC of the sizing base between entry and stop,
capped so notional never exceeds leverage × MAX_POSITION_FRAC of the base.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .logutil import get_logger

log = get_logger()

HL_MIN_NOTIONAL = 10.0  # hyperliquid rejects orders below ~$10


def round_px(px: float, sz_decimals: int) -> float:
    """hyperliquid price rules: <=5 significant figures and <= (6 - szDecimals)
    decimal places for perps. integer prices are always allowed."""
    if px <= 0:
        return px
    px = float(f"{px:.5g}")
    return round(px, max(0, 6 - sz_decimals))


@dataclass
class TradePlan:
    symbol: str
    side: str                 # LONG / SHORT
    is_buy: bool
    entry_ref: float          # expected fill (market) or the limit price
    stop_loss: float
    take_profit: float
    size: float               # coin units
    notional: float
    risk_usd: float
    rr: float
    kind: str = "market"      # market | limit
    reason: str = ""


def build_plan(symbol: str, direction: int, entry: float, stop: float, target: float,
               base: float, sz_decimals: int, cfg, kind: str = "market", reason: str = "") -> Optional[TradePlan]:
    is_buy = direction == 1
    sl = round_px(stop, sz_decimals)
    tp = round_px(target, sz_decimals)
    entry_r = round_px(entry, sz_decimals)
    stop_dist = abs(entry_r - sl)
    if stop_dist <= 0 or (is_buy and not sl < entry_r < tp) or (not is_buy and not tp < entry_r < sl):
        log.warning("[sizing] %s rejected: levels out of order entry=%.6g sl=%.6g tp=%.6g",
                    symbol, entry_r, sl, tp)
        return None

    risk_amount = base * cfg.risk_frac
    size = risk_amount / stop_dist
    max_notional = base * cfg.leverage * cfg.max_position_frac
    if size * entry_r > max_notional:
        size = max_notional / entry_r
    size = round(size, sz_decimals)
    notional = size * entry_r
    if size <= 0 or notional < HL_MIN_NOTIONAL:
        log.warning("[sizing] %s rejected: notional %.2f below the $%.0f exchange minimum "
                    "(sizing base %.2f). fund the account or raise MAX_POSITION_FRAC.",
                    symbol, notional, HL_MIN_NOTIONAL, base)
        return None
    return TradePlan(
        symbol=symbol, side="LONG" if is_buy else "SHORT", is_buy=is_buy, entry_ref=entry_r,
        stop_loss=sl, take_profit=tp, size=size, notional=notional,
        risk_usd=size * stop_dist, rr=abs(tp - entry_r) / stop_dist, kind=kind, reason=reason,
    )
