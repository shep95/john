"""asherin — the original movement / war / passion read, kept as a third engine.

this is the logic the bot traded before (the old analysis.py), unchanged in its
math, so it can be judged by the same validation gate as shepherd. it now only
trades real money if its own simulated record on that market earns it.

on hyperliquid 1h data it showed small positive samples on ETH and BTC with its
overextension filters on, and lost without them — too few trades to trust yet,
which is exactly what the gate is for.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from .market import Candle


@dataclass
class AsherinParams:
    war_window: int = 7
    atr_lookback: int = 14
    frame_len: int = 50
    normal_len: int = 50
    history_candles: int = 60
    trend_thresh: float = 0.25
    dom_thresh: float = 2.0
    w_mag: float = 0.20
    w_conf: float = 0.35
    w_wick: float = 0.25
    w_pers: float = 0.20
    passion_thresh: float = 1.00
    passion_max: float = 0.99
    norm_vel_max: float = 0.49
    net_force_min: float = 2.00
    net_force_max: float = 2.80
    reject_bouncing: bool = True
    conflict_thresh: float = 0.8
    echo_trigger: float = 1.6
    echo_max: int = 6
    stub_body: float = 0.25
    base_sl: float = 2.0
    base_tp: float = 3.0
    sl_sens: float = 0.60
    tp_sens: float = 1.00
    sl_floor: float = 0.50
    max_hold_bars: int = 48


def _sign(x: float) -> int:
    return 1 if x > 0 else (-1 if x < 0 else 0)


def _atr_series(candles: List[Candle], length: int) -> List[float]:
    trs = []
    for i, c in enumerate(candles):
        if i == 0:
            trs.append(c.rng)
        else:
            p = candles[i - 1]
            trs.append(max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)))
    atrs, rma = [], None
    for i in range(len(candles)):
        if i < length - 1:
            atrs.append(max(sum(trs[: i + 1]) / (i + 1), 1e-12))
        elif i == length - 1:
            rma = sum(trs[:length]) / length
            atrs.append(max(rma, 1e-12))
        else:
            rma = (rma * (length - 1) + trs[i]) / length
            atrs.append(max(rma, 1e-12))
    return atrs


def read(candles: List[Candle], p: AsherinParams) -> Tuple[int, float, float]:
    """(direction, strength, atr) on the latest closed candle; direction 0 = no trade."""
    n = len(candles)
    W = p.war_window
    if n < max(p.frame_len, p.normal_len, p.atr_lookback, W) + 2:
        return 0, 0.0, 0.0
    atrs = _atr_series(candles, p.atr_lookback)
    L = n - 1
    atr = atrs[L]
    c0 = candles[L]
    rngN = c0.rng / atr
    wickN = (c0.upper_wick + c0.lower_wick) / atr
    normVel = ((c0.close - candles[L - W].close) / W) / atr
    trendDir = _sign(normVel)
    netForce = sum(candles[L - k].body / atr for k in range(W))
    netDir = _sign(netForce)
    battles = 0
    for k in range(W - 1):
        s1, s2 = _sign(candles[L - k].body), _sign(candles[L - k - 1].body)
        if s1 != s2 and s1 != 0 and s2 != 0:
            battles += 1
    conflict = battles / float(W - 1) if W > 1 else 0.0
    upVel = sum(max(candles[L - k].close - candles[L - k - 1].close, 0.0) for k in range(W)) / atr
    dnVel = sum(max(candles[L - k - 1].close - candles[L - k].close, 0.0) for k in range(W)) / atr
    hold = sum(1 for k in range(W) if abs(candles[L - k].body) / atr < p.stub_body)
    persistence = hold / float(W)
    passion = (p.w_mag * rngN + p.w_conf * conflict * 3 + p.w_wick * wickN + p.w_pers * persistence * 3)

    echoDir, echoLen, echoMag, lastBig = 0, None, None, None
    for i in range(n):
        rn = candles[i].rng / atrs[i]
        bn = abs(candles[i].body) / atrs[i]
        if rn > p.echo_trigger:
            lastBig = i
        if lastBig is not None:
            resp = i - lastBig
            if 0 < resp <= p.echo_max and bn > 0.05:
                echoDir, echoLen, echoMag = _sign(candles[i].body), resp, rn

    warResolved = abs(normVel) >= p.trend_thresh and abs(netForce) >= p.dom_thresh and trendDir == netDir
    winnerClear = warResolved or abs(netForce) >= p.dom_thresh
    winner = (trendDir if warResolved else netDir) if winnerClear else 0
    if winner == 0 or echoLen is None or echoDir != winner:
        return 0, 0.0, atr

    echoRecency = max(0.0, (p.echo_max - echoLen) / float(p.echo_max))
    echoMagN = min(1.0, echoMag / p.echo_trigger)
    passionRel = min(2.0, passion / p.passion_thresh) if p.passion_thresh > 0 else 0.0
    strength = max(0.0, min(1.0, (passionRel / 2.0 + (echoRecency + echoMagN) / 2.0) / 2.0))

    # overextension filters from the original bot
    if passion >= p.passion_max or abs(normVel) >= p.norm_vel_max:
        return 0, strength, atr
    if abs(netForce) < p.net_force_min or abs(netForce) > p.net_force_max:
        return 0, strength, atr
    if p.reject_bouncing and upVel > p.conflict_thresh and dnVel > p.conflict_thresh:
        return 0, strength, atr
    return winner, strength, atr


def levels(direction: int, close: float, strength: float, atr: float, p: AsherinParams) -> Tuple[float, float]:
    """stop tighter and target wider as strength rises (the original SL/TP rule)."""
    sl_mult = max(p.sl_floor, p.base_sl * (1.0 - p.sl_sens * strength))
    tp_mult = p.base_tp * (1.0 + p.tp_sens * strength)
    return close - direction * sl_mult * atr, close + direction * tp_mult * atr
