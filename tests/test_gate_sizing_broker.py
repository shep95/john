from types import SimpleNamespace

from john_bot.broker import PaperBroker
from john_bot.gate import evaluate
from john_bot.market import Candle
from john_bot.shepherd import Shepherd
from john_bot.sizing import build_plan


def gate_cfg(**kw):
    base = dict(engine="projection", require_edge=True, min_trades=30, min_expectancy_r=0.05,
                min_t_stat=1.5, recent_trades=30, baseline_min_trades=10)
    base.update(kw)
    return SimpleNamespace(**base)


def sh_with(hist, mo=None, rn=None):
    sh = Shepherd()
    sh.pj.history = list(hist)
    sh.pj.trades = len(hist)
    sh.pj.sum_r = sum(hist)
    for eng, h in ((sh.mo, mo or []), (sh.rn, rn or [])):
        eng.history, eng.trades, eng.sum_r = list(h), len(h), sum(h)
    return sh


def test_gate_closed_without_enough_trades():
    g = evaluate(sh_with([1.0] * 10), gate_cfg())
    assert not g.open and any("simulated trades" in r for r in g.reasons)


def test_gate_closed_on_noise():
    g = evaluate(sh_with([1.2, -1.0] * 20), gate_cfg())
    assert not g.open


def test_gate_open_on_clear_edge_that_beats_baselines():
    hist = [1.5, -1.0, 1.5, 1.5, -1.0] * 8
    g = evaluate(sh_with(hist, mo=[-0.2] * 12, rn=[-0.3] * 12), gate_cfg())
    assert g.open, g.reasons


def test_gate_closed_when_a_baseline_does_better():
    hist = [1.5, -1.0, 1.5, 1.5, -1.0] * 8
    g = evaluate(sh_with(hist, rn=[1.0] * 12), gate_cfg())
    assert not g.open and any("random" in r for r in g.reasons)


def test_gate_override():
    g = evaluate(sh_with([]), gate_cfg(require_edge=False))
    assert g.open


def size_cfg(**kw):
    base = dict(risk_frac=0.01, leverage=2, max_position_frac=0.2)
    base.update(kw)
    return SimpleNamespace(**base)


def test_sizing_risks_the_fraction_and_respects_the_cap():
    p = build_plan("X", 1, 100.0, 99.0, 103.0, 10_000, 3, size_cfg(max_position_frac=1.0))
    assert abs(p.risk_usd - 100.0) < 1.0  # 1% of 10k
    capped = build_plan("X", 1, 100.0, 99.9, 103.0, 10_000, 3, size_cfg())
    assert capped.notional <= 10_000 * 2 * 0.2 + 1e-6


def test_sizing_rejects_bad_levels_and_dust():
    assert build_plan("X", 1, 100.0, 101.0, 103.0, 10_000, 3, size_cfg()) is None
    assert build_plan("X", -1, 100.0, 99.0, 97.0, 10_000, 3, size_cfg()) is None
    assert build_plan("X", 1, 100.0, 99.0, 103.0, 20, 3, size_cfg()) is None  # under $10 notional


def c(o, h, l, cl, t=0):
    return Candle(t, t + 1, o, h, l, cl, 1.0, 1)


def paper():
    return PaperBroker(SimpleNamespace(paper_equity=1000.0))


def test_paper_stop_first_when_both_touched():
    b = paper()
    b.open_market(build_plan("X", 1, 100, 99, 102, 1000, 3, size_cfg(risk_frac=0.01, max_position_frac=1)), 100)
    out = b.on_candle("X", c(100, 103, 98, 101))
    assert out and out[0].reason == "SL" and out[0].pnl < 0


def test_paper_limit_fill_then_same_candle_stop_counts_as_loss():
    b = paper()
    plan = build_plan("X", 1, 99.5, 98.5, 102, 1000, 3, size_cfg(max_position_frac=1), kind="limit")
    b.place_limit(plan)
    out = b.on_candle("X", c(100, 100, 98.0, 99))
    assert out and out[0].reason == "SL"
    assert b.any_position() is None


def test_paper_limit_cancelled_when_gapping_past_stop():
    b = paper()
    b.place_limit(build_plan("X", 1, 99.5, 98.5, 102, 1000, 3, size_cfg(max_position_frac=1), kind="limit"))
    assert b.on_candle("X", c(98.0, 98.2, 97.5, 98.1)) == []
    assert b.pending() is None and b.any_position() is None
