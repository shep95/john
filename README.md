# john algro bot

a non-ai, rules-based perpetual-futures bot that turns **john's chart narrative**
into a concrete algorithm and trades it on **hyperliquid** with **5x leverage** on
the **5m chart**, rotating **ETH ↔ DOGE**. hosted on **railway**.

there is no model and no runtime learning. every decision is deterministic math
over the last N closed candles — exactly the primitives john described.

## the core idea (narrative -> code)

john's thesis: *movement + context + velocity = meaning*, and the real unit is the
**relationship between movements**, not candle shape. the algorithm reads each 5m
frame like this:

1. **volatility / normal rate** — `atr` and average body/range set the environment
   so every measurement is normalized (velocity is contextual, not raw price).
2. **micro-war** — over a ~7-candle window, sum `buyer_force` vs `seller_force`
   (body + wick-defense, recency-weighted) → `dominance` and a `winner`.
3. **war resolved?** — only when dominance passes a threshold **and** velocity is
   accelerating in the winner's direction. high two-sided energy = *bouncing* → no trade.
4. **passion point** — the most contested candle (size + range + rejection wicks),
   scored with **persistence, repetition, defense, echo** so a small stubborn move
   can out-rank a big empty one (john's movement A vs B).
5. **echo** — the follow-through after the passion point: direction, length, latency,
   magnitude. reinforcement = same side as the winner.
6. **decision** — *winner + echo direction and length → LONG / SHORT*. anything
   unclear or blank → **no trade, wait for context**.
7. **SL/TP** — scaled by `conviction` (passion + echo + dominance). higher conviction
   → **tighter SL, wider TP** → better reward:risk, riding the resolved war's inertia.

file map: `analysis.py` (narrative math) · `strategy.py` (sizing + SL/TP) ·
`broker.py` (paper + live hyperliquid) · `engine.py` (loop, rotation, cooldown).

## behavior

- **5m chart**, one position at a time.
- **rotation**: after each closed trade, switch active symbol ETH → DOGE → ETH …
- **cooldown = last trade's duration**: if a trade took 40m to hit TP/SL, the bot
  waits ~40m before the next one (clamped by `MIN/MAX_COOLDOWN_SEC`).
- **TP/SL on-exchange**: live trades place reduce-only trigger orders so exits are
  automatic even if the bot restarts.
- **5x isolated leverage** (configurable).
- **compounding**: 40% of every profit is compounded back into the sizing base;
  the other 60% is *banked* (set aside). losses come out of the sizing base. all
  position sizing is `RISK_FRAC` of the compounding base, so wins grow the size.

## discord (alerts + commands)

set `DISCORD_BOT_TOKEN` + `DISCORD_CHANNEL_ID` (and optionally `DISCORD_GUILD_ID`
for instant command sync, `DISCORD_USER_ID` to get pinged). the bot then:

- 📈 alerts on **entry** — side, size, entry, TP/SL with **estimated profit & loss**,
  reward:risk, conviction.
- ✅/🛑 alerts on **exit** — result, trade pnl, held time, **compounded 40% vs
  banked 60%**, new sizing base, and **overall realized pnl + W/L record**.
- slash commands: `/status` `/position` `/pnl` `/pause` `/resume` `/flatten` `/params`.

no privileged intents needed (slash commands only). alerts-only? just set
`DISCORD_WEBHOOK_URL` and skip the token.

**bot setup**: discord developer portal → new application → Bot → copy token →
invite with `applications.commands` + `bot` scopes and "Send Messages" perm →
put the target channel id in `DISCORD_CHANNEL_ID`.

## quick start (paper, no keys)

```bash
pip install -r requirements.txt

# backtest / self-test on real recent candles
python -m john_bot.backtest              # ETH + DOGE, 500 candles
python -m john_bot.backtest ETH 1000

# run the live loop in PAPER mode (simulated fills on live data)
python -m john_bot
```

## going live on hyperliquid

1. create an **API wallet** (agent key) in the hyperliquid UI — never use your main
   withdrawal key.
2. set env: `DRY_RUN=false`, `HL_PRIVATE_KEY=<api wallet key>`,
   `HL_ACCOUNT_ADDRESS=<your main account address>`.
3. optionally `HL_TESTNET=true` to rehearse on testnet first.

all knobs live in `.env.example`.

## deploy to railway

1. push this repo to github (or use `railway up`).
2. `railway init` → new project → deploy from repo.
3. add variables from `.env.example` in the railway dashboard (at minimum set
   `DRY_RUN`, and for live: `HL_PRIVATE_KEY` + `HL_ACCOUNT_ADDRESS`).
4. railway runs the `worker` process (`python -m john_bot`) with auto-restart.

`state.json` persists rotation/cooldown/stats; railway's ephemeral fs resets on
redeploy, which is fine (the bot re-derives everything from the live feed).

## risk note

trading perps with leverage can lose the whole margin. defaults are conservative
(paper on, 2% risk/trade, isolated 5x). backtest results are hypothetical, ignore
funding/slippage beyond a taker-fee estimate, and are **not** a guarantee. start on
testnet or small size.
