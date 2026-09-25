"""configuration, all env-driven so railway can set it without code changes.

nothing secret is ever logged. private keys only come from env.
indicator parameters map 1:1 to shepherd.pine inputs; see john_bot/shepherd.py.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from typing import List

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # dotenv is optional in prod
    pass

from .asherin import AsherinParams
from .shepherd import Params


def _b(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return default
    return v.strip().lower() in ("1", "true", "yes", "y", "on")


def _f(name: str, default: float) -> float:
    v = os.getenv(name)
    try:
        return float(v) if v not in (None, "") else default
    except ValueError:
        return default


def _i(name: str, default: int) -> int:
    v = os.getenv(name)
    try:
        return int(v) if v not in (None, "") else default
    except ValueError:
        return default


def _s(name: str, default: str) -> str:
    v = os.getenv(name)
    return v if v is not None and v != "" else default


def _from_env(cls, prefix: str):
    """every indicator input can be overridden from env, e.g. SH_SEG_MULT=1.8 or ASH_DOM_THRESH=2.5."""
    p = cls()
    for f in fields(cls):
        key = prefix + f.name.upper()
        cur = getattr(p, f.name)
        if isinstance(cur, bool):
            setattr(p, f.name, _b(key, cur))
        elif isinstance(cur, int):
            setattr(p, f.name, _i(key, cur))
        else:
            setattr(p, f.name, _f(key, cur))
    return p


def _params_from_env() -> Params:
    return _from_env(Params, "SH_")


def _ash_from_env() -> AsherinParams:
    return _from_env(AsherinParams, "ASH_")


@dataclass
class Config:
    # --- exchange / account ---
    private_key: str = field(default_factory=lambda: _s("HL_PRIVATE_KEY", ""))
    account_address: str = field(default_factory=lambda: _s("HL_ACCOUNT_ADDRESS", ""))
    testnet: bool = field(default_factory=lambda: _b("HL_TESTNET", False))
    # paper trading on live data by default. set DRY_RUN=false + a key to go live.
    dry_run: bool = field(default_factory=lambda: _b("DRY_RUN", True))

    # --- market ---
    symbols: List[str] = field(
        default_factory=lambda: [s.strip().upper() for s in _s("SYMBOLS", "KAS").split(",") if s.strip()]
    )
    # 1h by default: on 5m the round-trip cost is a large slice of a normal move,
    # and every engine tested lost there. 5m still works; the gate decides.
    interval: str = field(default_factory=lambda: _s("INTERVAL", "1h"))
    leverage: int = field(default_factory=lambda: _i("LEVERAGE", 2))
    cross_margin: bool = field(default_factory=lambda: _b("CROSS_MARGIN", False))
    # which simulated engine the bot follows: projection | framework | asherin
    engine: str = field(default_factory=lambda: _s("ENGINE", "projection").lower())
    # candles replayed at startup so the pattern memory and the validation
    # record exist before the first live decision (hyperliquid serves <= 5000)
    warmup_candles: int = field(default_factory=lambda: _i("WARMUP_CANDLES", 5000))

    # --- sizing / risk ---
    paper_equity: float = field(default_factory=lambda: _f("PAPER_EQUITY", 1000.0))
    risk_frac: float = field(default_factory=lambda: _f("RISK_FRAC", 0.01))
    max_position_frac: float = field(default_factory=lambda: _f("MAX_POSITION_FRAC", 0.20))
    # worst price the market entry will accept. realised slippage on liquid
    # perps is far smaller; this is only a protective limit.
    slippage: float = field(default_factory=lambda: _f("SLIPPAGE", 0.005))
    compound_frac: float = field(default_factory=lambda: _f("COMPOUND_FRAC", 0.40))
    min_sizing_base: float = field(default_factory=lambda: _f("MIN_SIZING_BASE", 10.0))
    max_sizing_base: float = field(default_factory=lambda: _f("MAX_SIZING_BASE", 0.0))

    # --- live-account circuit breakers (the simulated record is not affected) ---
    max_daily_loss_pct: float = field(default_factory=lambda: _f("MAX_DAILY_LOSS_PCT", 0.06))
    max_consecutive_losses: int = field(default_factory=lambda: _i("MAX_CONSECUTIVE_LOSSES", 4))
    loss_streak_pause_sec: int = field(default_factory=lambda: _i("LOSS_STREAK_PAUSE_SEC", 43200))
    max_drawdown_pct: float = field(default_factory=lambda: _f("MAX_DRAWDOWN_PCT", 0.15))

    # --- validation gate: real orders only when the record proves an edge ---
    require_edge: bool = field(default_factory=lambda: _b("REQUIRE_VALIDATED_EDGE", True))
    min_trades: int = field(default_factory=lambda: _i("MIN_VALIDATION_TRADES", 30))
    min_expectancy_r: float = field(default_factory=lambda: _f("MIN_EXPECTANCY_R", 0.05))
    min_t_stat: float = field(default_factory=lambda: _f("MIN_EDGE_T", 2.0))
    recent_trades: int = field(default_factory=lambda: _i("RECENT_TRADES", 30))
    baseline_min_trades: int = field(default_factory=lambda: _i("BASELINE_MIN_TRADES", 10))

    # --- discord ---
    discord_bot_token: str = field(default_factory=lambda: _s("DISCORD_BOT_TOKEN", ""))
    discord_channel_id: str = field(default_factory=lambda: _s("DISCORD_CHANNEL_ID", ""))
    discord_guild_id: str = field(default_factory=lambda: _s("DISCORD_GUILD_ID", ""))
    discord_webhook_url: str = field(default_factory=lambda: _s("DISCORD_WEBHOOK_URL", ""))
    discord_user_id: str = field(default_factory=lambda: _s("DISCORD_USER_ID", ""))

    # --- timing / persistence ---
    poll_seconds: int = field(default_factory=lambda: _i("POLL_SECONDS", 20))
    state_path: str = field(default_factory=lambda: _s("STATE_PATH", "state.json"))
    log_level: str = field(default_factory=lambda: _s("LOG_LEVEL", "INFO"))
    reset_id: str = field(default_factory=lambda: _s("RESET_ID", ""))

    # --- indicator ---
    params: Params = field(default_factory=_params_from_env)
    ash_params: AsherinParams = field(default_factory=_ash_from_env)

    @property
    def is_live(self) -> bool:
        return (not self.dry_run) and bool(self.private_key)

    @property
    def discord_bot_enabled(self) -> bool:
        return bool(self.discord_bot_token and self.discord_channel_id)

    def redacted(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in ("params", "ash_params")}
        d["private_key"] = "***set***" if self.private_key else "(none)"
        d["discord_bot_token"] = "***set***" if self.discord_bot_token else "(none)"
        d["discord_webhook_url"] = "***set***" if self.discord_webhook_url else "(none)"
        if d.get("account_address"):
            a = d["account_address"]
            d["account_address"] = a[:6] + "..." + a[-4:] if len(a) > 12 else "***"
        return d


CONFIG = Config()
