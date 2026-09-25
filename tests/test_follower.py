"""end-to-end: the paper bot must mirror the simulated engine it follows."""
import dataclasses

import pytest

from john_bot import engine as engine_mod
from john_bot.config import Config
from john_bot.shepherd import Params

from conftest import make_candles


class FakeMarket:
    def __init__(self, candles, start):
        self.all = candles
        self.i = start

    def interval_ms(self, interval):
        return 3_600_000

    def closed_candles(self, coin, interval, count):
        return self.all[max(0, self.i - count):self.i]

    def closed_since(self, coin, interval, after):
        return [c for c in self.all[:self.i] if c.open_time > after]

    def sz_decimals(self, coin):
        return 3

    def last_price(self, coin, interval):
        return self.all[self.i - 1].close


@pytest.fixture(params=["projection", "asherin"])
def bot(request, tmp_path, monkeypatch):
    cs = make_candles(2400, seed=11)
    fake = FakeMarket(cs, 1200)
    monkeypatch.setattr(engine_mod, "MarketData", lambda testnet=False: fake)
    monkeypatch.setattr(engine_mod.time, "time", lambda: (fake.all[fake.i - 1].close_time + 5) / 1000)
    cfg = dataclasses.replace(Config(), symbols=["TEST"], interval="1h", dry_run=True, private_key="",
                              state_path=str(tmp_path / "state.json"), paper_equity=100_000.0,
                              discord_bot_token="", discord_webhook_url="", warmup_candles=1200,
                              max_consecutive_losses=0, max_drawdown_pct=0, max_daily_loss_pct=0,
                              engine=request.param,
                              params=Params(min_mem=20))
    e = engine_mod.Engine(cfg)
    return e, fake


def test_paper_bot_mirrors_the_simulated_engine(bot):
    e, fake = bot
    sim = e.shepherds["TEST"].engines()[e.cfg.engine]
    sim_before = sim.trades
    while fake.i < len(fake.all):
        fake.i += 1
        e.tick()
    sim_trades = sim.trades - sim_before
    bot_trades = e.state.wins + e.state.losses
    assert sim_trades > 5
    # a trade already open in the sim at warmup end can't be mirrored, and the
    # last one may still be open — so allow a difference of two
    assert abs(bot_trades - sim_trades) <= 2
    sim_r = sim.history[sim_before:]
    bot_r = [t["r"] for t in e.state.trades]
    wins_sim = sum(1 for r in sim_r if r > 0)
    wins_bot = sum(1 for r in bot_r if r > 0)
    assert abs(wins_sim - wins_bot) <= 2
