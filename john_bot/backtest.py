"""offline self-test / backtest.

pulls real recent 5m candles from hyperliquid (keyless) and walks the exact
same analysis + strategy forward, simulating TP/SL fills candle-by-candle.
this is how we "test the pattern" (narrative sections 16, 21, 26) before risking
anything. it also prints the current live read so you can eyeball the signal.

usage:
  python -m john_bot.backtest              # ETH + DOGE, 500 candles
  python -m john_bot.backtest ETH 1000
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import List

from .analysis import LONG, NO_TRADE, analyze
from .config import CONFIG
from .market import Candle, MarketData
from .strategy import build_plan


@dataclass
class SimTrade:
    side: str
    entry: float
    exit: float
    reason: str
    bars: int
    r: float  # realized reward:risk multiple (+tp_rr on win, -1 on loss)


def simulate(symbol: str, candles: List[Candle], cfg) -> List[SimTrade]:
    trades: List[SimTrade] = []
    warmup = max(cfg.war_window, cfg.atr_lookback) + 2
    i = warmup
    equity = cfg.paper_equity
    n = len(candles)
    while i < n - 1:
        read = analyze(symbol, candles[: i + 1], cfg)
        if read.signal == NO_TRADE:
            i += 1
            continue
        plan = build_plan(read, equity, 4, cfg)
        if plan is None:
            i += 1
            continue
        entry = plan.entry_ref
        sl, tp = plan.stop_loss, plan.take_profit
        is_long = plan.side == LONG
        # walk forward until a level is hit
        j = i + 1
        exit_px = None
        reason = None
        while j < n:
            c = candles[j]
            if is_long:
                if c.low <= sl:
                    exit_px, reason = sl, "SL"
                elif c.high >= tp:
                    exit_px, reason = tp, "TP"
            else:
                if c.high >= sl:
                    exit_px, reason = sl, "SL"
                elif c.low <= tp:
                    exit_px, reason = tp, "TP"
            if reason:
                break
            j += 1
        if reason is None:
            break  # ran out of data with an open trade
        r = plan.rr if reason == "TP" else -1.0
        trades.append(SimTrade(plan.side, entry, exit_px, reason, j - i, r))
        i = j + 1  # flat again after exit
    return trades


def report(symbol: str, candles: List[Candle], cfg) -> None:
    trades = simulate(symbol, candles, cfg)
    n = len(trades)
    wins = sum(1 for t in trades if t.reason == "TP")
    total_r = sum(t.r for t in trades)
    avg_bars = sum(t.bars for t in trades) / n if n else 0
    wr = (wins / n * 100) if n else 0.0
    print(f"\n=== {symbol} {cfg.interval} | {len(candles)} candles ===")
    print(f"trades={n}  wins={wins}  winrate={wr:.1f}%  total_R={total_r:+.2f}  "
          f"avg_hold={avg_bars:.1f} bars ({avg_bars*5:.0f}m)")
    if n:
        longs = sum(1 for t in trades if t.side == LONG)
        print(f"long={longs} short={n-longs}  "
              f"best={max(t.r for t in trades):+.2f}R worst={min(t.r for t in trades):+.2f}R")

    read = analyze(symbol, candles, cfg)
    print(f"live read -> {read.signal} | {read.reason}")
    print(f"  dom={read.dominance:+.2f} winner={read.winner} passion={read.passion:.2f} "
          f"conflict={read.conflict:.2f} echo(len={read.echo.length},dir={read.echo.direction:+d}) "
          f"atr={read.atr:.5g}")


def main() -> None:
    args = sys.argv[1:]
    count = 500
    symbols = CONFIG.symbols
    if args:
        if args[0].isdigit():
            count = int(args[0])
        else:
            symbols = [args[0].upper()]
            if len(args) > 1 and args[1].isdigit():
                count = int(args[1])
    md = MarketData(testnet=CONFIG.testnet)
    for sym in symbols:
        candles = md.closed_candles(sym, CONFIG.interval, count)
        report(sym, candles, CONFIG)


if __name__ == "__main__":
    main()
