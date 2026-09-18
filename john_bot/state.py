"""tiny json-file persistence so restarts (railway redeploys) keep rotation,
cooldown, and trade stats. no secrets ever written here.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from typing import List, Optional


@dataclass
class TradeRecord:
    symbol: str
    side: str
    entry: float
    exit: float
    pnl: float
    reason: str
    opened_at: float
    closed_at: float
    duration_sec: float


@dataclass
class BotState:
    rotation_idx: int = 0
    cooldown_until: float = 0.0
    last_trade_duration: float = 0.0
    trades: List[dict] = field(default_factory=list)
    realized_pnl: float = 0.0
    wins: int = 0
    losses: int = 0
    # compounding
    sizing_base: float = 0.0        # capital actually used for position sizing
    banked_reserve: float = 0.0     # profit set aside (the 60% not compounded)
    start_base: float = 0.0         # first sizing_base, for roi
    paused: bool = False
    reset_id: str = ""              # matches cfg.reset_id; a mismatch wipes stats
    # the currently-open trade, persisted so a restart (railway redeploy) does
    # not lose track of a live position. None when flat. keys mirror the fields
    # a broker needs to resume settling: symbol, side, is_buy, size, entry,
    # stop_loss, take_profit, opened_at.
    open_position: Optional[dict] = None

    def record(self, t: TradeRecord) -> None:
        self.trades.append(asdict(t))
        self.trades = self.trades[-500:]
        self.realized_pnl += t.pnl
        # a win is real profit; a loss is real loss. an exact breakeven (after
        # fees, vanishingly rare) is counted as neither so it never inflates the
        # win column.
        if t.pnl > 0:
            self.wins += 1
        elif t.pnl < 0:
            self.losses += 1
        self.last_trade_duration = t.duration_sec


def load_state(path: str) -> BotState:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            return BotState(**d)
        except Exception:
            pass
    return BotState()


def save_state(path: str, state: BotState) -> None:
    try:
        d = os.path.dirname(os.path.abspath(path))
        fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(asdict(state), f, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass
