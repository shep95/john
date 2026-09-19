"""self-reflection / self-improvement loop.

after enough trades, the bot reviews its OWN history, finds the pattern in its
losses, proposes a new entry filter, checks that the filter would actually have
improved results on the past trades, and -- only if the improvement clears a
real bar -- adopts it. this is john's "pattern creator" (narrative 22-26): the
bot criticises its own decisions and evolves its own rules.

hard guardrails (so a live-money bot can't hurt itself):
  * it only learns after MIN_TRADES_TO_LEARN real trades (no tuning on noise).
  * learned rules can ONLY make entries pickier (add a filter) -- never loosen
    a stop, widen risk, or disable a safety check.
  * every proposed rule must beat the current results by LEARN_MARGIN (in R) AND
    keep at least LEARN_KEEP_FRAC of the trades -- so it can't overfit by
    throwing away everything.
  * every adopted rule is logged + alerted, and RESET_ID wipes them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


def _won(t: dict) -> bool:
    return float(t.get("pnl", 0.0) or 0.0) > 0


def _r_multiple(t: dict) -> float:
    """realized reward:risk based on ACTUAL pnl vs the $ risked at the stop --
    so slippage/fees on wins and worse-than-planned stops on losses are counted
    honestly. falls back to the idealised rr/-1 only when risk wasn't recorded."""
    pnl = float(t.get("pnl", 0.0) or 0.0)
    risk = float(t.get("risk_usd", 0.0) or 0.0)
    if risk > 0:
        return pnl / risk
    if pnl > 0:
        return float(t.get("rr", 1.0) or 1.0)
    if pnl < 0:
        return -1.0
    return 0.0


def _stats(trades: List[dict]) -> Tuple[int, float, float]:
    """(n, win_rate 0-100, expectancy in R)."""
    n = len(trades)
    if n == 0:
        return 0, 0.0, 0.0
    wins = sum(1 for t in trades if _won(t))
    exp = sum(_r_multiple(t) for t in trades) / n
    return n, wins / n * 100.0, exp


# each learnable filter: (state key, feature key on the trade, direction, bounds)
#   direction "min" -> keep trades whose feature >= threshold
#   direction "max" -> keep trades whose feature <= threshold
_FILTERS = [
    ("min_strength", "strength", "min", 0.0, 0.9),
    ("max_conflict", "conflict", "max", 0.3, 1.0),
    ("min_abs_net_force", "abs_net_force", "min", 0.0, 6.0),
    ("min_echo_len", "echo_len", "min", 0.0, 5.0),
]


def _feature(t: dict, key: str) -> float:
    if key == "abs_net_force":
        return abs(float(t.get("net_force", 0.0) or 0.0))
    return float(t.get(key, 0.0) or 0.0)


@dataclass
class Proposal:
    key: str
    threshold: float
    kept: int
    dropped: int
    base_winrate: float
    new_winrate: float
    base_exp: float
    new_exp: float

    def human(self) -> str:
        word = {
            "min_strength": "require strength ≥ %.2f" % self.threshold,
            "max_conflict": "skip when conflict > %.2f" % self.threshold,
            "min_abs_net_force": "require |netForce| ≥ %.2f" % self.threshold,
            "min_echo_len": "require echo length ≥ %d" % int(self.threshold),
        }.get(self.key, f"{self.key} = {self.threshold:.2f}")
        return (f"{word}  (win-rate {self.base_winrate:.0f}%→{self.new_winrate:.0f}%, "
                f"expectancy {self.base_exp:+.2f}R→{self.new_exp:+.2f}R, "
                f"keeps {self.kept}/{self.kept + self.dropped} trades)")


@dataclass
class Reflection:
    n: int
    winrate: float
    expectancy: float
    wins: int
    losses: int
    worst_pattern: str
    proposal: Optional[Proposal]
    summary: str


def _best_threshold(trades, feat_key, direction, lo, hi, min_keep):
    """sweep the observed feature values, return (threshold, kept_trades) that
    maximises the kept subset's expectancy while keeping >= min_keep trades."""
    vals = sorted({_feature(t, feat_key) for t in trades})
    best = None
    for th in vals:
        if direction == "min":
            kept = [t for t in trades if _feature(t, feat_key) >= th]
        else:
            kept = [t for t in trades if _feature(t, feat_key) <= th]
        if len(kept) < min_keep:
            continue
        _, _, exp = _stats(kept)
        if best is None or exp > best[2]:
            best = (th, kept, exp)
    if best is None:
        return None
    th, kept, _ = best
    th = max(lo, min(hi, th))
    return th, kept


def reflect(trades: List[dict], cfg) -> Reflection:
    """analyse the trade history and, if warranted, return a proposed new rule."""
    n, winrate, expectancy = _stats(trades)
    wins = sum(1 for t in trades if _won(t))
    losses = n - wins

    if n < cfg.min_trades_to_learn:
        return Reflection(
            n, winrate, expectancy, wins, losses,
            worst_pattern="(not enough trades to judge yet)",
            proposal=None,
            summary=f"{n}/{cfg.min_trades_to_learn} trades — collecting data before self-tuning.",
        )

    min_keep = max(5, int(cfg.learn_keep_frac * n))

    # find, across every candidate filter, the single change that most improves
    # expectancy while clearing the guardrails.
    best_prop: Optional[Proposal] = None
    for key, feat_key, direction, lo, hi in _FILTERS:
        found = _best_threshold(trades, feat_key, direction, lo, hi, min_keep)
        if not found:
            continue
        th, kept = found
        kn, kwr, kexp = _stats(kept)
        if kn == n:  # filter drops nothing -> no learning
            continue
        if kexp - expectancy < cfg.learn_margin:  # not enough improvement
            continue
        if kwr < winrate:  # must not worsen the win-rate
            continue
        prop = Proposal(key=key, threshold=th, kept=kn, dropped=n - kn,
                        base_winrate=winrate, new_winrate=kwr,
                        base_exp=expectancy, new_exp=kexp)
        if best_prop is None or prop.new_exp > best_prop.new_exp:
            best_prop = prop

    # describe the worst recurring mistake (biggest expectancy gap of a feature)
    worst = _describe_worst(trades)

    if best_prop is None:
        summary = (f"reviewed {n} trades — win-rate {winrate:.0f}%, expectancy "
                   f"{expectancy:+.2f}R. no rule change clears the bar; current rules hold.")
    else:
        summary = (f"reviewed {n} trades — win-rate {winrate:.0f}%, expectancy "
                   f"{expectancy:+.2f}R. proposing: {best_prop.human()}")
    return Reflection(n, winrate, expectancy, wins, losses, worst, best_prop, summary)


def _describe_worst(trades: List[dict]) -> str:
    """plain-language note on where the losses cluster."""
    losers = [t for t in trades if not _won(t)]
    if not losers:
        return "no losses to learn from."
    # which feature best separates losers (highest avg conflict / lowest strength)
    avg_conf_loss = sum(_feature(t, "conflict") for t in losers) / len(losers)
    avg_str_loss = sum(_feature(t, "strength") for t in losers) / len(losers)
    winners = [t for t in trades if _won(t)]
    avg_conf_win = (sum(_feature(t, "conflict") for t in winners) / len(winners)) if winners else 0.0
    avg_str_win = (sum(_feature(t, "strength") for t in winners) / len(winners)) if winners else 0.0
    notes = []
    if avg_conf_loss > avg_conf_win + 0.05:
        notes.append(f"losses have higher conflict ({avg_conf_loss:.2f} vs {avg_conf_win:.2f} on wins)")
    if avg_str_loss < avg_str_win - 0.05:
        notes.append(f"losses have lower strength ({avg_str_loss:.2f} vs {avg_str_win:.2f} on wins)")
    return "; ".join(notes) if notes else "losses look similar to wins (no obvious tell yet)."
