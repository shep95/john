<div align="center">

# ◢ JOHN ◣

### `shepherd · movement` — the indicator, traded only when it has earned it

![python](https://img.shields.io/badge/python-3.12-0b0f19?style=for-the-badge&logo=python&logoColor=8fd3e6&labelColor=0b0f19)
![exchange](https://img.shields.io/badge/exchange-hyperliquid-0b0f19?style=for-the-badge&labelColor=0b0f19)
![deploy](https://img.shields.io/badge/deploy-railway-0b0f19?style=for-the-badge&logo=railway&logoColor=8fd3e6&labelColor=0b0f19)
<br>
![engine](https://img.shields.io/badge/engine-deterministic-8fd3e6?style=for-the-badge&labelColor=0b0f19)
![gate](https://img.shields.io/badge/real_money-gated_by_record-8fd3e6?style=for-the-badge&labelColor=0b0f19)
![default](https://img.shields.io/badge/default-paper-8f5a62?style=for-the-badge&labelColor=0b0f19)

</div>

---

## ◇ what it is

the bot runs [`shepherd.pine`](shepherd.pine) bar for bar in python
([`john_bot/shepherd.py`](john_bot/shepherd.py)): movements, the battle window,
war and resolution, echo, defense, the pattern memory that matches movement
sequences like melodies, and the projection plan (direction, stop, target,
weighted entry).

the engine keeps five simulated books with costs charged: **projection** and
**framework** (the indicator), **asherin** (the original bot's read, kept intact
so it can be judged fairly), and **momentum** and **random** as the baselines
anything must beat. that record — the validation panel on the chart — is the
only thing that can unlock real money. `ENGINE` picks which book the bot follows.

```
closed candle ─▶ shepherd engine ─▶ simulated engines ─▶ validation record
                                          │                      │
                                          ▼                      ▼
                                 open · limit · cancel      the gate: open?
                                 · time exit                       │
                                          └──────────┬─────────────┘
                                                     ▼
                                   paper: always follows · live: only if open
```

## ◇ the gate

real orders go out only when the followed engine's record on **that symbol and
timeframe** shows all of:

| check | default |
| --- | --- |
| enough simulated trades | ≥ 30 |
| expectancy after costs | ≥ +0.05R |
| distinguishable from noise | t-stat ≥ 2.0 |
| still working lately | last 30 trades > 0 |
| beats the dumb alternatives | better than momentum and random |
| pattern layer healthy | not degraded |

with the gate closed the bot keeps reading, logs every signal as `NOT traded`,
and tells you why. it re-checks after every candle and alerts when a gate opens
or closes. `REQUIRE_VALIDATED_EDGE=false` overrides it — don't.

## ◇ where it stands today

replayed on hyperliquid's last 5000 closed candles per market (fixed default
parameters, 0.12% round-trip cost), split in half to check the result holds.
each cell is trades / expectancy per trade in R:

| market | projection | asherin | random baseline |
| --- | --- | --- | --- |
| ETH 1h | 47 / −0.56 · 59 / −0.19 | **27 / +0.24 · 29 / +0.52** | −0.31 · −0.51 |
| BTC 1h | 38 / −0.50 · 60 / −0.59 | **16 / +0.69 · 30 / +0.11** | −1.61 · −0.46 |
| KAS 1h | 48 / +0.14 · 66 / −0.15 | 29 / −0.47 · 27 / +0.53 | −0.35 · +0.03 |
| SOL 1h | 48 / −0.32 · 75 / +0.13 | 27 / −0.48 · 31 / +0.40 | −0.64 · −1.17 |
| DOGE 1h | 52 / +0.00 · 85 / −0.23 | 35 / −0.04 · 27 / −0.34 | −1.04 · −1.71 |
| every market, 5m | −0.9 to −1.9 | −0.5 to +0.0 | |

what that means:

- **nothing clears the gate at its default strictness today.** closest is
  **asherin on ETH 1h** (56 trades, +0.38R, t = 1.5) and BTC 1h (46, +0.31R,
  t = 1.2). that is interesting, not proven: i tested ~30 market × engine ×
  timeframe combinations, and one or two looking this good is roughly what luck
  alone produces. that is why the default bar is t ≥ 2.0.
- **5m loses everywhere.** the round-trip cost is a big slice of a normal 5m move.
- **the projection's probabilities are real but not yet tradable.** predictions
  of .6–.8 came true ~56–58% of the time and .2–.4 about 33% — calibrated — but
  after costs and the stop/target geometry that has not turned into profit.
- **the old bot's default setup (KAS 5m) barely traded** — a price-rounding bug
  collapsed stops on low-priced coins — and 5m lost across the board.

the honest next step is forward paper-trading, which is truly out-of-sample:

```bash
SYMBOLS=ETH,BTC ENGINE=asherin INTERVAL=1h python -m john_bot   # paper
python -m john_bot.backtest ETH,BTC 5000                        # re-check any time
```

if the record keeps holding, the gate opens by itself.

## ◇ behavior

| trait | rule |
| --- | --- |
| **cadence** | acts on each closed candle (1h default); polls every 20s for fills and exits |
| **warmup** | replays up to 5000 candles at boot, so memory and the record exist immediately |
| **entries** | follows the chosen engine: market entry, or a resting limit for projection "wait" plans |
| **exits** | reduce-only stop and target rest on the exchange; time stops close at market |
| **one at a time** | one position or pending limit across all symbols |
| **sizing** | 1% of the sizing base at the stop, notional ≤ base × leverage × 20% |
| **compounding** | 40% of profit compounds into the sizing base, 60% banked |
| **breakers** | 6% daily loss → until next UTC day · 4 losses in a row → 12h pause · 15% drawdown → until `/resume` |
| **restart-safe** | reconciles with the exchange on boot; clears stale resting orders when flat |
| **catch-up** | after downtime, entry signals from old candles are ignored |

## ◇ discord

`/status` `/position` `/pnl` `/pause` `/resume` `/flatten` `/params` `/validate`

`/validate` prints the four engines' records, calibration, and the gate verdict
per symbol. set `DISCORD_BOT_TOKEN` + `DISCORD_CHANNEL_ID` for commands, or just
`DISCORD_WEBHOOK_URL` for alerts.

## ◇ run it

```bash
pip install -r requirements-dev.txt
pytest -q                          # engine, no-lookahead, gate, sizing, broker, follower
python -m john_bot.backtest        # the record for your configured symbols
python -m john_bot                 # the loop, paper by default
```

live: `DRY_RUN=false`, `HL_PRIVATE_KEY` (api / agent wallet, never your main
key), `HL_ACCOUNT_ADDRESS`. rehearse with `HL_TESTNET=true`. every knob is in
[`.env.example`](.env.example).

railway runs `python -m john_bot` (`railway.json`, `Procfile`). attach a volume
and point `STATE_PATH` at it if you want stats to survive redeploys.

## ◇ the indicator

open [`shepherd.pine`](shepherd.pine), paste it into TradingView's pine editor,
add to chart. its validation panel is the same record the bot reads.

## ◇ what changed from the old bot

| removed | why |
| --- | --- |
| `analysis.py` as the only brain | its math now lives in `asherin.py` as one engine among five; it trades real money only if its record earns it, like everything else |
| `asherin.pine` | replaced by `shepherd.pine`, whose validation panel is the bot's record |
| `reflect.py` self-learning | re-tuned its own filters from ~20 trades: fitting noise |
| `optimize.py` | grid search on a few weeks of data |
| 0.5% backtest slippage | made every 5m trade lose ~1 ATR on entry; now costs are an explicit 0.12% round trip |
| symbol rotation, cooldown = trade duration, 08–20 UTC session | untested rules that also made live trading differ from what the record measured |
| permanent pause after 3 losses | now a timed pause; drawdown still needs a human |
| kas price rounding bug in the backtest | stops collapsed to the entry on low-priced coins |

> [!CAUTION]
> leveraged perps can lose your whole margin. the record is simulated from
> candles: it charges a flat cost, assumes stops fill at their level, and can't
> see order-book depth or funding. a gate that opens is evidence, not a
> guarantee. start on testnet or tiny size.
