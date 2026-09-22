from types import SimpleNamespace

from john_bot.backtest import simulate
from john_bot.market import Candle


def test_simtrade_shape_and_empty_data_are_safe():
    cfg = SimpleNamespace(war_window=3, atr_lookback=3, frame_len=5,
                          normal_len=5, paper_equity=1000.0,
                          backtest_max_hold_bars=2, backtest_slippage=0.0,
                          min_sizing_base=10.0, max_consecutive_losses=3,
                          max_drawdown_pct=0.10, min_cooldown_sec=1800,
                          cooldown_mode="trade_duration")
    assert simulate("TEST", [], cfg) == []


def test_candle_fixture_is_closed():
    candle = Candle(0, 1, 100, 101, 99, 100.5, 1.0, 1)
    assert candle.close > candle.open
    assert candle.rng == 2
