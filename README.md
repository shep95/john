<div align="center">

# ◢ JOHN ◣

### `narrative → algorithm` · a non-ai perpetual-futures engine

**movement + context + velocity = meaning.** john's chart thesis, compiled into
deterministic math and traded live on hyperliquid.

<br>

![python](https://img.shields.io/badge/python-3.12-0b0f19?style=for-the-badge&logo=python&logoColor=7cf5c4&labelColor=0b0f19)
![exchange](https://img.shields.io/badge/exchange-hyperliquid-0b0f19?style=for-the-badge&logoColor=7cf5c4&labelColor=0b0f19)
![deploy](https://img.shields.io/badge/deploy-railway-0b0f19?style=for-the-badge&logo=railway&logoColor=7cf5c4&labelColor=0b0f19)
<br>
![engine](https://img.shields.io/badge/engine-deterministic-7cf5c4?style=for-the-badge&labelColor=0b0f19)
![leverage](https://img.shields.io/badge/leverage-5x_isolated-7cf5c4?style=for-the-badge&labelColor=0b0f19)
![default](https://img.shields.io/badge/default-paper_safe-ff5f8f?style=for-the-badge&labelColor=0b0f19)

`5m chart` · `ETH ⇄ DOGE rotation` · `no model · no runtime learning`

</div>

---

> [!NOTE]
> there is no ai here. every decision is deterministic math over the last N
> closed candles — exactly the primitives john described. nothing is fit,
> trained, or learned at runtime.

<br>

## ◇ the pipeline

each closed 5m frame is compiled through seven stages. the unit is the
**relationship between movements**, never the candle shape.

```
   candles ─▶ [1] volatility / normal rate      atr · avg body/range  →  everything normalized
             [2] micro-war                      Σ buyer_force vs seller_force (recency-weighted)
             [3] war resolved?                  |dominance| ≥ θ  AND  velocity accelerating
             [4] passion point                  most-contested candle: size·range·rejection·defense
             [5] echo                            follow-through: direction · length · latency · magnitude
             [6] decision                        winner + echo  →  LONG · SHORT · NO-TRADE
             [7] SL / TP                          conviction  →  tighter stop, wider target
                                                                     │
                                                                     ▼
                                                              sized order + on-exchange TP/SL
```

<table>
<tr><td width="55%">

**what makes it "john"**

- a **small stubborn** move can out-rank a **big empty** one — passion weights
  persistence, repetition, defense and echo, not raw body size.
- **both sides loud = bouncing** → no trade. it only fires when one side has
  established directional velocity.
- **persistence vs exhaustion** is one primitive read by direction, not two
  hardcoded labels.
- unclear or blank → **wait for context.**

</td><td width="45%">

**file map**

| module | role |
| --- | --- |
| `analysis.py` | narrative → math, the read |
| `strategy.py` | sizing + SL/TP shaping |
| `broker.py` | paper + live hyperliquid |
| `engine.py` | loop · rotation · cooldown |
| `market.py` | keyless 5m candle feed |
| `notify.py` | discord alerts + commands |

</td></tr>
</table>

<br>

## ◇ behavior

| trait | rule |
| --- | --- |
| **cadence** | 5m chart · one position at a time |
| **rotation** | after every close, flip active symbol `ETH → DOGE → ETH …` |
| **cooldown** | equals the *last trade's* duration — a 40m trade → ~40m wait (clamped `MIN/MAX_COOLDOWN_SEC`) |
| **exits** | live places **reduce-only trigger orders** on-exchange, so TP/SL fire even if the bot is down |
| **leverage** | 5x isolated (configurable) |
| **compounding** | **40%** of each profit compounds into the sizing base, **60%** is banked; losses come out of the base, so wins grow position size |
| **restart-safe** | the open trade is persisted and, in live mode, **reconciled directly from the exchange** on boot — no double-open, no lost position |

<br>

## ◇ discord — alerts + commands

set `DISCORD_BOT_TOKEN` + `DISCORD_CHANNEL_ID` (optionally `DISCORD_GUILD_ID`
for instant command sync, `DISCORD_USER_ID` to get pinged):

- 📈 **entry** — side, size, entry, TP/SL with estimated P&L, reward:risk, conviction.
- ✅ / 🛑 **exit** — result, trade pnl, held time, compounded 40% vs banked 60%, new sizing base, realized pnl + W/L record.
- ⌨️ **slash commands** — `/status` `/position` `/pnl` `/pause` `/resume` `/flatten` `/params`

> [!TIP]
> alerts-only, no commands? just set `DISCORD_WEBHOOK_URL` and skip the token.
> no privileged intents are required (slash commands only).

<details>
<summary><b>bot setup (click)</b></summary>

<br>

1. discord developer portal → **new application** → **Bot** → copy token.
2. invite with the `applications.commands` + `bot` scopes and the **Send Messages** permission.
3. put the target channel id in `DISCORD_CHANNEL_ID`.

</details>

<br>

## ◇ quick start — paper, no keys

```bash
pip install -r requirements.txt

# backtest / self-test on real recent candles
python -m john_bot.backtest            # ETH + DOGE, 500 candles
python -m john_bot.backtest ETH 1000

# run the live loop in PAPER mode (simulated fills on live data)
python -m john_bot
```

paper trading on live data is **on by default** — it never touches real funds
until you explicitly flip `DRY_RUN=false` and supply a key.

<br>

## ◇ going live on hyperliquid

```ini
DRY_RUN=false
HL_PRIVATE_KEY=<api wallet / agent key>     # NOT your main withdrawal key
HL_ACCOUNT_ADDRESS=<your main account address>
HL_TESTNET=true                             # optional: rehearse on testnet first
```

> [!IMPORTANT]
> use a hyperliquid **API wallet (agent key)** — never your main withdrawal key.
> every knob lives in [`.env.example`](.env.example).

<br>

## ◇ deploy to railway

1. push this repo to github (or `railway up`).
2. `railway init` → new project → deploy from repo.
3. add variables from `.env.example` in the dashboard (at minimum `DRY_RUN`; for live, `HL_PRIVATE_KEY` + `HL_ACCOUNT_ADDRESS`).
4. railway runs the `worker` process (`python -m john_bot`) with auto-restart.

> [!NOTE]
> `state.json` persists rotation, cooldown, stats and the open trade. railway's
> filesystem is ephemeral, so it can reset on redeploy — in **live** mode the bot
> recovers any open position straight from the exchange, and its on-exchange TP/SL
> keep protecting it regardless. attach a railway volume if you want the local
> state to survive redeploys too.

<br>

## ◇ risk

> [!CAUTION]
> trading perps with leverage can lose your entire margin. defaults are
> conservative (paper on, 2% risk/trade, isolated 5x), but backtest results are
> hypothetical, ignore funding and most slippage beyond a taker-fee estimate, and
> guarantee nothing. **start on testnet or tiny size.**

<div align="center">
<br>

`deterministic · auditable · non-ai`

</div>
