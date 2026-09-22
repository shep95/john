from types import SimpleNamespace

from john_bot.analysis import LONG, NO_TRADE, analyze
from john_bot.market import Candle


def cfg(**overrides):
    values = dict(
        atr_lookback=3, war_window=3, frame_len=5, normal_len=5,
        trend_thresh=0.01, dom_thresh=0.5,
        w_mag=0.20, w_conf=0.35, w_wick=0.25, w_pers=0.20,
        passion_thresh=1.0, passion_max=0.99, norm_vel_max=0.49,
        net_force_min=0.5, net_force_max=2.8,
        echo_trigger=1.2, echo_max=6, stub_body=0.25,
        conflict_thresh=0.8, reject_bouncing=True,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


def candles_from_rows(rows):
    return [Candle(open_time=i, close_time=i + 1, open=o, high=h, low=l,
                   close=c, volume=1, trades=1)
            for i, (o, h, l, c) in enumerate(rows)]


def test_analysis_returns_no_trade_when_warmup_is_incomplete():
    read = analyze("TEST", candles_from_rows([(1, 1.1, .9, 1.05)]), cfg())
    assert read.signal == NO_TRADE
    assert "warming up" in read.reason


def test_overextension_limits_are_configurable():
    rows = [(100, 101, 99, 100.5)] * 5 + [
        (100.5, 104, 100, 103.5),
        (103.5, 104, 102, 103.8),
        (103.8, 104, 103, 103.9),
        (103.9, 106, 103, 105.5),
    ]
    read = analyze("TEST", candles_from_rows(rows), cfg(passion_max=0.01))
    assert read.signal == NO_TRADE
    assert "veto" in read.reason
