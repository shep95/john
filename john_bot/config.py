"""configuration, all env-driven so railway can set it without code changes.

nothing secret is ever logged. private keys only come from env.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # dotenv is optional in prod
    pass


def _b(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


def _f(name: str, default: float) -> float:
    v = os.getenv(name)
    try:
        return float(v) if v is not None else default
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    v = os.getenv(name)
    try:
        return int(v) if v is not None else default
    except ValueError:
        return default


def _s(name: str, default: str) -> str:
    v = os.getenv(name)
    return v if v is not None and v != "" else default


@dataclass
class Config:
    # --- exchange / account ---
    private_key: str = field(default_factory=lambda: _s("HL_PRIVATE_KEY", ""))
    account_address: str = field(default_factory=lambda: _s("HL_ACCOUNT_ADDRESS", ""))
    testnet: bool = field(default_factory=lambda: _b("HL_TESTNET", False))
    # dry_run = paper trading on live data. defaults ON so it never trades real
    # money by accident. set DRY_RUN=false + supply a key to go live.
    dry_run: bool = field(default_factory=lambda: _b("DRY_RUN", True))

    # --- market / rotation ---
    symbols: List[str] = field(
        default_factory=lambda: [
            s.strip().upper() for s in _s("SYMBOLS", "ETH,DOGE").split(",") if s.strip()
        ]
    )
    interval: str = field(default_factory=lambda: _s("INTERVAL", "5m"))
    leverage: int = field(default_factory=lambda: _i("LEVERAGE", 5))
    cross_margin: bool = field(default_factory=lambda: _b("CROSS_MARGIN", False))

    # --- sizing / risk ---
    paper_equity: float = field(default_factory=lambda: _f("PAPER_EQUITY", 1000.0))
    risk_frac: float = field(default_factory=lambda: _f("RISK_FRAC", 0.02))  # risk per trade at SL
    max_position_frac: float = field(default_factory=lambda: _f("MAX_POSITION_FRAC", 0.5))
    slippage: float = field(default_factory=lambda: _f("SLIPPAGE", 0.02))

    # --- compounding ---
    # fraction of each *profit* that is compounded back into the sizing base.
    # the rest (1 - compound_frac) is "banked" (set aside, off the table).
    # losses come fully out of the sizing base.
    compound_frac: float = field(default_factory=lambda: _f("COMPOUND_FRAC", 0.40))
    min_sizing_base: float = field(default_factory=lambda: _f("MIN_SIZING_BASE", 10.0))

    # --- discord ---
    discord_bot_token: str = field(default_factory=lambda: _s("DISCORD_BOT_TOKEN", ""))
    discord_channel_id: str = field(default_factory=lambda: _s("DISCORD_CHANNEL_ID", ""))
    discord_guild_id: str = field(default_factory=lambda: _s("DISCORD_GUILD_ID", ""))
    discord_webhook_url: str = field(default_factory=lambda: _s("DISCORD_WEBHOOK_URL", ""))
    discord_user_id: str = field(default_factory=lambda: _s("DISCORD_USER_ID", ""))

    # --- analysis windows (narrative sections 4 & 9) ---
    war_window: int = field(default_factory=lambda: _i("WAR_WINDOW", 7))  # ~6-7 candle micro-war
    atr_lookback: int = field(default_factory=lambda: _i("ATR_LOOKBACK", 14))
    history_candles: int = field(default_factory=lambda: _i("HISTORY_CANDLES", 120))

    # --- signal gates (narrative sections 5, 7, 17) ---
    dominance_threshold: float = field(default_factory=lambda: _f("DOMINANCE_THRESHOLD", 0.35))
    echo_min_len: int = field(default_factory=lambda: _i("ECHO_MIN_LEN", 2))
    conflict_ceiling: float = field(default_factory=lambda: _f("CONFLICT_CEILING", 0.72))
    passion_min: float = field(default_factory=lambda: _f("PASSION_MIN", 0.0))

    # --- SL/TP shaping (narrative: passion + echo guide SL/TP) ---
    sl_atr_base: float = field(default_factory=lambda: _f("SL_ATR_BASE", 1.6))
    tp_atr_base: float = field(default_factory=lambda: _f("TP_ATR_BASE", 1.6))
    # conviction (passion+echo) shrinks SL and expands TP -> "lower SL, higher TP"
    sl_conviction_shrink: float = field(default_factory=lambda: _f("SL_CONVICTION_SHRINK", 0.45))
    tp_conviction_grow: float = field(default_factory=lambda: _f("TP_CONVICTION_GROW", 1.6))
    min_sl_atr: float = field(default_factory=lambda: _f("MIN_SL_ATR", 0.8))

    # --- timing / cooldown (narrative: cooldown = last trade duration) ---
    poll_seconds: int = field(default_factory=lambda: _i("POLL_SECONDS", 20))
    min_cooldown_sec: int = field(default_factory=lambda: _i("MIN_COOLDOWN_SEC", 300))
    max_cooldown_sec: int = field(default_factory=lambda: _i("MAX_COOLDOWN_SEC", 14400))
    cooldown_mode: str = field(default_factory=lambda: _s("COOLDOWN_MODE", "trade_duration"))

    # --- persistence ---
    state_path: str = field(default_factory=lambda: _s("STATE_PATH", "state.json"))
    log_level: str = field(default_factory=lambda: _s("LOG_LEVEL", "INFO"))

    @property
    def is_live(self) -> bool:
        return (not self.dry_run) and bool(self.private_key)

    @property
    def discord_bot_enabled(self) -> bool:
        return bool(self.discord_bot_token and self.discord_channel_id)

    def redacted(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        d["private_key"] = "***set***" if self.private_key else "(none)"
        d["discord_bot_token"] = "***set***" if self.discord_bot_token else "(none)"
        d["discord_webhook_url"] = "***set***" if self.discord_webhook_url else "(none)"
        if d.get("account_address"):
            a = d["account_address"]
            d["account_address"] = a[:6] + "..." + a[-4:] if len(a) > 12 else "***"
        return d


CONFIG = Config()
