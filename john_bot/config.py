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
            s.strip().upper() for s in _s("SYMBOLS", "KAS").split(",") if s.strip()
        ]
    )
    interval: str = field(default_factory=lambda: _s("INTERVAL", "5m"))
    leverage: int = field(default_factory=lambda: _i("LEVERAGE", 2))  # 2x: participate, don't be enslaved
    cross_margin: bool = field(default_factory=lambda: _b("CROSS_MARGIN", False))

    # --- sizing / risk ---
    paper_equity: float = field(default_factory=lambda: _f("PAPER_EQUITY", 1000.0))
    risk_frac: float = field(default_factory=lambda: _f("RISK_FRAC", 0.02))  # risk per trade at SL
    max_position_frac: float = field(default_factory=lambda: _f("MAX_POSITION_FRAC", 0.20))  # <=20% notional/trade
    slippage: float = field(default_factory=lambda: _f("SLIPPAGE", 0.005))  # 0.5%: patience over impatience

    # --- compounding ---
    # fraction of each *profit* that is compounded back into the sizing base.
    # the rest (1 - compound_frac) is "banked" (set aside, off the table).
    # losses come fully out of the sizing base.
    compound_frac: float = field(default_factory=lambda: _f("COMPOUND_FRAC", 0.40))
    min_sizing_base: float = field(default_factory=lambda: _f("MIN_SIZING_BASE", 10.0))
    # ceiling on how large the sizing base may grow via compounding. everything
    # above it is banked, not recycled into risk. 0 = no ceiling.
    max_sizing_base: float = field(default_factory=lambda: _f("MAX_SIZING_BASE", 0.0))
    # session-level circuit breaker: pause new entries once the day's realized
    # loss reaches this fraction of the sizing base.
    max_daily_loss_pct: float = field(default_factory=lambda: _f("MAX_DAILY_LOSS_PCT", 0.06))

    # --- discord ---
    discord_bot_token: str = field(default_factory=lambda: _s("DISCORD_BOT_TOKEN", ""))
    discord_channel_id: str = field(default_factory=lambda: _s("DISCORD_CHANNEL_ID", ""))
    discord_guild_id: str = field(default_factory=lambda: _s("DISCORD_GUILD_ID", ""))
    discord_webhook_url: str = field(default_factory=lambda: _s("DISCORD_WEBHOOK_URL", ""))
    discord_user_id: str = field(default_factory=lambda: _s("DISCORD_USER_ID", ""))

    # --- analysis windows (asherin.pine :: core windows) ---
    war_window: int = field(default_factory=lambda: _i("WAR_WINDOW", 7))       # warWin
    atr_lookback: int = field(default_factory=lambda: _i("ATR_LOOKBACK", 14))  # atrLen
    frame_len: int = field(default_factory=lambda: _i("FRAME_LEN", 50))        # frameLen
    normal_len: int = field(default_factory=lambda: _i("NORMAL_LEN", 50))      # normLen
    history_candles: int = field(default_factory=lambda: _i("HISTORY_CANDLES", 60))  # 5h of 5m data is enough

    # --- velocity / trend gates (asherin.pine :: velocity/trend) ---
    trend_thresh: float = field(default_factory=lambda: _f("TREND_THRESH", 0.25))  # trendThresh
    dom_thresh: float = field(default_factory=lambda: _f("DOM_THRESH", 2.0))       # domThresh (net force)

    # --- passion weights (asherin.pine :: passion) ---
    w_mag: float = field(default_factory=lambda: _f("W_MAG", 0.20))
    w_conf: float = field(default_factory=lambda: _f("W_CONF", 0.35))
    w_wick: float = field(default_factory=lambda: _f("W_WICK", 0.25))
    w_pers: float = field(default_factory=lambda: _f("W_PERS", 0.20))
    passion_thresh: float = field(default_factory=lambda: _f("PASSION_THRESH", 1.00))

    # --- entry overextension filters (validated trade-log pattern) ---
    passion_max: float = field(default_factory=lambda: _f("PASSION_MAX", 0.99))
    norm_vel_max: float = field(default_factory=lambda: _f("NORM_VEL_MAX", 0.49))
    net_force_max: float = field(default_factory=lambda: _f("NET_FORCE_MAX", 2.80))
    net_force_min: float = field(default_factory=lambda: _f("NET_FORCE_MIN", 2.00))
    reject_bouncing: bool = field(default_factory=lambda: _b("REJECT_BOUNCING", True))
    max_consecutive_losses: int = field(default_factory=lambda: _i("MAX_CONSECUTIVE_LOSSES", 3))
    max_drawdown_pct: float = field(default_factory=lambda: _f("MAX_DRAWDOWN_PCT", 0.10))

    # --- echo / normal rate (asherin.pine :: echo) ---
    echo_trigger: float = field(default_factory=lambda: _f("ECHO_TRIGGER", 1.6))
    echo_max: int = field(default_factory=lambda: _i("ECHO_MAX", 6))
    elong_mult: float = field(default_factory=lambda: _f("ELONG_MULT", 1.5))
    const_mult: float = field(default_factory=lambda: _f("CONST_MULT", 0.6))

    # --- stubborn / precursor / conflict (asherin.pine) ---
    stub_body: float = field(default_factory=lambda: _f("STUB_BODY", 0.25))
    prec_size: float = field(default_factory=lambda: _f("PREC_SIZE", 0.60))
    prec_count: int = field(default_factory=lambda: _i("PREC_COUNT", 3))
    conflict_thresh: float = field(default_factory=lambda: _f("CONFLICT_THRESH", 0.8))
    tap_tol: float = field(default_factory=lambda: _f("TAP_TOL", 0.25))
    piv_len: int = field(default_factory=lambda: _i("PIV_LEN", 3))

    # --- SL/TP shaping (asherin.pine :: trade) ---
    base_sl: float = field(default_factory=lambda: _f("BASE_SL", 2.0))
    base_tp: float = field(default_factory=lambda: _f("BASE_TP", 3.0))
    sl_sens: float = field(default_factory=lambda: _f("SL_SENS", 0.60))
    tp_sens: float = field(default_factory=lambda: _f("TP_SENS", 1.00))
    sl_floor: float = field(default_factory=lambda: _f("SL_FLOOR", 0.50))

    # --- timing / cooldown (narrative: cooldown = last trade duration) ---
    poll_seconds: int = field(default_factory=lambda: _i("POLL_SECONDS", 20))
    min_cooldown_sec: int = field(default_factory=lambda: _i("MIN_COOLDOWN_SEC", 1800))  # 30m minimum rest
    max_cooldown_sec: int = field(default_factory=lambda: _i("MAX_COOLDOWN_SEC", 14400))
    cooldown_mode: str = field(default_factory=lambda: _s("COOLDOWN_MODE", "trade_duration"))
    backtest_max_hold_bars: int = field(default_factory=lambda: _i("BACKTEST_MAX_HOLD_BARS", 288))
    backtest_slippage: float = field(default_factory=lambda: _f("BACKTEST_SLIPPAGE", 0.005))
    # trading-hours window (UTC). the algorithm rests outside it. 12 on / 12 off.
    session_start_utc_hour: int = field(default_factory=lambda: _i("SESSION_START_HOUR", 8))
    session_end_utc_hour: int = field(default_factory=lambda: _i("SESSION_END_HOUR", 20))

    # --- persistence ---
    state_path: str = field(default_factory=lambda: _s("STATE_PATH", "state.json"))
    log_level: str = field(default_factory=lambda: _s("LOG_LEVEL", "INFO"))
    # scoreboard reset: change RESET_ID to any new value and redeploy to wipe
    # pnl / win-rate / compounding once. leave it the same to keep history.
    reset_id: str = field(default_factory=lambda: _s("RESET_ID", ""))

    # --- self-reflection / self-improvement (reflect.py) ---
    # after each closed trade, once there are >= min_trades_to_learn trades, the
    # bot reviews its own history and may adopt a new entry filter (only ever
    # making entries pickier). set SELF_LEARN=false to keep it report-only.
    self_learn: bool = field(default_factory=lambda: _b("SELF_LEARN", False))  # explicit opt-in
    min_trades_to_learn: int = field(default_factory=lambda: _i("MIN_TRADES_TO_LEARN", 20))
    learn_margin: float = field(default_factory=lambda: _f("LEARN_MARGIN", 0.50))  # min R gain to adopt
    learn_keep_frac: float = field(default_factory=lambda: _f("LEARN_KEEP_FRAC", 0.75))  # keep >=75% of trades

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
