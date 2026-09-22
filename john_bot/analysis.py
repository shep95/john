"""analysis engine -- a faithful 1:1 port of the `asherin.pine` TradingView
indicator so the live bot fires on the SAME conditions the chart shows.

every threshold, weight and formula below mirrors asherin.pine (same defaults):
  - ATR = Wilder RMA (ta.atr), atrLen=14
  - netForce = sum(body/atr, warWin), dominance gate = |netForce| >= domThresh(2.0)
  - normVel = (close - close[warWin]) / warWin / atr, trend gate = |normVel| >= trendThresh(0.25)
  - warResolved = |normVel|>=trendThresh AND |netForce|>=domThresh AND trendDir==netDir
  - winner + echo direction (echoDir) must agree -> LONG/SHORT, else wait
  - passion = wMag*rngN + wConf*(conflictDensity*3) + wWick*wickN + wPers*(persistence*3)
  - strength = clip((passionRel/2 + echoStrength)/2, 0, 1)  -> drives SL/TP in strategy.py

the MarketRead fields are kept for the engine / notify / backtest consumers, but
their meaning now follows the indicator (dominance holds netForce, etc.).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

from .market import Candle

LONG = "LONG"
SHORT = "SHORT"
NO_TRADE = "NO_TRADE"


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _sign(x: float) -> int:
    return 1 if x > 0 else (-1 if x < 0 else 0)


# ---------------------------------------------------------------------------
# ATR series -- Wilder RMA of true range, matching pine's ta.atr(atrLen)
# ---------------------------------------------------------------------------
def _atr_series(candles: List[Candle], length: int) -> List[float]:
    n = len(candles)
    trs: List[float] = []
    for i in range(n):
        if i == 0:
            trs.append(candles[0].rng)
        else:
            c, p = candles[i], candles[i - 1]
            trs.append(max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)))
    atrs: List[float] = []
    rma: Optional[float] = None
    for i in range(n):
        if i < length - 1:
            rma_run = sum(trs[: i + 1]) / (i + 1)  # running mean until warmed
            atrs.append(max(rma_run, 1e-9))
        elif i == length - 1:
            rma = sum(trs[:length]) / length
            atrs.append(max(rma, 1e-9))
        else:
            rma = (rma * (length - 1) + trs[i]) / length  # type: ignore[operator]
            atrs.append(max(rma, 1e-9))
    return atrs


# ---------------------------------------------------------------------------
# echo -- same construct as the indicator (var-state replayed over the series)
# ---------------------------------------------------------------------------
@dataclass
class Echo:
    direction: int = 0
    length: int = 0
    latency: int = 0
    magnitude: float = 0.0
    micro_count: int = 0
    reinforces_winner: bool = False


@dataclass
class MarketRead:
    symbol: str
    price: float
    atr: float
    normal_body: float
    buyer_force: float
    seller_force: float
    dominance: float            # NOTE: holds netForce = sum(body/atr, warWin)
    winner: int
    war_resolved: bool
    velocity_established: bool
    conflict: float             # conflictDensity [0,1]
    total_energy: float
    passion: float              # indicator passion (can exceed 1)
    passion_idx: int
    elongation: float
    double_taps: int
    persistence: float
    echo: Echo = field(default_factory=Echo)
    signal: str = NO_TRADE
    conviction: float = 0.0     # strength [0,1]
    reason: str = ""


def analyze(symbol: str, candles: List[Candle], cfg, learned: Optional[dict] = None) -> MarketRead:
    """port of asherin.pine's per-bar read, evaluated on the latest closed bar."""
    n = len(candles)
    price = candles[-1].close if candles else 0.0

    atr_len = cfg.atr_lookback
    W = cfg.war_window
    need = max(cfg.frame_len, cfg.normal_len, atr_len, W) + 2

    if n < need:
        return MarketRead(
            symbol=symbol, price=price, atr=1e-9, normal_body=1e-9,
            buyer_force=0, seller_force=0, dominance=0, winner=0,
            war_resolved=False, velocity_established=False, conflict=0, total_energy=0,
            passion=0, passion_idx=0, elongation=1.0, double_taps=0, persistence=0,
            echo=Echo(), signal=NO_TRADE, conviction=0.0,
            reason=f"warming up ({n}/{need} candles)",
        )

    atrs = _atr_series(candles, atr_len)
    L = n - 1
    atr_cur = atrs[L]

    c0 = candles[L]
    body_cur = c0.body
    rngN_cur = c0.rng / atr_cur
    bodyN_cur = abs(body_cur) / atr_cur
    wickN_cur = (c0.upper_wick + c0.lower_wick) / atr_cur

    # ----- velocity / trend (section 2 & 5) -----
    disp = c0.close - candles[L - W].close
    normVel = (disp / W) / atr_cur
    trendDir = _sign(normVel)

    # ----- micro-war: normalize every bar with the current ATR, matching pine -----
    netForce = sum(candles[L - k].body / atr_cur for k in range(W))
    netDir = _sign(netForce)

    # battles / conflict density (adjacent colour flips inside the window)
    battles = 0
    for k in range(W - 1):
        s1 = _sign(candles[L - k].body)
        s2 = _sign(candles[L - k - 1].body)
        if s1 != s2 and s1 != 0 and s2 != 0:
            battles += 1
    conflictDensity = battles / float(W - 1) if W > 1 else 0.0

    # up/down velocity (for the bouncing/energy read + display)
    upVel = sum(max(candles[L - k].close - candles[L - k - 1].close, 0.0) for k in range(W)) / atr_cur
    dnVel = sum(max(candles[L - k - 1].close - candles[L - k].close, 0.0) for k in range(W)) / atr_cur

    # ----- normal rate + elongation (section 9) -----
    normalRate = sum(candles[i].rng / atrs[i] for i in range(L - cfg.normal_len + 1, L + 1)) / cfg.normal_len
    elongation = rngN_cur / normalRate if normalRate > 0 else 1.0

    # ----- persistence (holdCount over the window, current atr) -----
    holdCount = sum(1 for k in range(W) if (abs(candles[L - k].body) / atr_cur) < cfg.stub_body)
    persistence = holdCount / float(W) if W > 0 else 0.0

    # ----- passion (section 6 & 8) -----
    passion = (
        cfg.w_mag * rngN_cur
        + cfg.w_conf * (conflictDensity * 3)
        + cfg.w_wick * wickN_cur
        + cfg.w_pers * (persistence * 3)
    )

    # ----- echo (section 7): replay the var-state over the series -----
    echoDir = 0
    echoLen: Optional[int] = None
    echoMag: Optional[float] = None
    lastBigBar: Optional[int] = None
    for i in range(n):
        rngN_i = candles[i].rng / atrs[i]
        bodyN_i = abs(candles[i].body) / atrs[i]
        if rngN_i > cfg.echo_trigger:
            lastBigBar = i
        if lastBigBar is not None:
            respBar = i - lastBigBar
            if 0 < respBar <= cfg.echo_max and bodyN_i > 0.05:
                echoDir = _sign(candles[i].body)
                echoLen = respBar
                echoMag = rngN_i
    echoPresent = echoLen is not None

    # ----- war resolution + dominance (section 5 & 11) -----
    warResolved = abs(normVel) >= cfg.trend_thresh and abs(netForce) >= cfg.dom_thresh and trendDir == netDir
    winnerClear = warResolved or abs(netForce) >= cfg.dom_thresh
    winnerDir = (trendDir if warResolved else netDir) if winnerClear else 0

    # ----- trade decision: fresh winner + echo agreement, else wait -----
    signalLong = winnerDir > 0 and echoDir > 0 and echoPresent
    signalShort = winnerDir < 0 and echoDir < 0 and echoPresent
    echoFresh = echoPresent and echoLen is not None and echoLen <= cfg.echo_max
    rawDir = 1 if echoFresh and signalLong else (-1 if echoFresh and signalShort else 0)

    # ----- strength (drives SL/TP shaping in strategy.py) -----
    echoRecency = max(0.0, (cfg.echo_max - echoLen) / float(cfg.echo_max)) if echoPresent else 0.0
    echoMagN = min(1.0, echoMag / cfg.echo_trigger) if echoMag is not None else 0.0
    echoStrength = (echoRecency + echoMagN) / 2.0
    passionRel = min(2.0, passion / cfg.passion_thresh) if cfg.passion_thresh > 0 else 0.0
    strength = _clip((passionRel / 2.0 + echoStrength) / 2.0, 0.0, 1.0)

    echo = Echo(
        direction=echoDir,
        length=echoLen if echoLen is not None else 0,
        latency=echoLen if echoLen is not None else 0,
        magnitude=echoMag if echoMag is not None else 0.0,
        micro_count=0,
        reinforces_winner=(echoDir == winnerDir and winnerDir != 0),
    )

    read = MarketRead(
        symbol=symbol, price=price, atr=atr_cur, normal_body=normalRate,
        buyer_force=upVel, seller_force=dnVel, dominance=netForce, winner=winnerDir,
        war_resolved=warResolved, velocity_established=abs(normVel) >= cfg.trend_thresh,
        conflict=conflictDensity, total_energy=upVel + dnVel, passion=passion,
        passion_idx=0, elongation=elongation, double_taps=battles, persistence=persistence,
        echo=echo, conviction=strength,
    )

    # verdict + reason (mirrors the indicator's curSignal wording)
    if winnerDir == 0:
        read.signal, read.reason = NO_TRADE, f"wait (no clear winner) |netForce|={abs(netForce):.2f}<{cfg.dom_thresh}"
    elif not echoPresent:
        read.signal, read.reason = NO_TRADE, "wait (no echo yet)"
    elif rawDir == 0:
        read.signal, read.reason = NO_TRADE, f"wait (echo disagrees) winner={winnerDir:+d} echo={echoDir:+d}"
    else:
        read.signal = LONG if rawDir > 0 else SHORT
        label = "persistence (buyers)" if netDir > 0 else "exhaustion (sellers)"
        read.reason = (
            f"{label}: netForce={netForce:+.2f} normVel={normVel:+.3f} "
            f"passion={passion:.2f} echo(dir={echoDir:+d},len={echo.length}) "
            f"strength={strength:.2f}"
        )

    # validated overextension filters: a valid winner+echo signal is still too
    # late when passion, velocity, or net force has already reached exhaustion.
    # These veto entries; they never create or loosen a signal.
    if read.signal in (LONG, SHORT):
        if passion >= cfg.passion_max:
            read.signal, read.reason = NO_TRADE, f"overextension veto: passion {passion:.2f} >= {cfg.passion_max:.2f}"
        elif abs(normVel) >= cfg.norm_vel_max:
            read.signal, read.reason = NO_TRADE, f"overextension veto: |normVel| {abs(normVel):.3f} >= {cfg.norm_vel_max:.2f}"
        elif abs(netForce) < cfg.net_force_min:
            read.signal, read.reason = NO_TRADE, f"underpowered veto: |netForce| {abs(netForce):.2f} < {cfg.net_force_min:.2f}"
        elif abs(netForce) > cfg.net_force_max:
            read.signal, read.reason = NO_TRADE, f"overextension veto: |netForce| {abs(netForce):.2f} > {cfg.net_force_max:.2f}"
        elif cfg.reject_bouncing and upVel > cfg.conflict_thresh and dnVel > cfg.conflict_thresh:
            read.signal, read.reason = NO_TRADE, "regime veto: bouncing / two-sided velocity"

    # self-learned filters (from reflect.py). they ONLY veto a trade the base
    # rules would have taken -- they can never create or loosen one.
    if read.signal in (LONG, SHORT) and learned:
        ms = learned.get("min_strength")
        mc = learned.get("max_conflict")
        mf = learned.get("min_abs_net_force")
        me = learned.get("min_echo_len")
        if ms is not None and strength < ms:
            read.signal, read.reason = NO_TRADE, f"learned veto: strength {strength:.2f} < {ms:.2f}"
        elif mc is not None and conflictDensity > mc:
            read.signal, read.reason = NO_TRADE, f"learned veto: conflict {conflictDensity:.2f} > {mc:.2f}"
        elif mf is not None and abs(netForce) < mf:
            read.signal, read.reason = NO_TRADE, f"learned veto: |netForce| {abs(netForce):.2f} < {mf:.2f}"
        elif me is not None and echo.length < me:
            read.signal, read.reason = NO_TRADE, f"learned veto: echo len {echo.length} < {int(me)}"

    return read
