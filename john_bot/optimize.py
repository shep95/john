"""bounded walk-forward optimizer for passion weights and entry thresholds.

this is research-only. it never changes environment variables, state, or live
configuration. a candidate must preserve a meaningful trade count and improve
validation expectancy without violating risk limits.

usage:
  python -m john_bot.optimize between 2026-09-04 2026-09-21 KAS
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from itertools import product
from typing import Iterable, List, Tuple

from .analysis import LONG, NO_TRADE, analyze
from .backtest import SimTrade, simulate
from .config import CONFIG
from .market import Candle, MarketData
from .strategy import build_plan


@dataclass(frozen=True)
class Candidate:
    w_mag: float
    w_conf: float
    w_wick: float
    w_pers: float
    passion_max: float
    norm_vel_max: float
    net_force_min: float
    net_force_max: float

    def config(self, base):
        return replace(base, w_mag=self.w_mag, w_conf=self.w_conf,
                       w_wick=self.w_wick, w_pers=self.w_pers,
                       passion_max=self.passion_max, norm_vel_max=self.norm_vel_max,
                       net_force_min=self.net_force_min, net_force_max=self.net_force_max)

    def label(self) -> str:
        return (f"weights={self.w_mag:.2f}/{self.w_conf:.2f}/{self.w_wick:.2f}/{self.w_pers:.2f} "
                f"gates={self.passion_max:.2f}/{self.norm_vel_max:.2f}/"
                f"{self.net_force_min:.2f}-{self.net_force_max:.2f}")


@dataclass
class Score:
    trades: int
    wins: int
    total_r: float
    max_drawdown_r: float

    @property
    def winrate(self) -> float:
        return self.wins / self.trades * 100 if self.trades else 0.0

    @property
    def expectancy(self) -> float:
        return self.total_r / self.trades if self.trades else 0.0



def score(trades: Iterable[SimTrade]) -> Score:
    ts = list(trades)
    running = peak = drawdown = wins = total = 0.0
    for t in ts:
        wins += t.r > 0
        total += t.r
        running += t.r
        peak = max(peak, running)
        drawdown = max(drawdown, peak - running)
    return Score(len(ts), int(wins), total, drawdown)


def candidates() -> Iterable[Candidate]:
    # bounded, interpretable hypotheses; all weight vectors sum to 1.
    weights = [
        (0.20, 0.35, 0.25, 0.20),
        (0.20, 0.20, 0.25, 0.35),
        (0.25, 0.20, 0.25, 0.30),
        (0.25, 0.15, 0.30, 0.30),
        (0.30, 0.10, 0.30, 0.30),
    ]
    gates = [
        (0.99, 0.49, 2.00, 2.80),
        (0.90, 0.45, 2.00, 2.60),
        (0.99, 0.45, 2.20, 2.80),
    ]
    for w, gate in product(weights, gates):
        yield Candidate(*w, *gate)


def rank(train: Score, validation: Score, test: Score) -> Tuple[float, ...]:
    # a candidate is only desirable when it survives both unseen partitions.
    if validation.expectancy <= 0 or test.expectancy <= 0:
        return (-10_000.0, -10_000.0, -validation.max_drawdown_r, 0, 0.0)
    return (validation.expectancy, test.expectancy, -validation.max_drawdown_r,
            min(validation.trades, test.trades), validation.winrate)


def _utc_ms(value: str) -> int:
    return int(datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def main() -> None:
    args = sys.argv[1:]
    if len(args) < 3 or args[0] != "between":
        raise SystemExit("usage: python -m john_bot.optimize between YYYY-MM-DD YYYY-MM-DD SYMBOL")
    start = _utc_ms(args[1])
    end = _utc_ms(args[2]) + 86_400_000 - 1
    symbol = args[3].upper()
    candles = MarketData(testnet=CONFIG.testnet).candles_between(symbol, CONFIG.interval, start, end)
    if len(candles) < 300:
        raise SystemExit(f"not enough candles for walk-forward test: {len(candles)}")
    n = len(candles)
    train_end = int(n * 0.50)
    validation_end = int(n * 0.75)
    base = CONFIG
    results = []
    # Keep the grid intentionally small: each candidate still runs the exact
    # production analyzer, but one walk-forward pass is enough to reject
    # hypotheses that do not generalize.
    for candidate in candidates():
        cfg = candidate.config(base)
        train = score(simulate(symbol, candles[:train_end], cfg))
        validation = score(simulate(symbol, candles[train_end:validation_end], cfg))
        test = score(simulate(symbol, candles[validation_end:], cfg))
        if validation.trades >= 3 and test.trades >= 3:
            results.append((rank(train, validation, test), candidate, train, validation, test))
    results.sort(reverse=True, key=lambda x: x[0])
    print(f"candles={n} train={train_end} validation={validation_end-train_end} test={n-validation_end}")
    print(f"evaluated={len(list(candidates()))} accepted={len(results)}")
    for _, candidate, train, validation, test in results[:10]:
        print(f"{candidate.label()} | train {train.winrate:.1f}%/{train.expectancy:+.2f}R/{train.trades} "
              f"validation {validation.winrate:.1f}%/{validation.expectancy:+.2f}R/{validation.trades} "
              f"test {test.winrate:.1f}%/{test.expectancy:+.2f}R/{test.trades} "
              f"dd={test.max_drawdown_r:.2f}R")


if __name__ == "__main__":
    main()
