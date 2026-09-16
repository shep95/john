"""analysis engine -- john's narrative turned into deterministic math.

the whole point (narrative section 24): the unit is the *relationship between
movements*, not the candle shape. so we never say "shape X => buy". we measure
movement + context + velocity, detect the micro-war winner, read the echo that
follows the passion point, and only then derive a directional interpretation.

section map (narrative -> code):
  1  movement + context + velocity = meaning ...... every primitive is /atr normalized
  2  trend is velocity (normalized by volatility) . velocity_norm, atr
  3  frame = contextual position ................... window slice + recency weights
  4  market = competing movement (micro-war) ....... buyer_force / seller_force
  5  war ends on directional dominance ............. dominance, winner, war_resolved
  6  passion point != big candle ................... passion_point + passion score
  7  echoes reveal interaction after a move ........ Echo (latency/len/dir/magnitude)
  8  passion = persistence + conflict, not size .... persistence/repetition/defense weights
  9  normal rate belongs to the pattern ............ normal_body/range, elongation
  11 persistence vs exhaustion = same primitive .... directional dominance, label derived late
  13 double taps / contradictory sub-moves ......... conflict_density, double_taps
  17 both sides high velocity = bouncing ........... conflict balance -> no-trade gate
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


def _logistic(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


# ---------------------------------------------------------------------------
# section 2 & 9 -- volatility (the environment) and the pattern's normal rate
# ---------------------------------------------------------------------------
def atr(candles: List[Candle], lookback: int) -> float:
    if len(candles) < 2:
        return candles[-1].rng if candles else 1e-9
    trs = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        trs.append(max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)))
    window = trs[-lookback:]
    return max(sum(window) / len(window), 1e-9)


def normal_body(candles: List[Candle], lookback: int) -> float:
    w = candles[-lookback:]
    return max(sum(c.abs_body for c in w) / len(w), 1e-9)


def normal_range(candles: List[Candle], lookback: int) -> float:
    w = candles[-lookback:]
    return max(sum(c.rng for c in w) / len(w), 1e-9)


# ---------------------------------------------------------------------------
# section 7 -- the echo after the passion point
# ---------------------------------------------------------------------------
@dataclass
class Echo:
    direction: int = 0          # net direction of the follow-through
    length: int = 0             # consecutive candles continuing the winner's way
    latency: int = 0            # candles until the first real response
    magnitude: float = 0.0      # summed follow-through body, /atr
    micro_count: int = 0        # small "micro candle echoes" after the point
    reinforces_winner: bool = False


def read_echo(window: List[Candle], point_idx: int, winner: int, atr_v: float, norm_body: float) -> Echo:
    """narrative section 7 & 13: the sequence of smaller movements after the
    initial (passion) movement. timing (latency), magnitude, direction, length.
    reinforcement = follow-through on the winner's side; opposition = against it.
    """
    echo = Echo()
    tail = window[point_idx + 1:]
    if not tail:
        return echo

    # latency: candles until the first non-trivial response
    latency = 0
    for c in tail:
        latency += 1
        if c.abs_body > 0.2 * atr_v:
            break
    echo.latency = latency

    # net follow-through direction + magnitude
    net = sum(c.body for c in tail)
    echo.direction = 1 if net > 0 else (-1 if net < 0 else 0)
    echo.magnitude = sum(c.abs_body for c in tail) / atr_v

    # length: consecutive candles continuing in the winner's direction,
    # tolerating tiny (sub-normal) candles as continuation, breaking on a
    # strong opposite candle.
    length = 0
    for c in tail:
        if c.direction == winner or c.abs_body < 0.35 * norm_body:
            length += 1
        else:
            break
    echo.length = length

    echo.micro_count = sum(1 for c in tail if c.abs_body < norm_body)
    echo.reinforces_winner = echo.direction == winner and winner != 0
    return echo


# ---------------------------------------------------------------------------
# the full read of the current frame
# ---------------------------------------------------------------------------
@dataclass
class MarketRead:
    symbol: str
    price: float
    atr: float
    normal_body: float
    # micro-war (sections 4,5)
    buyer_force: float
    seller_force: float
    dominance: float            # [-1,1], sign = winner
    winner: int                 # +1 buyers, -1 sellers, 0 none
    war_resolved: bool
    velocity_established: bool
    conflict: float             # [0,1], balance of forces (1 = perfect bounce)
    total_energy: float
    # passion (sections 6,8)
    passion: float              # [0,1] inferred passion of the winning move
    passion_idx: int            # index in window of the passion point
    elongation: float           # passion range / normal range (section 9)
    double_taps: int            # repeated contradictory activations (section 13)
    persistence: float          # fraction of window on winner's side (section 8)
    # echo (section 7)
    echo: Echo = field(default_factory=Echo)
    # verdict
    signal: str = NO_TRADE
    conviction: float = 0.0     # [0,1]
    reason: str = ""


def analyze(symbol: str, candles: List[Candle], cfg) -> MarketRead:
    """turn closed candles into a full MarketRead. deterministic, no model."""
    n = len(candles)
    price = candles[-1].close if candles else 0.0
    atr_v = atr(candles, cfg.atr_lookback)
    norm_body = normal_body(candles, cfg.atr_lookback)
    norm_rng = normal_range(candles, cfg.atr_lookback)

    W = min(cfg.war_window, n)
    window = candles[-W:]

    if n < max(cfg.war_window, 3):
        return MarketRead(
            symbol=symbol, price=price, atr=atr_v, normal_body=norm_body,
            buyer_force=0, seller_force=0, dominance=0, winner=0,
            war_resolved=False, velocity_established=False, conflict=0, total_energy=0,
            passion=0, passion_idx=max(0, W - 1), elongation=1.0, double_taps=0,
            persistence=0, echo=Echo(), signal=NO_TRADE, conviction=0.0,
            reason="not enough candles yet",
        )

    # ----- section 4: competing movement -> buyer/seller force -----
    # each candle pushes its direction via body; wick rejections count as
    # *defense* (buyers holding lows / sellers capping highs). recent candles
    # weigh more (section 3: frame = where we are inside the context).
    wick_w = 0.5
    buyer_force = seller_force = 0.0
    for i, c in enumerate(window):
        rw = (i + 1) / W  # recency weight, newest heaviest
        buyer_push = (max(c.body, 0.0) + wick_w * c.lower_wick) / atr_v
        seller_push = (max(-c.body, 0.0) + wick_w * c.upper_wick) / atr_v
        buyer_force += rw * buyer_push
        seller_force += rw * seller_push

    total = buyer_force + seller_force + 1e-9
    dominance = (buyer_force - seller_force) / total
    winner = 1 if dominance > 0 else (-1 if dominance < 0 else 0)

    # conflict / bounce (section 17): how balanced the two sides are.
    conflict = (2.0 * min(buyer_force, seller_force)) / total
    total_energy = buyer_force + seller_force

    # ----- section 5: velocity established? (acceleration in winner's dir) -----
    def dir_vel(cs: List[Candle]) -> float:
        if not cs:
            return 0.0
        return sum(max(c.body * winner, 0.0) for c in cs) / (len(cs) * atr_v)

    recent_k = min(3, W)
    recent_vel = dir_vel(window[-recent_k:])
    window_vel = dir_vel(window)
    velocity_established = winner != 0 and recent_vel >= max(0.12, 0.9 * window_vel)
    war_resolved = abs(dominance) >= cfg.dominance_threshold and velocity_established

    # ----- section 6 & 8: passion point + passion score -----
    # per-candle passion emphasises resistance/holding (wicks) and range, not
    # just body, so a smaller but contested candle can out-score a big empty one.
    def passion_raw(c: Candle) -> float:
        rejection = (c.upper_wick + c.lower_wick) / atr_v
        return 0.6 * (c.abs_body / atr_v) + 0.5 * (c.rng / atr_v) + 0.7 * rejection

    passion_idx = max(range(W), key=lambda i: passion_raw(window[i]))
    p_candle = window[passion_idx]
    elongation = p_candle.rng / norm_rng

    # persistence (section 8): fraction of the window on the winner's side
    persistence = sum(1 for c in window if c.direction == winner) / W if winner else 0.0

    # double taps / contradictory sub-moves (section 13): direction flips inside
    # the window -> repeated structural activation / conflict density.
    double_taps = sum(1 for i in range(1, W) if window[i].direction != window[i - 1].direction and window[i].direction != 0)

    echo = read_echo(window, passion_idx, winner, atr_v, norm_body)

    # passion is an *inferred state* from several observations (section 8):
    # magnitude + persistence + repetition + defense + echo reinforcement.
    rejection_norm = (p_candle.upper_wick + p_candle.lower_wick) / atr_v
    passion_score = (
        0.9 * (p_candle.abs_body / atr_v)
        + 1.1 * persistence
        + 0.35 * double_taps
        + 0.8 * rejection_norm
        + 0.6 * (echo.length)
        + 0.5 * (1.0 if echo.reinforces_winner else 0.0)
        - 0.4 * conflict
    )
    passion = _clip(_logistic(0.6 * passion_score - 1.4), 0.0, 1.0)

    read = MarketRead(
        symbol=symbol, price=price, atr=atr_v, normal_body=norm_body,
        buyer_force=buyer_force, seller_force=seller_force, dominance=dominance,
        winner=winner, war_resolved=war_resolved, velocity_established=velocity_established,
        conflict=conflict, total_energy=total_energy, passion=passion,
        passion_idx=passion_idx, elongation=elongation, double_taps=double_taps,
        persistence=persistence, echo=echo,
    )

    _decide(read, cfg)
    return read


# ---------------------------------------------------------------------------
# section 5 & narrative rule: "winner + echo direction and length determines
# short or long. if unclear or blank -> no trade till more context arrives."
# ---------------------------------------------------------------------------
def _decide(r: MarketRead, cfg) -> None:
    if r.winner == 0:
        r.signal, r.reason = NO_TRADE, "no winner (flat)"
        return
    if not r.war_resolved:
        r.signal, r.reason = NO_TRADE, f"war unresolved (|dom|={abs(r.dominance):.2f}, vel_est={r.velocity_established})"
        return
    # section 17: both sides high velocity -> high bouncing, no lasting dominance
    if r.conflict >= cfg.conflict_ceiling:
        r.signal, r.reason = NO_TRADE, f"high bouncing (conflict={r.conflict:.2f})"
        return
    # echo must confirm the winner with enough length
    if not r.echo.reinforces_winner:
        r.signal, r.reason = NO_TRADE, "echo does not reinforce winner"
        return
    if r.echo.length < cfg.echo_min_len:
        r.signal, r.reason = NO_TRADE, f"echo too short (len={r.echo.length} < {cfg.echo_min_len})"
        return
    if r.passion < cfg.passion_min:
        r.signal, r.reason = NO_TRADE, f"passion below floor ({r.passion:.2f})"
        return

    # conviction blends passion, echo length/magnitude, and dominance (section 8).
    # john's SL/TP rule: "passion point being the guiding principle ... higher
    # passion + more explicit echo = lower SL and higher TP." so passion leads
    # the blend (0.45) and the echo is the next strongest input (0.30 combined),
    # with dominance (0.25) as support -- passion + echo drive the SL/TP shaping.
    echo_len_norm = _clip(r.echo.length / max(cfg.war_window - 1, 1), 0.0, 1.0)
    conviction = _clip(
        0.45 * r.passion
        + 0.20 * echo_len_norm
        + 0.10 * _clip(r.echo.magnitude / 3.0, 0.0, 1.0)
        + 0.25 * abs(r.dominance),
        0.0, 1.0,
    )
    r.conviction = conviction
    r.signal = LONG if r.winner > 0 else SHORT
    label = "persistence(up)" if r.winner > 0 else "exhaustion(down)"  # section 11
    r.reason = (
        f"{label}: dom={r.dominance:+.2f} passion={r.passion:.2f} "
        f"echo(dir={r.echo.direction:+d},len={r.echo.length},lat={r.echo.latency}) "
        f"conviction={conviction:.2f}"
    )
