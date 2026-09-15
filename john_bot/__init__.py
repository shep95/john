"""john algro bot.

a non-ai, rules-based futures trading bot that turns john's chart narrative
(movement + context + velocity = meaning) into a concrete algorithm and trades
it on hyperliquid with 5x leverage on a 5m chart, rotating eth <-> doge.

the narrative -> code mapping lives in john_bot/analysis.py. every trade
decision is deterministic math over the last N closed candles. no model,
no learning at runtime -- just the primitives john described.
"""

__version__ = "0.1.0"
