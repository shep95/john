"""replay real hyperliquid candles through the shepherd engine and print the
validation record — the same numbers the bot's gate reads before it is allowed
to trade real money.

usage:
  python -m john_bot.backtest                 # configured symbols, WARMUP_CANDLES
  python -m john_bot.backtest KAS 5000
  python -m john_bot.backtest KAS,ETH 5000 5m
"""
from __future__ import annotations

import sys
from dataclasses import replace

from .config import CONFIG
from .gate import evaluate
from .market import MarketData
from .shepherd import Shepherd


def _fmt(x, f):
    return "—" if x is None else format(x, f)


def report(sym: str, candles, cfg) -> None:
    sh = Shepherd(cfg.params, primary=cfg.engine, ap=cfg.ash_params)
    for c in candles:
        sh.update(c)
    v = sh.validation()
    print(f"\n=== {sym} {cfg.interval} | {len(candles)} candles | cost {cfg.params.cost_pct:.2f}% round trip ===")
    print(f"{'engine':<11}{'n':>5}{'win':>7}{'exp R':>9}{'sum R':>9}")
    for name in ("projection", "framework", "asherin", "momentum", "random"):
        x = v[name]
        print(f"{name:<11}{x['trades']:>5}{_fmt(x['winrate'] and x['winrate']*100, '6.0f'):>7}"
              f"{_fmt(x['expectancy'], '+9.2f'):>9}{x['sum_r']:>+9.2f}")
    print(f"buy & hold {_fmt(v['buy_hold_pct'], '+.1f')}%")
    print("calibration (predicted continuation → realized):")
    for c in v["calibration"]:
        print(f"  p {c['bin']}: n={c['n']:<4} real={_fmt(c['real'], '.2f')}")
    pc = v["precursor"]
    print(f"precursor: probe {_fmt(pc['probe'], '.2f')} vs base {_fmt(pc['base'], '.2f')} (n={pc['n']})")
    pa = v["pattern_acc"]
    print(f"pattern accuracy: life {_fmt(pa['life'], '.2f')} · rolling {_fmt(pa['roll'], '.2f')}")
    print(evaluate(sh, cfg).summary())
    r = sh.last
    if r is not None:
        pj = r.projection
        call = {1: "long", -1: "short"}.get(pj.call, "no call")
        print(f"now: {r.state_name} · {r.regime_name} · projection {call} {pj.mode or pj.msg} · gate {r.gate or 'clear'}")


def main() -> None:
    args = sys.argv[1:]
    symbols = [s.strip().upper() for s in args[0].split(",")] if args else CONFIG.symbols
    count = int(args[1]) if len(args) > 1 else CONFIG.warmup_candles
    cfg = replace(CONFIG, interval=args[2]) if len(args) > 2 else CONFIG
    md = MarketData(testnet=cfg.testnet)
    for sym in symbols:
        report(sym, md.closed_candles(sym, cfg.interval, count), cfg)


if __name__ == "__main__":
    main()
