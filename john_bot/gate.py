"""the validation gate: real orders only when the record proves an edge.

the shepherd engine keeps a simulated book for every signal it would have taken,
with fixed parameters and costs included. the pattern memory only ever learns
from outcomes that already happened, so that record is walk-forward by
construction. this module reads it and answers one question: is there enough
evidence that following this engine, on this market and timeframe, beats doing
something dumb? if not, the bot keeps reading and paper-trading, and real money
stays out.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

from .shepherd import Shepherd, SimEngine


@dataclass
class GateResult:
    open: bool
    engine: str
    trades: int
    expectancy: Optional[float]
    t_stat: Optional[float]
    recent_expectancy: Optional[float]
    reasons: List[str] = field(default_factory=list)

    def summary(self) -> str:
        head = "OPEN" if self.open else "CLOSED"
        exp = "—" if self.expectancy is None else f"{self.expectancy:+.2f}R"
        t = "—" if self.t_stat is None else f"{self.t_stat:.1f}"
        rec = "—" if self.recent_expectancy is None else f"{self.recent_expectancy:+.2f}R"
        base = f"gate {head} · {self.engine} n={self.trades} exp={exp} t={t} recent={rec}"
        return base if self.open else base + " · " + "; ".join(self.reasons)


def _mean(xs: List[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def _t_stat(xs: List[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    if var <= 0:
        return math.inf if m > 0 else None
    return m / math.sqrt(var / n)


def evaluate(sh: Shepherd, cfg) -> GateResult:
    name = cfg.engine if cfg.engine in ("projection", "framework", "asherin") else "projection"
    eng: SimEngine = sh.engines()[name]
    hist = eng.history
    exp = _mean(hist)
    t = _t_stat(hist)
    recent = _mean(hist[-cfg.recent_trades:]) if hist else None
    reasons: List[str] = []

    if len(hist) < cfg.min_trades:
        reasons.append(f"only {len(hist)}/{cfg.min_trades} simulated trades")
    if exp is None or exp < cfg.min_expectancy_r:
        reasons.append(f"expectancy {('—' if exp is None else f'{exp:+.2f}R')} < {cfg.min_expectancy_r:+.2f}R")
    if t is None or t < cfg.min_t_stat:
        reasons.append(f"edge not distinguishable from noise (t={'—' if t is None else f'{t:.1f}'} < {cfg.min_t_stat})")
    if recent is None or recent <= 0:
        reasons.append("last trades not positive")
    for base in (sh.mo, sh.rn):
        if base.trades >= cfg.baseline_min_trades and exp is not None and base.expectancy is not None:
            if exp <= base.expectancy:
                reasons.append(f"does not beat {base.name} baseline ({base.expectancy:+.2f}R)")
    if sh.last is not None and sh.last.decayed:
        reasons.append("pattern layer degraded")

    ok = not reasons
    if not cfg.require_edge:
        ok = True
        reasons = ["REQUIRE_VALIDATED_EDGE=false (override)"] + reasons
    return GateResult(ok, name, len(hist), exp, t, recent, reasons)
