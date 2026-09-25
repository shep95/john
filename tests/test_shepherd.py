import math

from john_bot.shepherd import Params, Shepherd, _dtw, _Rolling

P = Params(min_mem=20)


def run(cs, p=P):
    sh = Shepherd(p)
    readings = [sh.update(c) for c in cs]
    return sh, readings


def fingerprint(r):
    pj = r.projection
    return (r.state, r.regime, r.dom_dir, r.echo_dir, r.gate, pj.call,
            None if math.isnan(pj.p_up) else round(pj.p_up, 12),
            tuple((e.kind, e.engine, e.dir, None if math.isnan(e.price) else round(e.price, 9)) for e in r.events))


def test_no_lookahead(candles):
    """a bar's reading must not change when future bars are appended."""
    n = 1800
    _, short = run(candles[:n])
    _, long = run(candles[:n + 400])
    assert [fingerprint(r) for r in short] == [fingerprint(r) for r in long[:n]]


def test_deterministic(candles):
    a, _ = run(candles)
    b, _ = run(candles)
    for name in ("framework", "projection", "momentum", "random"):
        assert a.engines()[name].history == b.engines()[name].history


def test_engine_produces_movements_memory_and_trades(candles):
    sh, readings = run(candles)
    assert len(sh.segs) > 50
    assert all(sh.segs[i].dir == -sh.segs[i + 1].dir for i in range(len(sh.segs) - 1))
    assert sh.mem_resolved >= P.min_mem
    assert sh.pj.trades > 0
    assert readings[-1].ready
    for r in sh.pj.history:
        assert math.isfinite(r)


def test_movement_is_confirmed_after_its_extreme(candles):
    """a movement's extreme bar is never later than the bar that confirmed it."""
    sh = Shepherd(P)
    for i, c in enumerate(candles[:1500]):
        last = sh.segs[-1] if sh.segs else None
        sh.update(c)
        if sh.segs and sh.segs[-1] is not last:
            assert sh.segs[-1].b1 <= i


def test_rolling_matches_pine_sum():
    r = _Rolling(3)
    assert r.push(1) is None
    assert r.push(2) is None
    assert r.push(3) == 6
    assert r.push(4) == 9


def test_dtw_identity_and_band():
    a = [1.0, -0.5, 1.2, -0.7, 1.0]
    rh = [0.1, -0.2, 0.0, 0.3, -0.2]
    assert _dtw(a, rh, a, rh, 0.5) == 0.0
    b = [x * -1 for x in a]
    assert _dtw(a, rh, b, rh, 0.5) > _dtw(a, rh, [x * 1.05 for x in a], rh, 0.5)


def test_projection_plan_is_consistent(candles):
    _, readings = run(candles)
    seen = 0
    for r in readings:
        pj = r.projection
        if pj.call != 0 and not pj.msg and not math.isnan(pj.entry):
            seen += 1
            d = pj.call
            assert (pj.entry - pj.sl) * d > 0, "stop on the wrong side"
            assert (pj.tp - pj.entry) * d > 0, "target on the wrong side"
            assert 0.0 <= pj.w_now <= 1.0
            if not math.isnan(pj.limit):
                assert (r.close - pj.limit) * d > 0, "limit must be better than price now"
    assert seen > 0
