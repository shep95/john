"""tiny json-file persistence so restarts (railway redeploys) keep stats, the
open trade and the circuit-breaker state. no secrets ever written here.
"""
from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field, fields
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
    r: float = 0.0
    risk_usd: float = 0.0


@dataclass
class BotState:
    trades: List[dict] = field(default_factory=list)
    realized_pnl: float = 0.0
    wins: int = 0
    losses: int = 0
    # compounding
    sizing_base: float = 0.0        # capital actually used for position sizing
    banked_reserve: float = 0.0     # profit set aside (the part not compounded)
    start_base: float = 0.0
    peak_base: float = 0.0
    # breakers
    paused: bool = False            # manual or drawdown pause — needs /resume
    pause_until: float = 0.0        # timed pause after a losing streak
    consecutive_losses: int = 0
    daily_loss_usd: float = 0.0
    daily_session_date: str = ""
    reset_id: str = ""
    open_position: Optional[dict] = None

    def record(self, t: TradeRecord) -> None:
        self.trades.append(asdict(t))
        self.trades = self.trades[-200:]
        self.realized_pnl += t.pnl
        if t.pnl > 0:
            self.wins += 1
        elif t.pnl < 0:
            self.losses += 1


def load_state(path: str) -> BotState:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
            known = {f.name for f in fields(BotState)}
            # older state files carry keys (rotation, learned rules) that no longer exist
            return BotState(**{k: v for k, v in d.items() if k in known})
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
