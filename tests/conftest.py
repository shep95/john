import math
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from john_bot.market import Candle  # noqa: E402


def make_candles(n: int = 2500, seed: int = 7, start: float = 100.0, span_ms: int = 3_600_000):
    """a seeded random walk with shifting drift and volatility, so the engine
    sees trends, ranges and regime changes."""
    rnd = random.Random(seed)
    out, px, t = [], start, 1_700_000_000_000
    drift, vol = 0.0, 0.006
    for i in range(n):
        if i % 200 == 0:
            drift = rnd.uniform(-0.0015, 0.0015)
            vol = rnd.uniform(0.003, 0.012)
        o = px
        c = max(0.01, o * math.exp(rnd.gauss(drift, vol)))
        hi = max(o, c) * (1 + abs(rnd.gauss(0, vol / 2)))
        lo = min(o, c) * (1 - abs(rnd.gauss(0, vol / 2)))
        v = rnd.uniform(500, 1500) * (1 + 3 * abs(c - o) / o / vol)
        out.append(Candle(t, t + span_ms - 1, o, hi, lo, c, v, 100))
        px, t = c, t + span_ms
    return out


@pytest.fixture(scope="session")
def candles():
    return make_candles()
