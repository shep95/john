"""shepherd · movement — the indicator, compiled into a streaming python engine.

this is a bar-for-bar port of `shepherd.pine`. feed it closed candles in order
with `update(candle)` and it keeps every piece of state the pine script keeps:
movements, echo, war/resolution, the pattern memory (melody matching), the
projection plan, and four simulated engines (framework, projection, momentum,
random) that act as the validation record.

nothing here looks ahead. each call to `update` sees only the candle it is given
and the ones before it, exactly like a pine script executing on bar close.
`tests/test_shepherd.py` checks that property directly.
"""
from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional

import numpy as np

from . import asherin as ash
from .market import Candle

STATE_NAMES = ("stall", "probe ↑", "probe ↓", "war", "bounce",
               "dominance ↑", "dominance ↓", "decay ↑", "decay ↓")
REGIME_NAMES = ("range · low vol", "range · high vol", "trend · low vol", "trend · high vol")


@dataclass
class Params:
    # segmentation
    atr_len: int = 14
    seg_mult: float = 1.5
    win: int = 7
    # interaction
    impulse_mult: float = 1.0
    echo_win: int = 5
    echo_net_min: float = 0.25
    echo_min_qual: float = 0.2
    echo_fresh: int = 12
    resolve_vel: float = 1.2
    conflict_max: float = 0.35
    decay_frac: float = 0.5
    probe_vel: float = 0.6
    probe_min: int = 4
    stall_vel: float = 0.3
    bounce_vel: float = 0.9
    def_tol: float = 0.25
    sweep_wick: float = 0.55
    # 0 disables the thin-terrain check. crypto perp volume is real exchange
    # volume, so this stays on by default; set 0 for tick-volume feeds.
    thin_vol: float = 0.6
    # regime / normal rate
    reg_len: int = 100
    er_trend: float = 0.3
    norm_n: int = 40
    # pattern memory
    seq_k: int = 5
    rhythm_w: float = 0.5
    mem_max: int = 400
    knn_k: int = 15
    min_mem: int = 60
    min_prob: float = 0.56
    min_edge: float = 0.04
    req_pat: bool = True
    roll_n: int = 40
    decay_tol: float = 0.08
    halt_decay: bool = True
    # trade
    res_look: int = 3
    chase_max: float = 1.0
    sl_buf: float = 0.3
    max_stop: float = 3.0
    min_rr: float = 1.5
    pass_ext: float = 0.5
    time_mult: float = 2.0
    cost_pct: float = 0.12
    cost_mult: float = 3.0
    # projection
    proj_min_p: float = 0.56
    proj_min_edge: float = 0.03
    proj_winner: bool = False
    depth_q: float = 0.4
    wait_bars: int = 0
    # baselines / sim risk
    max_day_r: float = 3.0
    p_rand: float = 0.03


@dataclass
class Seg:
    b0: int
    p0: float
    b1: int
    p1: float
    dir: int
    dur: int
    mag: float
    reg: int


@dataclass
class Mem:
    iv: List[float]
    rh: List[float]
    dir: int
    cont_lvl: float
    fail_lvl: float
    outcome: int
    pred: Optional[float]
    born: int


@dataclass
class SimEngine:
    """one simulated book. r-multiples only — sizing lives in the live broker."""
    name: str
    dir: int = 0
    entry: float = math.nan
    sl: float = math.nan
    tp: float = math.nan
    risk: float = math.nan
    bar0: int = -1
    max_bars: int = 0
    trades: int = 0
    wins: int = 0
    sum_r: float = 0.0
    day_r: float = 0.0
    history: List[float] = field(default_factory=list)

    @property
    def expectancy(self) -> Optional[float]:
        return self.sum_r / self.trades if self.trades else None

    @property
    def winrate(self) -> Optional[float]:
        return self.wins / self.trades if self.trades else None


@dataclass
class Event:
    """something the selected sim engine did on this bar, for the live follower."""
    kind: str               # open · pending · cancel · fill · exit
    engine: str
    dir: int = 0
    price: float = math.nan
    sl: float = math.nan
    tp: float = math.nan
    max_bars: int = 0
    code: int = 0           # exit: 1 target · -1 stop · 2 time
    r: float = math.nan
    reason: str = ""


@dataclass
class Projection:
    call: int = 0
    p: float = math.nan
    p_up: float = math.nan
    b_up: float = math.nan
    sl: float = math.nan
    limit: float = math.nan
    fill: float = math.nan
    tgt: float = math.nan
    ev_now: float = math.nan
    ev_wait: float = math.nan
    w_now: float = math.nan
    entry: float = math.nan
    tp: float = math.nan
    rr: float = math.nan
    ev: float = math.nan
    mode: str = ""
    msg: str = ""


@dataclass
class Reading:
    bar: int
    time_ms: int
    close: float
    ready: bool
    atr: float
    state: int
    regime: int
    velocity: float
    thin: bool
    effort_up: float
    effort_dn: float
    conflict: float
    dom_dir: int
    echo_dir: int
    echo_qual: float
    passion_idx: float
    normal_dur: float
    normal_mag: float
    p_up: float
    base_up: float
    mem_resolved: int
    decayed: bool
    gate: str
    projection: Projection
    events: List[Event]

    @property
    def state_name(self) -> str:
        return STATE_NAMES[self.state]

    @property
    def regime_name(self) -> str:
        return REGIME_NAMES[self.regime]


def _nan(x: Optional[float]) -> bool:
    return x is None or (isinstance(x, float) and math.isnan(x))


def _dtw(a1, r1, a2, r2, w_r: float) -> float:
    # two movement sequences compared like melodies. the band keeps the warp to
    # one step — tempo is already normalized, wider warps just smear identity.
    n = len(a1)
    big = 1e10
    D = [[big] * (n + 1) for _ in range(n + 1)]
    D[0][0] = 0.0
    for i in range(1, n + 1):
        for j in range(max(1, i - 1), min(n, i + 1) + 1):
            c = abs(a1[i - 1] - a2[j - 1]) + w_r * abs(r1[i - 1] - r2[j - 1])
            D[i][j] = c + min(D[i - 1][j], D[i][j - 1], D[i - 1][j - 1])
    return D[n][n]


class _Rolling:
    """pine's math.sum / ta.sma: None until the window is full."""

    def __init__(self, n: int):
        self.n = n
        self.q: Deque[float] = deque()
        self.s = 0.0

    def push(self, x: float) -> Optional[float]:
        self.q.append(x)
        self.s += x
        if len(self.q) > self.n:
            self.s -= self.q.popleft()
        return self.s if len(self.q) == self.n else None


class Shepherd:
    def __init__(self, p: Optional[Params] = None, seed: int = 42, primary: str = "projection",
                 ap: Optional[ash.AsherinParams] = None):
        self.p = p or Params()
        self.ap = ap or ash.AsherinParams()
        self.ash_window: Deque[Candle] = deque(maxlen=self.ap.history_candles)
        self.primary = primary
        P = self.p
        self.bar = -1
        hist = max(P.reg_len, P.win, 20) + 5
        self.closes: Deque[float] = deque(maxlen=hist)
        self.prev_close: Optional[float] = None
        self.prev_time: Optional[int] = None
        # atr (wilder rma of true range)
        self._tr_seed: List[float] = []
        self.atr: Optional[float] = None
        # rolling windows
        self.r_upb = _Rolling(P.win)
        self.r_dnb = _Rolling(P.win)
        self.r_upc = _Rolling(P.win)
        self.r_dnc = _Rolling(P.win)
        self.r_vol_ma = _Rolling(20)
        self.r_vol_w = _Rolling(P.win)
        self.r_path = _Rolling(P.reg_len)
        self.r_atr_ma = _Rolling(P.reg_len)
        self.r_touch_l = _Rolling(P.win)
        self.r_touch_s = _Rolling(P.win)
        self.r_res_up = _Rolling(P.win)
        self.r_res_dn = _Rolling(P.win)
        self.st_hist: Deque[int] = deque(maxlen=P.win + 1)
        self.ready_hist: Deque[bool] = deque(maxlen=P.win + 1)
        self.prev_nv: Optional[float] = None
        self.prev_call = 0
        # segmentation
        self.seg_dir = 0
        self.sP0 = math.nan
        self.sB0 = -1
        self.xP = math.nan
        self.xB = -1
        self.swing_lo = math.nan
        self.swing_hi = math.nan
        self.last_tempo = math.nan
        self.last_amp = math.nan
        self.segs: List[Seg] = []
        self.buckets = [([], []) for _ in range(4)]
        self.retr: List[float] = []
        # echo
        self.e_dir = 0
        self.e_bar = -1
        self.e_close = math.nan
        self.e_same = 0
        self.e_counter = 0
        self.e_run = 0
        self.e_run_open = False
        self.e_lat: Optional[int] = None
        self.echo_dir = 0
        self.echo_qual = math.nan
        self.echo_run = 0
        self.echo_lat: Optional[int] = None
        self.echo_net = math.nan
        self.echo_bar: Optional[int] = None
        # war
        self.dom_dir = 0
        self.dom_peak = 0.0
        self.res_bar: Optional[int] = None
        self.res_price = math.nan
        # precursor test
        self.pc_n = 0
        self.pc_hit = 0
        self.bs_n = 0
        self.bs_hit = 0
        # pattern memory
        self.mem: List[Mem] = []
        self.pending: List[Mem] = []
        self.p_cont: Optional[float] = None
        self.base_cont: Optional[float] = None
        self.mem_resolved = 0
        self.match_q = math.nan
        self.last_seg_dir = 0
        self.cal_n = [0] * 5
        self.cal_hit = [0] * 5
        self.acc_roll: Deque[int] = deque()
        self.acc_n = 0
        self.acc_hit = 0
        # engines
        self.fw = SimEngine("framework")
        self.pj = SimEngine("projection")
        self.mo = SimEngine("momentum")
        self.rn = SimEngine("random")
        self.ash = SimEngine("asherin")
        self.pend_dir = 0
        self.pend_px = math.nan
        self.pend_sl = math.nan
        self.pend_tp = math.nan
        self.pend_exp = 0
        self._rng_a = random.Random(seed)
        self._rng_b = random.Random(seed + 1295)
        self.bh0: Optional[float] = None
        self.last: Optional[Reading] = None

    # ------------------------------------------------------------------ utils
    def engines(self) -> Dict[str, SimEngine]:
        return {"framework": self.fw, "projection": self.pj, "asherin": self.ash,
                "momentum": self.mo, "random": self.rn}

    def _close_ago(self, k: int) -> Optional[float]:
        if len(self.closes) <= k:
            return None
        return self.closes[-1 - k]

    # ------------------------------------------------------------------ engine sim
    def _record(self, e: SimEngine, r: float) -> None:
        e.trades += 1
        e.wins += 1 if r > 0 else 0
        e.sum_r += r
        e.day_r += r
        e.dir = 0
        e.history.append(r)

    def _step(self, e: SimEngine, c: Candle, cost: float, events: List[Event]) -> None:
        if e.dir == 0 or self.bar <= e.bar0:
            return
        cost_r = cost * e.entry / e.risk
        d = e.dir
        sl_gap = c.open <= e.sl if d == 1 else c.open >= e.sl
        tp_gap = c.open >= e.tp if d == 1 else c.open <= e.tp
        sl_hit = c.low <= e.sl if d == 1 else c.high >= e.sl
        tp_hit = c.high >= e.tp if d == 1 else c.low <= e.tp
        code, r = 0, math.nan
        if sl_gap or tp_gap:
            r = (c.open - e.entry) * d / e.risk - cost_r
            code = -1 if sl_gap else 1
        elif sl_hit:
            # stop and target in one candle: order unknown inside the bar, assume the stop
            r = (e.sl - e.entry) * d / e.risk - cost_r
            code = -1
        elif tp_hit:
            r = (e.tp - e.entry) * d / e.risk - cost_r
            code = 1
        elif self.bar - e.bar0 >= e.max_bars:
            r = (c.close - e.entry) * d / e.risk - cost_r
            code = 2
        if code != 0:
            self._record(e, r)
            events.append(Event("exit", e.name, dir=d, price=c.close, code=code, r=r))

    def _open(self, e: SimEngine, d: int, px: float, sl: float, tp: float, max_bars: int,
              events: List[Event], kind: str = "open") -> None:
        e.dir, e.entry, e.sl, e.tp = d, px, sl, tp
        e.risk = abs(px - sl)
        e.bar0 = self.bar
        e.max_bars = max_bars
        events.append(Event(kind, e.name, dir=d, price=px, sl=sl, tp=tp, max_bars=max_bars))

    # ------------------------------------------------------------------ main
    def update(self, c: Candle) -> Reading:
        P = self.p
        self.bar += 1
        bar = self.bar
        events: List[Event] = []

        # ── core series ───────────────────────────────────────────────
        if self.prev_close is None:
            tr = c.high - c.low
        else:
            tr = max(c.high - c.low, abs(c.high - self.prev_close), abs(c.low - self.prev_close))
        if self.atr is None:
            self._tr_seed.append(tr)
            if len(self._tr_seed) == P.atr_len:
                self.atr = sum(self._tr_seed) / P.atr_len
        else:
            self.atr = (self.atr * (P.atr_len - 1) + tr) / P.atr_len
        atr = self.atr

        self.closes.append(c.close)
        body = c.close - c.open
        abs_b = abs(body)
        rng = c.high - c.low
        sq_w = math.sqrt(P.win)
        up_b = self.r_upb.push(body if body > 0 else 0.0)
        dn_b = self.r_dnb.push(-body if body < 0 else 0.0)
        cw = self._close_ago(P.win)
        nv = (c.close - cw) / (atr * sq_w) if (atr and cw is not None) else None
        abs_nv = abs(nv) if nv is not None else math.nan
        dir_win = (1 if nv > 0 else -1 if nv < 0 else 0) if nv is not None else 0
        eff_up = up_b / (atr * sq_w) if (up_b is not None and atr) else math.nan
        eff_dn = dn_b / (atr * sq_w) if (dn_b is not None and atr) else math.nan
        tot_b = (up_b + dn_b) if (up_b is not None and dn_b is not None) else None
        if tot_b is not None and tot_b > 0:
            opp = dn_b if dir_win == 1 else up_b if dir_win == -1 else min(up_b, dn_b)
            conflict = opp / tot_b
        else:
            conflict = 0.0
        up_cnt = self.r_upc.push(1.0 if c.close > c.open else 0.0)
        dn_cnt = self.r_dnc.push(1.0 if c.close < c.open else 0.0)
        up_cnt = int(up_cnt) if up_cnt is not None else 0
        dn_cnt = int(dn_cnt) if dn_cnt is not None else 0

        vol = c.volume or 0.0
        vol_sum = self.r_vol_ma.push(vol)
        vol_ma = vol_sum / 20.0 if vol_sum is not None else None
        vol_w = self.r_vol_w.push(vol)
        has_vol = (vol_ma or 0.0) > 0
        rel_vol_w = (vol_w / (vol_ma * P.win)) if (has_vol and vol_w is not None) else 1.0
        # velocity through empty terrain isn't a win — nobody was fighting
        thin = has_vol and P.thin_vol > 0 and rel_vol_w < P.thin_vol

        path = self.r_path.push(abs(c.close - self.prev_close) if self.prev_close is not None else 0.0)
        c_reg = self._close_ago(P.reg_len)
        if path is not None and path > 0 and c_reg is not None:
            er = abs(c.close - c_reg) / path
        else:
            er = 0.0
        atr_sum = self.r_atr_ma.push(atr) if atr is not None else None
        atr_ma = atr_sum / P.reg_len if atr_sum is not None else None
        reg = (2 if er >= P.er_trend else 0) + (1 if (atr is not None and atr_ma is not None and atr >= atr_ma) else 0)

        new_day = False
        if self.prev_time is not None:
            d0 = datetime.fromtimestamp(self.prev_time / 1000, tz=timezone.utc).date()
            d1 = datetime.fromtimestamp(c.open_time / 1000, tz=timezone.utc).date()
            new_day = d0 != d1
        ready = atr is not None and atr_ma is not None and c_reg is not None and atr > 0

        mom_long = (nv is not None and self.prev_nv is not None and nv > P.resolve_vel and self.prev_nv <= P.resolve_vel)
        mom_short = (nv is not None and self.prev_nv is not None and nv < -P.resolve_vel and self.prev_nv >= -P.resolve_vel)
        rv = self._rng_a.random()
        rdv = self._rng_b.random()

        # ── segmentation (real time, no lookahead) ────────────────────
        # sweeps read the swing levels as they stood before this bar touched them
        sweep_low = (ready and not _nan(self.swing_lo) and c.low < self.swing_lo and c.close > self.swing_lo
                     and (min(c.open, c.close) - c.low) >= P.sweep_wick * rng)
        sweep_high = (ready and not _nan(self.swing_hi) and c.high > self.swing_hi and c.close < self.swing_hi
                      and (c.high - max(c.open, c.close)) >= P.sweep_wick * rng)
        seg_new = False
        seg_last: Optional[Seg] = None
        if ready:
            if self.seg_dir == 0:
                self.seg_dir = 1 if c.close >= c.open else -1
                self.sP0 = c.low if self.seg_dir == 1 else c.high
                self.sB0 = bar
                self.xP = c.high if self.seg_dir == 1 else c.low
                self.xB = bar
            else:
                extends = c.high > self.xP if self.seg_dir == 1 else c.low < self.xP
                if extends:
                    self.xP = c.high if self.seg_dir == 1 else c.low
                    self.xB = bar
                elif ((self.xP - c.low) if self.seg_dir == 1 else (c.high - self.xP)) >= P.seg_mult * atr:
                    sd = max(self.xB - self.sB0, 1)
                    sm = abs(self.xP - self.sP0) / atr
                    b_dur, b_mag = self.buckets[reg]
                    nd0 = float(np.median(b_dur)) if len(b_dur) >= 8 else math.nan
                    nm0 = float(np.median(b_mag)) if len(b_mag) >= 8 else math.nan
                    self.last_tempo = sd / nd0 if not math.isnan(nd0) else math.nan
                    self.last_amp = sm / nm0 if not math.isnan(nm0) else math.nan
                    if self.segs:
                        prev = self.segs[-1]
                        prev_range = abs(prev.p1 - prev.p0)
                        if prev_range > 0:
                            self.retr.append(abs(self.xP - self.sP0) / prev_range)
                            if len(self.retr) > 150:
                                self.retr.pop(0)
                    seg_last = Seg(self.sB0, self.sP0, self.xB, self.xP, self.seg_dir, sd, sm, reg)
                    self.segs.append(seg_last)
                    if len(self.segs) > 80:
                        self.segs.pop(0)
                    b_dur.append(float(sd))
                    b_mag.append(sm)
                    if len(b_dur) > P.norm_n:
                        b_dur.pop(0)
                        b_mag.pop(0)
                    if self.seg_dir == 1:
                        self.swing_lo, self.swing_hi = self.sP0, self.xP
                    else:
                        self.swing_hi, self.swing_lo = self.sP0, self.xP
                    seg_new = True
                    self.sP0, self.sB0 = self.xP, self.xB
                    self.seg_dir = -self.seg_dir
                    self.xP = c.high if self.seg_dir == 1 else c.low
                    self.xB = bar

        # ── normal rate (regime, pooled while thin) ───────────────────
        n_dur = n_mag = math.nan
        if ready:
            b_dur, b_mag = self.buckets[reg]
            if len(b_dur) >= 8:
                n_dur, n_mag = float(np.median(b_dur)), float(np.median(b_mag))
            elif len(self.segs) >= 8:
                n_dur = float(np.median([s.dur for s in self.segs]))
                n_mag = float(np.median([s.mag for s in self.segs]))

        # ── defense ───────────────────────────────────────────────────
        def_long = self.sP0 if self.seg_dir == 1 else self.swing_lo
        def_short = self.sP0 if self.seg_dir == -1 else self.swing_hi
        touch_l = (ready and not _nan(def_long) and c.low <= def_long + P.def_tol * atr and c.close > def_long)
        touch_s = (ready and not _nan(def_short) and c.high >= def_short - P.def_tol * atr and c.close < def_short)
        def_l = int(self.r_touch_l.push(1.0 if touch_l else 0.0) or 0)
        def_s = int(self.r_touch_s.push(1.0 if touch_s else 0.0) or 0)

        # ── echo ──────────────────────────────────────────────────────
        is_impulse = ready and abs_b >= P.impulse_mult * atr and abs_b >= 0.6 * rng
        if ready:
            if self.e_dir != 0:
                ek = bar - self.e_bar
                cd = 1 if body > 0 else -1 if body < 0 else 0
                if cd == self.e_dir:
                    self.e_same += 1
                    if self.e_run_open:
                        self.e_run += 1
                    if self.e_lat is None and abs_b >= 0.25 * atr:
                        self.e_lat = ek
                else:
                    self.e_run_open = False
                    if cd == -self.e_dir:
                        self.e_counter += 1
                if ek >= P.echo_win:
                    self.echo_net = (c.close - self.e_close) / atr * self.e_dir
                    self.echo_qual = (self.e_same - self.e_counter) / P.echo_win
                    # an echo that runs against its impulse still names a winner: the other side
                    if self.echo_net >= P.echo_net_min and self.echo_qual > 0:
                        self.echo_dir = self.e_dir
                    elif self.echo_net <= -P.echo_net_min and self.echo_qual < 0:
                        self.echo_dir = -self.e_dir
                    else:
                        self.echo_dir = 0
                    self.echo_run = self.e_run
                    self.echo_lat = self.e_lat
                    self.echo_bar = bar
                    self.e_dir = 0
            if self.e_dir == 0 and is_impulse:
                self.e_dir = 1 if body > 0 else -1
                self.e_bar = bar
                self.e_close = c.close
                self.e_same = self.e_counter = self.e_run = 0
                self.e_run_open = True
                self.e_lat = None

        # ── war · resolution · state ──────────────────────────────────
        res_now = False
        bounce = ready and eff_up >= P.bounce_vel and eff_dn >= P.bounce_vel and abs_nv < P.resolve_vel
        if ready:
            resolves = abs_nv >= P.resolve_vel and conflict <= P.conflict_max and not thin and dir_win != 0
            if resolves and self.dom_dir != dir_win:
                self.dom_dir, self.dom_peak = dir_win, abs_nv
                self.res_bar, self.res_price = bar, c.close
                res_now = True
            elif self.dom_dir != 0:
                if dir_win == self.dom_dir:
                    self.dom_peak = max(self.dom_peak, abs_nv)
                elif (dir_win == -self.dom_dir and abs_nv >= P.stall_vel) or bounce:
                    self.dom_dir, self.dom_peak = 0, 0.0
        st = 0
        if ready:
            if self.dom_dir != 0:
                decaying = abs_nv < P.decay_frac * self.dom_peak
                st = (7 if self.dom_dir == 1 else 8) if decaying else (5 if self.dom_dir == 1 else 6)
            elif bounce:
                st = 4
            elif abs_nv < P.probe_vel and up_cnt >= P.probe_min and up_cnt > dn_cnt:
                st = 1
            elif abs_nv < P.probe_vel and dn_cnt >= P.probe_min and dn_cnt > up_cnt:
                st = 2
            elif abs_nv < P.stall_vel:
                st = 0
            else:
                st = 3

        # ── precursor test ────────────────────────────────────────────
        H = P.win
        up_res = self.r_res_up.push(1.0 if (res_now and self.dom_dir == 1) else 0.0)
        dn_res = self.r_res_dn.push(1.0 if (res_now and self.dom_dir == -1) else 0.0)
        self.st_hist.append(st)
        self.ready_hist.append(ready)
        st_then = self.st_hist[0] if len(self.st_hist) == H + 1 else None
        ready_then = self.ready_hist[0] if len(self.ready_hist) == H + 1 else False
        probe_then = 1 if st_then == 1 else -1 if st_then == 2 else 0
        if ready and ready_then and up_res is not None and dn_res is not None:
            self.bs_n += 2
            self.bs_hit += (1 if up_res > 0 else 0) + (1 if dn_res > 0 else 0)
            if probe_then != 0:
                self.pc_n += 1
                self.pc_hit += 1 if ((up_res if probe_then == 1 else dn_res) > 0) else 0

        # ── pattern memory ────────────────────────────────────────────
        if ready and self.pending:
            for i in range(len(self.pending) - 1, -1, -1):
                m = self.pending[i]
                if bar > m.born:
                    cont = c.high > m.cont_lvl if m.dir == 1 else c.low < m.cont_lvl
                    fail = c.low < m.fail_lvl if m.dir == 1 else c.high > m.fail_lvl
                    if cont and fail:
                        # both levels inside one candle: the order is unknowable from bars
                        m.outcome = -2
                    elif cont or fail:
                        m.outcome = 1 if cont else 0
                        if m.pred is not None:
                            cb = min(int(m.pred * 5), 4)
                            self.cal_n[cb] += 1
                            self.cal_hit[cb] += m.outcome
                            hit = 1 if ((m.pred >= 0.5) == (m.outcome == 1)) else 0
                            self.acc_n += 1
                            self.acc_hit += hit
                            self.acc_roll.append(hit)
                            if len(self.acc_roll) > P.roll_n:
                                self.acc_roll.popleft()
                    if m.outcome != -1:
                        self.pending.pop(i)

        if seg_new and len(self.segs) >= P.seq_k:
            window = self.segs[-P.seq_k:]
            mean_m = sum(s.mag for s in window) / P.seq_k
            mean_ld = sum(math.log(s.dur) for s in window) / P.seq_k
            iv = [seg_last.dir * s.dir * s.mag / mean_m for s in window]
            rh = [math.log(s.dur) - mean_ld for s in window]
            dists: List[float] = []
            outs: List[int] = []
            for mm in self.mem:
                if mm.outcome >= 0 and len(mm.iv) == P.seq_k:
                    dists.append(_dtw(iv, rh, mm.iv, mm.rh, P.rhythm_w))
                    outs.append(mm.outcome)
            self.mem_resolved = len(outs)
            self.base_cont = (sum(outs) / len(outs)) if outs else None
            p_now: Optional[float] = None
            if self.mem_resolved >= P.min_mem:
                idxs = sorted(range(len(dists)), key=lambda k: dists[k])
                kk = min(P.knn_k, self.mem_resolved)
                hits = sum(outs[idxs[j]] for j in range(kk))
                d_sum = sum(dists[idxs[j]] for j in range(kk))
                # shrunk toward the base rate: k neighbors alone overstate confidence
                p_now = (hits + 2.0 * self.base_cont) / (kk + 2.0)
                self.match_q = (d_sum / kk) / max(float(np.median(dists)), 1e-9)
            self.p_cont = p_now
            self.last_seg_dir = seg_last.dir
            nm = Mem(iv, rh, seg_last.dir, seg_last.p1, seg_last.p0, -1, p_now, bar)
            self.mem.append(nm)
            self.pending.append(nm)
            if len(self.mem) > P.mem_max:
                self.mem.pop(0)

        acc_life = self.acc_hit / self.acc_n if self.acc_n else math.nan
        acc_roll_v = sum(self.acc_roll) / len(self.acc_roll) if self.acc_roll else math.nan
        decayed = (len(self.acc_roll) >= P.roll_n and not math.isnan(acc_life)
                   and acc_roll_v < acc_life - P.decay_tol)

        dom = self.dom_dir
        p_dir = edge = math.nan
        if self.p_cont is not None and dom != 0:
            p_dir = self.p_cont if self.last_seg_dir == dom else 1.0 - self.p_cont
            b = self.base_cont if self.last_seg_dir == dom else 1.0 - self.base_cont
            edge = p_dir - b

        # ── passion (vector; the index only stretches the target, unvalidated) ─
        persist = ((bar - self.sB0) / n_dur) if (dom != 0 and self.seg_dir == dom and n_dur > 0) else 0.0
        def_dom = def_l if dom == 1 else def_s if dom == -1 else 0
        echo_s = abs(self.echo_qual) if (dom != 0 and self.echo_dir == dom and not math.isnan(self.echo_qual)) else 0.0
        lat_s = (max(0.0, 1.0 - (self.echo_lat - 1.0) / P.echo_win)
                 if (dom != 0 and self.echo_dir == dom and self.echo_lat is not None) else 0.0)
        passion_idx = (min(persist, 1.0) + min(def_dom / 3.0, 1.0) + min(echo_s, 1.0) + lat_s) / 4.0

        # ── projection ────────────────────────────────────────────────
        pj = self._projection(c, atr, ready, dom, def_long, def_short, n_mag, passion_idx)

        # ── engines ───────────────────────────────────────────────────
        cost = P.cost_pct / 100.0
        if new_day:
            for e in (self.fw, self.pj, self.ash, self.mo, self.rn):
                e.day_r = 0.0
        if ready and self.bh0 is None:
            self.bh0 = c.close
        for e in (self.fw, self.pj, self.ash, self.mo, self.rn):
            self._step(e, c, cost, events)
        mb = 20 if math.isnan(n_dur) else min(max(int(round(n_dur * P.time_mult)), 3), 400)

        # projection engine: takes the majority side of the weighted plan when a new call appears
        if ready and self.pend_dir != 0 and self.pj.dir == 0:
            touched = c.low <= self.pend_px if self.pend_dir == 1 else c.high >= self.pend_px
            gapped = c.open <= self.pend_px if self.pend_dir == 1 else c.open >= self.pend_px
            past_sl = c.open <= self.pend_sl if self.pend_dir == 1 else c.open >= self.pend_sl
            if past_sl:
                self.pend_dir = 0
                events.append(Event("cancel", "projection", reason="gapped past stop"))
            elif touched:
                px = c.open if gapped else self.pend_px
                self._open(self.pj, self.pend_dir, px, self.pend_sl, self.pend_tp, mb, events, kind="fill")
                self.pend_dir = 0
                # filled and stopped inside the same candle: bars can't show the order, so the stop counts
                stop_same = c.low <= self.pj.sl if self.pj.dir == 1 else c.high >= self.pj.sl
                if stop_same:
                    r = (self.pj.sl - self.pj.entry) * self.pj.dir / self.pj.risk - cost * self.pj.entry / self.pj.risk
                    d = self.pj.dir
                    self._record(self.pj, r)
                    events.append(Event("exit", "projection", dir=d, price=self.pj.sl, code=-1, r=r))
            elif bar >= self.pend_exp or pj.call != self.pend_dir:
                self.pend_dir = 0
                events.append(Event("cancel", "projection", reason="expired or call changed"))

        pj_new = pj.call != 0 and pj.call != self.prev_call
        if ready and pj_new and self.pj.dir == 0 and self.pend_dir == 0 and not math.isnan(pj.w_now):
            if pj.w_now >= 0.5:
                self._open(self.pj, pj.call, c.close, pj.sl, c.close + pj.call * pj.tgt, mb, events)
            elif not math.isnan(pj.limit):
                self.pend_dir = pj.call
                self.pend_px = pj.limit
                self.pend_sl = pj.sl
                self.pend_tp = pj.limit + pj.call * pj.tgt
                self.pend_exp = bar + (P.wait_bars if P.wait_bars > 0 else mb)
                events.append(Event("pending", "projection", dir=pj.call, price=pj.limit,
                                    sl=pj.sl, tp=self.pend_tp, max_bars=mb))

        why = "warming up"
        if ready:
            d = dom
            if self.fw.dir != 0:
                why = "in trade"
            elif self.fw.day_r <= -P.max_day_r:
                why = "stood down · daily loss"
            elif d == 0:
                why = "no winner yet"
            elif bar - self.res_bar > P.res_look:
                why = "resolution stale"
            elif (c.close - self.res_price) * d > P.chase_max * atr:
                why = "chasing"
            elif self.echo_dir == 0 or self.echo_bar is None or bar - self.echo_bar > P.echo_fresh:
                why = "echo unclear"
            elif self.echo_dir != d:
                why = "echo against winner"
            elif abs(self.echo_qual) < P.echo_min_qual:
                why = "echo faint"
            elif P.req_pat and math.isnan(p_dir):
                why = "pattern memory blank"
            elif P.req_pat and (p_dir < P.min_prob or edge < P.min_edge):
                why = "pattern edge thin"
            elif P.halt_decay and decayed:
                why = "pattern layer degraded"
            else:
                msg, slv, tpv, rk = self._plan(d, P.pass_ext * passion_idx, c.close, atr, def_long, def_short, n_mag)
                why = msg
                if msg == "":
                    self._open(self.fw, d, c.close, slv, tpv, mb, events)

            # baselines share the same exits; if the framework can't beat them,
            # the edge lives in the exit, not the read
            if self.mo.dir == 0 and (mom_long or mom_short):
                md = 1 if mom_long else -1
                msg, slv, tpv, _ = self._plan(md, 0.0, c.close, atr, def_long, def_short, n_mag)
                if msg == "":
                    self._open(self.mo, md, c.close, slv, tpv, mb, events)
            if self.rn.dir == 0 and rv < P.p_rand:
                rd = 1 if rdv < 0.5 else -1
                msg, slv, tpv, _ = self._plan(rd, 0.0, c.close, atr, def_long, def_short, n_mag)
                if msg == "":
                    self._open(self.rn, rd, c.close, slv, tpv, mb, events)

        # the original asherin read, judged by the same record
        self.ash_window.append(c)
        if ready and self.ash.dir == 0 and len(self.ash_window) == self.ap.history_candles:
            a_dir, a_str, a_atr = ash.read(list(self.ash_window), self.ap)
            if a_dir != 0 and a_atr > 0:
                a_sl, a_tp = ash.levels(a_dir, c.close, a_str, a_atr, self.ap)
                if (c.close - a_sl) * a_dir > 0:
                    self._open(self.ash, a_dir, c.close, a_sl, a_tp, self.ap.max_hold_bars, events)

        self.prev_nv = nv
        self.prev_close = c.close
        self.prev_time = c.open_time
        self.prev_call = pj.call

        p_up = pj.p_up
        b_up = pj.b_up
        reading = Reading(
            bar=bar, time_ms=c.open_time, close=c.close, ready=ready, atr=atr or math.nan,
            state=st, regime=reg, velocity=nv if nv is not None else math.nan, thin=thin,
            effort_up=eff_up, effort_dn=eff_dn, conflict=conflict, dom_dir=dom,
            echo_dir=self.echo_dir, echo_qual=self.echo_qual, passion_idx=passion_idx,
            normal_dur=n_dur, normal_mag=n_mag, p_up=p_up, base_up=b_up,
            mem_resolved=self.mem_resolved, decayed=decayed, gate=why, projection=pj,
            events=[e for e in events if e.engine in ("framework", "projection", "asherin")],
        )
        self.last = reading
        return reading

    # ------------------------------------------------------------------ plans
    def _plan(self, d: int, ext: float, close: float, atr: float, def_long: float,
              def_short: float, n_mag: float):
        # stop from structure (the defended level), target from the regime's normal
        # rate. passion only stretches the target — a clear defended level is what
        # earns the tight stop.
        P = self.p
        lvl = def_long if d == 1 else def_short
        slv = math.nan if _nan(lvl) else lvl - d * P.sl_buf * atr
        rsk = math.nan if math.isnan(slv) else (close - slv) * d
        tgt = math.nan if math.isnan(n_mag) else n_mag * (1.0 + ext) * atr
        tpv = math.nan if math.isnan(tgt) else close + d * tgt
        cost = P.cost_pct / 100.0
        if math.isnan(slv):
            msg = "no defended level"
        elif rsk <= 0:
            msg = "defended level broken"
        elif rsk > P.max_stop * atr:
            msg = "stop too wide"
        elif math.isnan(tgt):
            msg = "normal rate unknown"
        elif tgt < P.cost_mult * cost * close:
            msg = "target inside cost"
        elif tgt / rsk < P.min_rr:
            msg = "reward : risk low"
        else:
            msg = ""
        return msg, slv, tpv, rsk

    def _projection(self, c: Candle, atr, ready, dom, def_long, def_short, n_mag, passion_idx) -> Projection:
        # the limit sits at a normal pullback depth for this chart; fill odds come
        # from how often pullbacks that already went this deep kept going to the
        # limit. p is assumed equal for both entries — deeper fills likely win a
        # little less often, so if anything the wait side is flattered.
        P = self.p
        pj = Projection()
        if self.p_cont is not None and self.base_cont is not None:
            pj.p_up = self.p_cont if self.last_seg_dir == 1 else 1.0 - self.p_cont
            pj.b_up = self.base_cont if self.last_seg_dir == 1 else 1.0 - self.base_cont
        if ready and not math.isnan(pj.p_up):
            if pj.p_up >= P.proj_min_p and pj.p_up - pj.b_up >= P.proj_min_edge:
                pj.call = 1
            elif pj.p_up <= 1.0 - P.proj_min_p and pj.b_up - pj.p_up >= P.proj_min_edge:
                pj.call = -1
            if P.proj_winner and pj.call != 0 and dom != pj.call:
                pj.call = 0
        if pj.call == 0 or math.isnan(n_mag):
            return pj
        d = pj.call
        pj.p = pj.p_up if d == 1 else 1.0 - pj.p_up
        leg_a = self.sP0 if self.seg_dir == d else (self.swing_lo if d == 1 else self.swing_hi)
        leg_b = self.xP if self.seg_dir == d else self.sP0
        leg = 0.0 if (_nan(leg_a) or _nan(leg_b)) else abs(leg_b - leg_a)
        cur_r = 0.0 if (self.seg_dir == d or leg <= 0) else abs(self.xP - self.sP0) / leg
        lvl = def_long if d == 1 else def_short
        pj.sl = math.nan if _nan(lvl) else lvl - d * P.sl_buf * atr
        pj.tgt = n_mag * (1.0 + (P.pass_ext * passion_idx if dom == d else 0.0)) * atr
        cost = P.cost_pct / 100.0
        r_now = math.nan if math.isnan(pj.sl) else (c.close - pj.sl) * d
        if not math.isnan(r_now) and 0 < r_now <= P.max_stop * atr:
            pj.ev_now = pj.p * pj.tgt / r_now - (1.0 - pj.p) - cost * c.close / r_now
        if len(self.retr) >= 20 and leg > 0 and not math.isnan(pj.sl):
            v = min(float(np.percentile(self.retr, P.depth_q * 100.0)), 0.9)
            if cur_r < v:
                lim = leg_b - d * v * leg
                r_w = (lim - pj.sl) * d
                if (c.close - lim) * d > 0 and r_w > 0:
                    n_v = sum(1 for x in self.retr if x >= v)
                    n_c = sum(1 for x in self.retr if x >= cur_r)
                    fill = n_v / n_c if n_c > 0 else math.nan
                    if not math.isnan(fill):
                        pj.fill = fill
                        pj.limit = lim
                        pj.ev_wait = fill * (pj.p * pj.tgt / r_w - (1.0 - pj.p) - cost * lim / r_w)
        e_n = max(0.0 if math.isnan(pj.ev_now) else pj.ev_now, 0.0)
        e_w = max(0.0 if math.isnan(pj.ev_wait) else pj.ev_wait, 0.0)
        if math.isnan(pj.sl):
            pj.msg = "no defended level"
        elif e_n + e_w <= 0:
            pj.msg = "no edge after costs"
        else:
            pj.w_now = e_n / (e_n + e_w)
            pj.entry = c.close if math.isnan(pj.limit) else pj.w_now * c.close + (1.0 - pj.w_now) * pj.limit
            pj.tp = pj.entry + d * pj.tgt
            pj.rr = pj.tgt / ((pj.entry - pj.sl) * d)
            pj.ev = pj.w_now * e_n + (1.0 - pj.w_now) * e_w
            if pj.w_now >= 0.8:
                pj.mode = "enter now"
            elif pj.w_now <= 0.2:
                pj.mode = f"wait for {pj.limit:.6g}"
            else:
                pj.mode = "split"
        return pj

    # ------------------------------------------------------------------ validation
    def validation(self) -> dict:
        out = {}
        for name, e in self.engines().items():
            out[name] = {"trades": e.trades, "wins": e.wins, "winrate": e.winrate,
                         "expectancy": e.expectancy, "sum_r": e.sum_r}
        out["calibration"] = [
            {"bin": f"{b/5:.1f}-{(b+1)/5:.1f}", "n": self.cal_n[b],
             "real": (self.cal_hit[b] / self.cal_n[b]) if self.cal_n[b] else None}
            for b in range(5)
        ]
        out["precursor"] = {"probe": (self.pc_hit / self.pc_n) if self.pc_n else None,
                            "base": (self.bs_hit / self.bs_n) if self.bs_n else None, "n": self.pc_n}
        out["pattern_acc"] = {"life": (self.acc_hit / self.acc_n) if self.acc_n else None,
                              "roll": (sum(self.acc_roll) / len(self.acc_roll)) if self.acc_roll else None}
        out["buy_hold_pct"] = ((self.closes[-1] / self.bh0 - 1) * 100) if (self.bh0 and self.closes) else None
        return out
