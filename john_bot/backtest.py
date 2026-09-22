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
from datetime import datetime, timezone
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
    r: float


def simulate(symbol: str, candles: List[Candle], cfg) -> List[SimTrade]:
    """simulate the live timing conservatively: signal on closed bar, enter at
    next open with slippage, charge both fees, enforce cooldown and breakers."""
    trades: List[SimTrade] = []
    warmup = max(cfg.war_window, cfg.atr_lookback, cfg.frame_len, cfg.normal_len) + 2
    i = warmup
    equity = cfg.paper_equity
    peak = equity
    consecutive_losses = 0
    cooldown_until = 0
    interval_ms = 300_000
    max_hold = max(1, cfg.backtest_max_hold_bars)
    while i < len(candles) - 1:
        if i < cooldown_until:
            i += 1
            continue
        read = analyze(symbol, candles[:i + 1], cfg)
        if read.signal == NO_TRADE:
            i += 1
            continue
        entry_bar = i + 1
        plan = build_plan(read, equity, 4, cfg)
        if plan is None:
            i += 1
            continue
        raw_entry = candles[entry_bar].open
        slip = cfg.backtest_slippage
        entry = raw_entry * (1 + slip if plan.is_buy else 1 - slip)
        direction = 1 if plan.is_buy else -1
        sl_dist = abs(plan.entry_ref - plan.stop_loss)
        tp_dist = abs(plan.take_profit - plan.entry_ref)
        sl = entry - sl_dist if plan.is_buy else entry + sl_dist
        tp = entry + tp_dist if plan.is_buy else entry - tp_dist
        risk = plan.size * sl_dist
        if risk <= 0:
            i += 1
            continue
        entry_fee = entry * plan.size * 0.00045
        equity -= entry_fee
        reason = None
        exit_px = None
        exit_bar = min(len(candles) - 1, entry_bar + max_hold)
        # the entry fill occurs at the next bar open; protective orders can only
        # be evaluated after that fill, never against the same open-price event.
        for j in range(entry_bar + 1, exit_bar + 1):
            c = candles[j]
            if plan.is_buy:
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
                exit_bar = j
                break
        if reason is None:
            exit_px = candles[exit_bar].close
            reason = "TIME"
        assert exit_px is not None
        exit_fee = exit_px * plan.size * 0.00045
        gross = (exit_px - entry) * plan.size * direction
        pnl = gross - entry_fee - exit_fee
        r = pnl / risk if risk > 0 else 0.0
        result = "TP" if reason == "TP" and r > 0 else ("SL" if reason == "SL" and r < 0 else reason)
        trades.append(SimTrade(plan.side, entry, exit_px, result, exit_bar - i, r))
        equity = max(cfg.min_sizing_base, equity + pnl)
        peak = max(peak, equity)
        consecutive_losses = consecutive_losses + 1 if pnl < 0 else 0
        drawdown = (peak - equity) / peak if peak else 0.0
        if (consecutive_losses >= cfg.max_consecutive_losses or
                drawdown >= cfg.max_drawdown_pct):
            break
        cooldown_bars = max(1, int(cfg.min_cooldown_sec * 1000 / interval_ms))
        duration_bars = max(1, exit_bar - entry_bar)
        if cfg.cooldown_mode == "trade_duration":
            cooldown_bars = max(cooldown_bars, duration_bars)
        cooldown_until = exit_bar + cooldown_bars + 1
        i = exit_bar + 1
    return trades


def report(symbol: str, candles: List[Candle], cfg) -> None:
    trades = simulate(symbol, candles, cfg)
    n = len(trades)
    wins = sum(1 for t in trades if t.r > 0)
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


def _utc_ms(value: str) -> int:
    return int(datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp() * 1000)


def main() -> None:
    args = sys.argv[1:]
    count = 500
    symbols = CONFIG.symbols
    start_ms = end_ms = None
    if args and args[0] == "between":
        if len(args) < 3:
            raise SystemExit("usage: python -m john_bot.backtest between YYYY-MM-DD YYYY-MM-DD [SYMBOL ...]")
        start_ms = _utc_ms(args[1])
        # end date is inclusive through the final millisecond of that UTC day.
        end_ms = _utc_ms(args[2]) + 86_400_000 - 1
        symbols = [s.upper() for s in args[3:]] or CONFIG.symbols
    elif args:
        if args[0].isdigit():
            count = int(args[0])
        else:
            symbols = [args[0].upper()]
            if len(args) > 1 and args[1].isdigit():
                count = int(args[1])
    md = MarketData(testnet=CONFIG.testnet)
    for sym in symbols:
        candles = (md.candles_between(sym, CONFIG.interval, start_ms, end_ms)  # type: ignore[arg-type]
                   if start_ms is not None and end_ms is not None else md.closed_candles(sym, CONFIG.interval, count))
        report(sym, candles, CONFIG)


if __name__ == "__main__":
    main()
