"""strategy: MarketRead -> a concrete, sized trade plan with SL/TP.

SL/TP rule (john's closing note):
  "stop loss and take profit ... determined by the same metrics as short or
   long, but with passion point being the guiding principle. higher passion
   point and more explicit echo = ... lower SL and higher TP."

so conviction (passion + echo clarity + dominance) does two things:
  - shrinks the stop  (tighter SL, we trust the direction)
  - stretches the target (higher TP, riding the inertia of a resolved war)
=> conviction widens reward:risk. everything is scaled by atr (the environment's
volatility) and nudged by elongation (the pattern's own normal rate).
"""
from __future__ import annotations

from dataclasses import dataclass

from .analysis import LONG, MarketRead, NO_TRADE, SHORT, _clip
from .logutil import get_logger

log = get_logger()


def round_px(px: float, sz_decimals: int) -> float:
    """hyperliquid price rules: <=5 significant figures and <= (6 - szDecimals)
    decimal places for perps. integer prices are always allowed."""
    if px <= 0:
        return px
    px = float(f"{px:.5g}")
    decimals = max(0, 6 - sz_decimals)
    return round(px, decimals)


@dataclass
class TradePlan:
    symbol: str
    side: str                 # LONG / SHORT
    is_buy: bool
    entry_ref: float
    stop_loss: float
    take_profit: float
    size: float               # coin units
    notional: float
    conviction: float
    sl_dist: float
    tp_dist: float
    rr: float                 # reward:risk
    reason: str


def build_plan(read: MarketRead, equity: float, sz_decimals: int, cfg) -> "TradePlan | None":
    if read.signal == NO_TRADE:
        return None

    atr_v = read.atr
    c = read.conviction  # = strength, matching the indicator

    # SL/TP in atr units, shaped by strength -- identical to asherin.pine:
    #   slMult = max(slFloor, baseSL * (1 - slSens * strength))   (tighter when strong)
    #   tpMult = baseTP * (1 + tpSens * strength)                 (wider when strong)
    sl_atr = max(cfg.sl_floor, cfg.base_sl * (1.0 - cfg.sl_sens * c))
    tp_atr = cfg.base_tp * (1.0 + cfg.tp_sens * c)

    sl_dist = sl_atr * atr_v
    tp_dist = tp_atr * atr_v

    entry = read.price
    if read.signal == LONG:
        is_buy = True
        sl = entry - sl_dist
        tp = entry + tp_dist
    else:  # SHORT
        is_buy = False
        sl = entry + sl_dist
        tp = entry - tp_dist

    sl = round_px(sl, sz_decimals)
    tp = round_px(tp, sz_decimals)
    entry_r = round_px(entry, sz_decimals)

    # sizing: risk RISK_FRAC of equity if the stop is hit.
    risk_amount = equity * cfg.risk_frac
    stop_dist_price = abs(entry - sl)
    if stop_dist_price <= 0:
        log.warning("[sizing] rejected %s: stop distance is 0 (entry=%.6g sl=%.6g)",
                    read.symbol, entry, sl)
        return None
    size = risk_amount / stop_dist_price

    # cap notional at leverage * max_position_frac of equity
    max_notional = equity * cfg.leverage * cfg.max_position_frac
    notional = size * entry
    if notional > max_notional:
        size = max_notional / entry
        notional = size * entry

    size = round(size, sz_decimals)
    if size <= 0:
        log.warning(
            "[sizing] rejected %s: size rounds to 0. equity=%.4f risk_amt=%.4f "
            "entry=%.6g stop_dist=%.6g raw_size=%.8f sz_decimals=%d -> fund the "
            "account or lower risk; equity here is the sizing base.",
            read.symbol, equity, risk_amount, entry, stop_dist_price,
            risk_amount / stop_dist_price, sz_decimals,
        )
        return None
    notional = size * entry

    rr = tp_dist / sl_dist if sl_dist > 0 else 0.0

    return TradePlan(
        symbol=read.symbol, side=read.signal, is_buy=is_buy, entry_ref=entry_r,
        stop_loss=sl, take_profit=tp, size=size, notional=notional,
        conviction=c, sl_dist=sl_dist, tp_dist=tp_dist, rr=rr, reason=read.reason,
    )
