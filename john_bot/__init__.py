"""john bot.

a rules-based perpetual-futures bot that runs the shepherd · movement indicator
(shepherd.pine) bar for bar in python and trades it on hyperliquid — but only
once the indicator's own simulated record on that market proves an edge.

no model, no runtime self-tuning. every decision is deterministic math over
closed candles. see john_bot/shepherd.py (the read) and john_bot/gate.py (the
permission to trade).
"""

__version__ = "0.2.0"
