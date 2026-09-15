"""market data: a candle model + the hyperliquid 5m feed.

a candle here is john's "observation of movement" (narrative section 1) --
we keep raw ohlcv and derive meaning later, never treating shape alone as truth.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Optional

import requests

MAINNET_INFO_URL = "https://api.hyperliquid.xyz/info"
TESTNET_INFO_URL = "https://api.hyperliquid-testnet.xyz/info"

_INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}


@dataclass(frozen=True)
class Candle:
    open_time: int  # ms
    close_time: int  # ms
    open: float
    high: float
    low: float
    close: float
    volume: float
    trades: int

    @property
    def body(self) -> float:
        return self.close - self.open

    @property
    def direction(self) -> int:
        if self.close > self.open:
            return 1
        if self.close < self.open:
            return -1
        return 0

    @property
    def rng(self) -> float:
        return max(self.high - self.low, 1e-12)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def abs_body(self) -> float:
        return abs(self.body)

    @classmethod
    def from_hl(cls, d: dict) -> "Candle":
        return cls(
            open_time=int(d["t"]),
            close_time=int(d["T"]),
            open=float(d["o"]),
            high=float(d["h"]),
            low=float(d["l"]),
            close=float(d["c"]),
            volume=float(d["v"]),
            trades=int(d.get("n", 0)),
        )


class MarketData:
    """pulls closed candles from hyperliquid's public info endpoint (keyless)."""

    def __init__(self, testnet: bool = False, timeout: float = 10.0):
        self.url = TESTNET_INFO_URL if testnet else MAINNET_INFO_URL
        self.timeout = timeout
        self._session = requests.Session()

    def interval_ms(self, interval: str) -> int:
        return _INTERVAL_MS.get(interval, 300_000)

    def _post(self, payload: dict, retries: int = 3) -> object:
        last_err: Optional[Exception] = None
        for attempt in range(retries):
            try:
                r = self._session.post(self.url, json=payload, timeout=self.timeout)
                r.raise_for_status()
                return r.json()
            except Exception as e:  # network / 429 / 5xx
                last_err = e
                time.sleep(0.6 * (attempt + 1))
        raise RuntimeError(f"hyperliquid info request failed: {last_err}")

    def candles(self, coin: str, interval: str, count: int) -> List[Candle]:
        """return the most recent `count` candles for `coin`, oldest -> newest.

        the last candle may still be forming; callers that need only *closed*
        candles should use `closed_candles`.
        """
        span = self.interval_ms(interval)
        end_ms = int(time.time() * 1000)
        start_ms = end_ms - span * (count + 2)
        payload = {
            "type": "candleSnapshot",
            "req": {"coin": coin, "interval": interval, "startTime": start_ms, "endTime": end_ms},
        }
        raw = self._post(payload)
        if not isinstance(raw, list):
            raise RuntimeError(f"unexpected candle response: {type(raw)}")
        candles = [Candle.from_hl(d) for d in raw]
        candles.sort(key=lambda c: c.open_time)
        return candles[-count:]

    def closed_candles(self, coin: str, interval: str, count: int) -> List[Candle]:
        """only fully-closed candles (drop the in-progress last one)."""
        span = self.interval_ms(interval)
        now = int(time.time() * 1000)
        cs = self.candles(coin, interval, count + 1)
        closed = [c for c in cs if c.close_time <= now]
        return closed[-count:]

    def last_price(self, coin: str, interval: str) -> float:
        cs = self.candles(coin, interval, 1)
        return cs[-1].close if cs else 0.0

    def meta(self) -> dict:
        return self._post({"type": "meta"})  # type: ignore[return-value]

    def sz_decimals(self, coin: str) -> int:
        try:
            m = self.meta()
            for a in m.get("universe", []):
                if a.get("name", "").upper() == coin.upper():
                    return int(a.get("szDecimals", 2))
        except Exception:
            pass
        return 2
