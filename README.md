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
![leverage](https://img.shields.io/badge/leverage-2x_isolated-7cf5c4?style=for-the-badge&labelColor=0b0f19)
![default](https://img.shields.io/badge/default-paper_safe-ff5f8f?style=for-the-badge&labelColor=0b0f19)

`5m + 1h charts` · `KAS` · `deterministic` · `self-review` · `no model, no runtime learning`

</div>

---

> [!NOTE]
> there is no ai model here. every trade decision is deterministic math over the
> last N closed candles — exactly the primitives john described. an **optional**
> self-review layer (off by default, `SELF_LEARN`) can tighten its own entry
> filters after enough trades, but the core signal is never a trained model.

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
| **market** | trades **`KAS`** by default (any Hyperliquid perp; set `SYMBOLS`) · one position at a time |
| **cadence** | 5m chart · new entries only inside the trading-hours window (`SESSION_START/END_HOUR`, 08–20 UTC) |
| **cooldown** | equals the *last trade's* duration — a 40m trade → ~40m wait (clamped `MIN/MAX_COOLDOWN_SEC`, min 30m) |
| **exits** | live places **reduce-only trigger orders** on-exchange, so TP/SL fire even if the bot is down |
| **leverage** | **2x** isolated (configurable) |
| **risk limits** | ≤20% notional per trade · **6% daily-loss circuit breaker** pauses new entries |
| **compounding** | **40%** of each profit compounds into the sizing base, **60%** is banked (optional ceiling `MAX_SIZING_BASE`); losses come out of the base |
| **self-review** | after ≥20 trades it can tighten its own entry filters (opt-in `SELF_LEARN`, alerts before adopting) |
| **restart-safe** | the open trade is persisted and, in live mode, **reconciled directly from the exchange** on boot — no double-open, no lost position |

<br>

## ◇ cadence, cooldown & timeframes

the bot's rhythm is set by the trade you just took, not a fixed clock.

1. **the cooldown = how long the trade lived.** time the trade from entry until it
   hits its stop-loss **or** take-profit — then wait that same amount before
   entering a new one.

   > [!NOTE]
   > **scenario.** you enter KAS and it takes **40 minutes** to hit take-profit.
   > the bot now sits out for **~40 minutes** (min 30m) before it will consider
   > the next entry. a trade that resolved in **10 minutes** → the 30-minute
   > floor applies. (clamped by `MIN_COOLDOWN_SEC` / `MAX_COOLDOWN_SEC`.)

2. **timeframes: trade the 1-hour and the 5-minute charts only.** the 5m is the
   default reactive frame; the 1h is the slower, higher-conviction frame. set it
   with `INTERVAL=5m` or `INTERVAL=1h`.

3. **change the market with `SYMBOLS`.** it trades one symbol (`KAS` by default);
   set `SYMBOLS=KAS` — or comma-separate (e.g. `SYMBOLS=KAS,DOGE`) to rotate
   between markets after each closed trade.

<br>

## ◇ discord — alerts + commands

set `DISCORD_BOT_TOKEN` + `DISCORD_CHANNEL_ID` (optionally `DISCORD_GUILD_ID`
for instant command sync, `DISCORD_USER_ID` to get pinged):

- 📈 **entry** — side, size, entry, TP/SL with estimated P&L, reward:risk, conviction.
- ✅ / 🛑 **exit** — result, trade pnl, held time, compounded 40% vs banked 60%, new sizing base, realized pnl + W/L record.
- ⌨️ **slash commands** — `/status` `/position` `/pnl` `/pause` `/resume` `/flatten` `/params` `/reflect`

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
python -m john_bot.backtest            # configured symbols, 500 candles
python -m john_bot.backtest KAS 1000

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

## ◇ asherin — tradingview indicator

the same movement / war / passion read, as a **Pine v5 indicator** you can drop
straight onto any TradingView chart. it paints the state (trend / bouncing /
precursor / stubborn), marks passion points and echoes, and prints a live
**trade guide** with `LONG / SHORT / NO-TRADE`, entry, stop-loss and take-profit.

> [!TIP]
> **grab it:** open [`asherin.pine`](asherin.pine) and copy the whole file → in
> TradingView open **Pine Editor** → paste → **Add to chart**. use it on the
> **5m** and **1h** charts to match the bot.

**how to add it**

1. TradingView → bottom panel → **Pine Editor**.
2. paste the contents of [`asherin.pine`](asherin.pine).
3. **Save** → **Add to chart**.
4. (optional) open the ⚙ settings to tune the war window, passion weights, and
   SL/TP multiples; set alerts on the built-in `long signal` / `short signal`
   conditions.

> [!NOTE]
> the indicator is a **read / visualization aid** — it draws the setup and levels.
> it does **not** place orders. execution is the bot's job (paper or live on
> hyperliquid).

<details>
<summary><b>full source — <code>asherin.pine</code> (click to expand)</b></summary>

```pine
//@version=5
// asherin — movement / war / passion engine
indicator("asherin — movement / war / passion engine", overlay=true, max_labels_count=500, max_lines_count=500)

// ============================================================
// inputs
// ============================================================
grpCore  = "core windows"
warWin   = input.int(7, "war / observation window (candles)", minval=3, maxval=50, group=grpCore, tooltip="~6-7 candle micro-war window (section 4). not a law, an observation window.")
atrLen   = input.int(14, "volatility (ATR) length", minval=1, group=grpCore)
frameLen = input.int(50, "frame / context length", minval=5, group=grpCore, tooltip="frame = relative position of the move inside larger context (section 3).")
normLen  = input.int(50, "normal-rate lookback", minval=5, group=grpCore, tooltip="normal rate belongs to the pattern/context (section 9).")

grpVel      = "velocity / trend (section 2 & 5)"
trendThresh = input.float(0.25, "trend resolve threshold (norm velocity)", step=0.05, group=grpVel, tooltip="trend is velocity normalized by volatility, not raw displacement.")
domThresh   = input.float(2.0, "directional dominance threshold (net force)", step=0.5, group=grpVel, tooltip="war resolves when one side establishes velocity + direction (section 5).")

grpPassion    = "passion weights (section 6 & 8)"
wMag          = input.float(0.20, "weight: magnitude", step=0.05, group=grpPassion)
wConf         = input.float(0.35, "weight: conflict density", step=0.05, group=grpPassion)
wWick         = input.float(0.25, "weight: wick / resistance", step=0.05, group=grpPassion)
wPers         = input.float(0.20, "weight: persistence", step=0.05, group=grpPassion)
passionThresh = input.float(1.00, "passion point threshold", step=0.10, group=grpPassion, tooltip="large move with no resistance is NOT auto high passion (section 8).")

grpEcho     = "echo / normal rate (section 7 & 9)"
echoTrigger = input.float(1.6, "echo trigger (range/ATR)", step=0.1, group=grpEcho)
echoMax     = input.int(6, "echo max latency (bars)", group=grpEcho)
elongMult   = input.float(1.5, "elongation multiple", step=0.1, group=grpEcho)
constMult   = input.float(0.6, "constriction multiple", step=0.1, group=grpEcho)

grpStub        = "stubborn / precursor / conflict (12, 15-17)"
stubBody       = input.float(0.25, "stubborn body max (|body|/ATR)", step=0.05, group=grpStub)
precSize       = input.float(0.60, "precursor attempt max (range/ATR)", step=0.05, group=grpStub)
precCount      = input.int(3, "precursor attempts needed", group=grpStub)
conflictThresh = input.float(0.8, "bounce/conflict velocity threshold", step=0.1, group=grpStub)
tapTol         = input.float(0.25, "double-tap tolerance (ATR)", step=0.05, group=grpStub)
pivLen         = input.int(3, "pivot length (taps/echo)", group=grpStub)

grpTrade = "trade / SL / TP (new narrative)"
baseSL   = input.float(2.0, "base stop-loss (ATR mult)", step=0.1, group=grpTrade, tooltip="scaled DOWN by passion + echo strength (tighter when read is strong).")
baseTP   = input.float(3.0, "base take-profit (ATR mult)", step=0.1, group=grpTrade, tooltip="scaled UP by passion + echo strength (wider when read is strong).")
slSens   = input.float(0.60, "SL sensitivity to strength", step=0.05, minval=0, maxval=1, group=grpTrade)
tpSens   = input.float(1.00, "TP sensitivity to strength", step=0.05, minval=0, group=grpTrade)
slFloor  = input.float(0.50, "SL floor (min ATR mult)", step=0.1, group=grpTrade)

grpViz      = "visuals"
showBg      = input.bool(true, "state background", group=grpViz)
showBars    = input.bool(true, "elongation/constriction bar color", group=grpViz)
showLabels  = input.bool(false, "event labels (passion/echo/precursor)", group=grpViz, tooltip="off by default to reduce clutter.")
showTrade   = input.bool(true, "trade lines (entry/SL/TP)", group=grpViz)
showSigLabel= input.bool(true, "trade signal label on bar", group=grpViz)
showGuide   = input.bool(true, "trade guide box", group=grpViz)
guidePos    = input.string("middle_left", "guide box position", options=["top_left","top_center","top_right","middle_left","middle_center","middle_right","bottom_left","bottom_center","bottom_right"], group=grpViz)
showTable   = input.bool(true, "dashboard (top-right)", group=grpViz)

// ============================================================
// helpers
// ============================================================
f_pos(s) =>
    s == "top_left" ? position.top_left : s == "top_center" ? position.top_center : s == "top_right" ? position.top_right : s == "middle_left" ? position.middle_left : s == "middle_center" ? position.middle_center : s == "middle_right" ? position.middle_right : s == "bottom_left" ? position.bottom_left : s == "bottom_center" ? position.bottom_center : position.bottom_right

// ============================================================
// movement primitives (section 25)
// ============================================================
atr     = ta.atr(atrLen)
body    = close - open
absBody = math.abs(body)
rng     = high - low
upWick  = high - math.max(open, close)
dnWick  = math.min(open, close) - low
rngN    = atr > 0 ? rng / atr : 0.0
bodyN   = atr > 0 ? absBody / atr : 0.0
wickN   = atr > 0 ? (upWick + dnWick) / atr : 0.0

disp    = close - close[warWin]
velBar  = disp / warWin
normVel = atr > 0 ? velBar / atr : 0.0
trendDir = normVel > 0 ? 1 : normVel < 0 ? -1 : 0

hh       = ta.highest(high, frameLen)
ll       = ta.lowest(low, frameLen)
framePos = (hh - ll) > 0 ? (close - ll) / (hh - ll) : 0.5

// ============================================================
// interaction primitives — the micro-war (section 4 & 5)
// ============================================================
netForce = atr > 0 ? math.sum(body / atr, warWin) : 0.0
netDir   = netForce > 0 ? 1 : netForce < 0 ? -1 : 0

battles = 0
for i = 0 to warWin - 2
    s1 = math.sign(close[i] - open[i])
    s2 = math.sign(close[i + 1] - open[i + 1])
    if s1 != s2 and s1 != 0 and s2 != 0
        battles += 1
conflictDensity = warWin > 1 ? battles / float(warWin - 1) : 0.0

pdir = math.sign(close - close[1])
var int run = 0
run := (pdir == pdir[1] and pdir != 0) ? run + 1 : 1
inertia = normVel * run

upMove   = math.max(close - close[1], 0)
dnMove   = math.max(close[1] - close, 0)
upVel    = atr > 0 ? math.sum(upMove, warWin) / atr : 0.0
dnVel    = atr > 0 ? math.sum(dnMove, warWin) / atr : 0.0
bouncing = upVel > conflictThresh and dnVel > conflictThresh

normalRate  = ta.sma(rngN, normLen)
elongation  = normalRate > 0 and rngN > normalRate * elongMult
constriction= normalRate > 0 and rngN < normalRate * constMult
nrState     = elongation ? "elongation" : constriction ? "constriction" : "normal"

stubborn = bodyN < stubBody and math.abs(normVel) < trendThresh and not bouncing

// ============================================================
// passion (section 6 & 8)
// ============================================================
holdCount = 0
for i = 0 to warWin - 1
    b = atr > 0 ? math.abs(close[i] - open[i]) / atr : 0.0
    if b < stubBody
        holdCount += 1
persistence = warWin > 0 ? holdCount / float(warWin) : 0.0

passion = wMag * rngN + wConf * (conflictDensity * 3) + wWick * wickN + wPers * (persistence * 3)
passionPoint = passion > passionThresh and (conflictDensity > 0 or wickN > rngN * 0.5)
passionFire = passionPoint and not passionPoint[1]

// ============================================================
// echoes (section 7)
// ============================================================
big = rngN > echoTrigger
var int lastBigBar = na
var int lastBigDir = na
if big
    lastBigBar := bar_index
    lastBigDir := int(math.sign(body))
echoLatency = na(lastBigBar) ? na : bar_index - lastBigBar
isEcho = not na(lastBigBar) and not na(lastBigDir) and math.sign(body) == -lastBigDir and echoLatency <= echoMax and echoLatency > 0 and rngN < echoTrigger
echoFire = isEcho and not isEcho[1]

var int   echoDir = 0
var int   echoLen = na
var float echoMag = na
respBar = na(lastBigBar) ? na : bar_index - lastBigBar
if not na(respBar) and respBar > 0 and respBar <= echoMax and bodyN > 0.05
    echoDir := int(math.sign(body))
    echoLen := respBar
    echoMag := rngN

// ============================================================
// double taps (section 13)
// ============================================================
ph = ta.pivothigh(pivLen, pivLen)
pl = ta.pivotlow(pivLen, pivLen)
var float lastPH = na
var float lastPL = na
doubleTapHigh = false
doubleTapLow  = false
if not na(ph)
    if not na(lastPH) and math.abs(ph - lastPH) <= tapTol * atr
        doubleTapHigh := true
    lastPH := ph
if not na(pl)
    if not na(lastPL) and math.abs(pl - lastPL) <= tapTol * atr
        doubleTapLow := true
    lastPL := pl

// ============================================================
// precursor (section 15 & 16)
// ============================================================
attUp = 0
attDn = 0
for i = 0 to warWin - 1
    r = atr > 0 ? (high[i] - low[i]) / atr : 0.0
    if r < precSize
        if close[i] > open[i]
            attUp += 1
        else if close[i] < open[i]
            attDn += 1
precursorUp = attUp >= precCount and math.abs(normVel) < trendThresh and attUp > attDn
precursorDn = attDn >= precCount and math.abs(normVel) < trendThresh and attDn > attUp
precFire    = (precursorUp or precursorDn) and not (precursorUp[1] or precursorDn[1])

// ============================================================
// war resolution + dominance (section 5 & 11)
// ============================================================
warResolved = math.abs(normVel) >= trendThresh and math.abs(netForce) >= domThresh and trendDir == netDir
trendUp = warResolved and trendDir > 0
trendDn = warResolved and trendDir < 0
domInterp = netDir > 0 ? "persistence (buyers)" : netDir < 0 ? "exhaustion (sellers)" : "balanced"

state = "forming (battle)"
if bouncing
    state := "conflict / bouncing"
else if trendUp
    state := "trend up (resolved)"
else if trendDn
    state := "trend down (resolved)"
else if precursorUp
    state := "precursor up"
else if precursorDn
    state := "precursor down"
else if stubborn
    state := "stubborn / neutral"

// ============================================================
// TRADE DECISION — winner + echo => long/short; unclear/blank => wait
// ============================================================
winnerClear = warResolved or math.abs(netForce) >= domThresh
winnerDir   = winnerClear ? (warResolved ? trendDir : netDir) : 0

echoPresent  = not na(echoLen)
echoRecency  = echoPresent ? math.max(0.0, (echoMax - echoLen) / float(echoMax)) : 0.0
echoMagN     = not na(echoMag) ? math.min(1.0, echoMag / echoTrigger) : 0.0
echoStrength = (echoRecency + echoMagN) / 2.0

passionRel = passionThresh > 0 ? math.min(2.0, passion / passionThresh) : 0.0
strength   = math.min(1.0, math.max(0.0, (passionRel / 2.0 + echoStrength) / 2.0))

signalLong  = winnerDir > 0 and echoDir > 0 and echoPresent
signalShort = winnerDir < 0 and echoDir < 0 and echoPresent
rawDir      = signalLong ? 1 : signalShort ? -1 : 0
curSignal   = rawDir > 0 ? "LONG" : rawDir < 0 ? "SHORT" : (winnerDir == 0 ? "wait (no clear winner)" : not echoPresent ? "wait (no echo yet)" : "wait (echo disagrees)")

slMult = math.max(slFloor, baseSL * (1.0 - slSens * strength))
tpMult = baseTP * (1.0 + tpSens * strength)

var float entryP   = na
var float slP      = na
var float tpP      = na
var int   posDir   = 0
var int   entryBar = na
newSignal = rawDir != 0 and rawDir != posDir
if newSignal
    posDir   := rawDir
    entryP   := close
    entryBar := bar_index
    slP := rawDir > 0 ? close - slMult * atr : close + slMult * atr
    tpP := rawDir > 0 ? close + tpMult * atr : close - tpMult * atr

var bool tradeClosed = false
tradeClosed := false
if posDir != 0
    hitTP = posDir > 0 ? high >= tpP : low <= tpP
    hitSL = posDir > 0 ? low <= slP  : high >= slP
    if hitTP or hitSL
        posDir := 0
        tradeClosed := true

// ============================================================
// TRADE LINES — real horizontal price lines that pan/zoom with the chart
// ============================================================
var line  lnEntry = na
var line  lnSL    = na
var line  lnTP    = na
var label lbEntry = na
var label lbSL    = na
var label lbTP    = na

if showTrade and newSignal
    line.delete(lnEntry)
    line.delete(lnSL)
    line.delete(lnTP)
    label.delete(lbEntry)
    label.delete(lbSL)
    label.delete(lbTP)
    lnTP    := line.new(entryBar, tpP, bar_index, tpP, xloc=xloc.bar_index, extend=extend.right, color=color.new(color.lime, 0), width=2)
    lnSL    := line.new(entryBar, slP, bar_index, slP, xloc=xloc.bar_index, extend=extend.right, color=color.new(color.red, 0), width=2)
    lnEntry := line.new(entryBar, entryP, bar_index, entryP, xloc=xloc.bar_index, extend=extend.right, color=color.new(color.gray, 0), width=1, style=line.style_dashed)
    lbTP    := label.new(bar_index, tpP, "TP " + str.tostring(tpP, format.mintick), xloc=xloc.bar_index, style=label.style_label_left, color=color.new(color.lime, 0), textcolor=color.black, size=size.small)
    lbSL    := label.new(bar_index, slP, "SL " + str.tostring(slP, format.mintick), xloc=xloc.bar_index, style=label.style_label_left, color=color.new(color.red, 0), textcolor=color.white, size=size.small)
    lbEntry := label.new(bar_index, entryP, "entry " + str.tostring(entryP, format.mintick), xloc=xloc.bar_index, style=label.style_label_left, color=color.new(color.gray, 0), textcolor=color.white, size=size.tiny)

// keep the price labels riding the current bar (right edge) while the trade is live
if showTrade and posDir != 0 and not na(lbTP)
    label.set_x(lbTP, bar_index)
    label.set_x(lbSL, bar_index)
    label.set_x(lbEntry, bar_index)

// stop extending the lines once the trade closes (freeze at the exit bar)
if tradeClosed and not na(lnTP)
    line.set_extend(lnTP, extend.none)
    line.set_extend(lnSL, extend.none)
    line.set_extend(lnEntry, extend.none)
    line.set_x2(lnTP, bar_index)
    line.set_x2(lnSL, bar_index)
    line.set_x2(lnEntry, bar_index)

// ============================================================
// visuals
// ============================================================
bgCol = showBg ? (bouncing ? color.new(color.orange, 80) : trendUp ? color.new(color.green, 85) : trendDn ? color.new(color.red, 85) : (precursorUp or precursorDn) ? color.new(color.blue, 85) : stubborn ? color.new(color.gray, 88) : na) : na
bgcolor(bgCol)

barCol = showBars ? (elongation ? color.yellow : constriction ? color.new(color.gray, 40) : na) : na
barcolor(barCol)

if showLabels and passionFire
    label.new(bar_index, high, "passion " + str.tostring(passion, "#.##"), style=label.style_label_down, color=color.new(color.purple, 20), textcolor=color.white, size=size.tiny)
if showLabels and echoFire
    label.new(bar_index, low, "echo L" + str.tostring(echoLatency), style=label.style_label_up, color=color.new(color.teal, 30), textcolor=color.white, size=size.tiny)
if showLabels and precFire
    label.new(bar_index, precursorUp ? low : high, "precursor", style=precursorUp ? label.style_label_up : label.style_label_down, color=color.new(color.blue, 30), textcolor=color.white, size=size.tiny)

if showSigLabel and newSignal
    txt = (rawDir > 0 ? "LONG" : "SHORT") + "  R:R " + str.tostring(tpMult / slMult, "#.##")
    label.new(bar_index, rawDir > 0 ? low : high, txt, style=rawDir > 0 ? label.style_label_up : label.style_label_down, color=rawDir > 0 ? color.new(color.green, 10) : color.new(color.red, 10), textcolor=color.white, size=size.normal)

// ============================================================
// TRADE GUIDE BOX — plain numbers (movable, stays fixed on screen)
// ============================================================
var table g = table.new(f_pos(guidePos), 2, 5, border_width=1, frame_width=1, frame_color=color.gray)
if showGuide and barstate.islast
    dispDir   = posDir != 0 ? posDir : rawDir
    dispEntry = posDir != 0 ? entryP : (rawDir != 0 ? close : na)
    dispSL    = posDir != 0 ? slP : (rawDir > 0 ? close - slMult * atr : rawDir < 0 ? close + slMult * atr : na)
    dispTP    = posDir != 0 ? tpP : (rawDir > 0 ? close + tpMult * atr : rawDir < 0 ? close - tpMult * atr : na)
    dirTxt    = dispDir > 0 ? "LONG" : dispDir < 0 ? "SHORT" : "NO TRADE - WAIT"
    hCol      = dispDir > 0 ? color.new(color.green, 0) : dispDir < 0 ? color.new(color.red, 0) : color.new(color.gray, 10)
    bg        = color.new(color.black, 0)
    table.cell(g, 0, 0, "asherin trade guide", text_color=color.white, bgcolor=bg, text_size=size.small)
    table.cell(g, 1, 0, "", bgcolor=bg)
    table.merge_cells(g, 0, 0, 1, 0)
    table.cell(g, 0, 1, dirTxt, text_color=color.white, bgcolor=hCol, text_size=size.huge)
    table.cell(g, 1, 1, "", bgcolor=hCol)
    table.merge_cells(g, 0, 1, 1, 1)
    table.cell(g, 0, 2, "take profit", text_color=color.white, bgcolor=bg, text_size=size.large)
    table.cell(g, 1, 2, na(dispTP) ? "-" : str.tostring(dispTP, format.mintick), text_color=color.new(color.lime, 0), bgcolor=bg, text_size=size.large)
    table.cell(g, 0, 3, "stop loss", text_color=color.white, bgcolor=bg, text_size=size.large)
    table.cell(g, 1, 3, na(dispSL) ? "-" : str.tostring(dispSL, format.mintick), text_color=color.new(color.red, 0), bgcolor=bg, text_size=size.large)
    table.cell(g, 0, 4, "entry ~", text_color=color.white, bgcolor=bg, text_size=size.normal)
    table.cell(g, 1, 4, na(dispEntry) ? "-" : str.tostring(dispEntry, format.mintick), text_color=color.new(color.white, 0), bgcolor=bg, text_size=size.normal)

// ============================================================
// dashboard (top-right, detail)
// ============================================================
var table t = table.new(position.top_right, 2, 18, border_width=1)
if showTable and barstate.islast
    posText = posDir > 0 ? "long" : posDir < 0 ? "short" : "flat"
    table.cell(t, 0, 0, "metric", bgcolor=color.new(color.black, 0), text_color=color.white)
    table.cell(t, 1, 0, "value",  bgcolor=color.new(color.black, 0), text_color=color.white)
    table.cell(t, 0, 1, "signal")
    table.cell(t, 1, 1, curSignal)
    table.cell(t, 0, 2, "position")
    table.cell(t, 1, 2, posText)
    table.cell(t, 0, 3, "entry")
    table.cell(t, 1, 3, na(entryP) ? "-" : str.tostring(entryP, format.mintick))
    table.cell(t, 0, 4, "stop loss")
    table.cell(t, 1, 4, na(slP) ? "-" : str.tostring(slP, format.mintick) + "  (" + str.tostring(slMult, "#.##") + "x)")
    table.cell(t, 0, 5, "take profit")
    table.cell(t, 1, 5, na(tpP) ? "-" : str.tostring(tpP, format.mintick) + "  (" + str.tostring(tpMult, "#.##") + "x)")
    table.cell(t, 0, 6, "strength")
    table.cell(t, 1, 6, str.tostring(strength, "#.##"))
    table.cell(t, 0, 7, "state")
    table.cell(t, 1, 7, state)
    table.cell(t, 0, 8, "norm velocity")
    table.cell(t, 1, 8, str.tostring(normVel, "#.###"))
    table.cell(t, 0, 9, "net force")
    table.cell(t, 1, 9, str.tostring(netForce, "#.##"))
    table.cell(t, 0, 10, "dominance")
    table.cell(t, 1, 10, domInterp)
    table.cell(t, 0, 11, "battles / density")
    table.cell(t, 1, 11, str.tostring(battles) + " / " + str.tostring(conflictDensity, "#.##"))
    table.cell(t, 0, 12, "passion")
    table.cell(t, 1, 12, str.tostring(passion, "#.##"))
    table.cell(t, 0, 13, "echo dir / len")
    table.cell(t, 1, 13, (echoDir > 0 ? "up" : echoDir < 0 ? "dn" : "-") + " / " + (na(echoLen) ? "-" : str.tostring(echoLen)))
    table.cell(t, 0, 14, "echo strength")
    table.cell(t, 1, 14, str.tostring(echoStrength, "#.##"))
    table.cell(t, 0, 15, "frame pos")
    table.cell(t, 1, 15, str.tostring(framePos, "#.##"))
    table.cell(t, 0, 16, "normal-rate")
    table.cell(t, 1, 16, nrState)
    table.cell(t, 0, 17, "up / dn vel")
    table.cell(t, 1, 17, str.tostring(upVel, "#.##") + " / " + str.tostring(dnVel, "#.##"))

// ============================================================
// alerts
// ============================================================
alertcondition(newSignal and rawDir > 0, "long signal",  "winner + echo => LONG")
alertcondition(newSignal and rawDir < 0, "short signal", "winner + echo => SHORT")
alertcondition(passionPoint,             "passion point","high passion detected")
alertcondition(precFire,                 "precursor",    "repeated directional attempts")
alertcondition(bouncing,                 "conflict",     "both sides high velocity, unresolved")
```

</details>

<br>

## ◇ risk

> [!CAUTION]
> trading perps with leverage can lose your entire margin. defaults are
> conservative (paper on, 2% risk/trade, isolated 2x, ≤20% notional, 6% daily-loss
> cap), but backtest results are hypothetical, ignore funding and most slippage
> beyond a taker-fee estimate, and guarantee nothing. **start on testnet or tiny
> size.** note: at 2x with ≤20% notional, a very small account (e.g. ~$20) may not
> reach Hyperliquid's ~$10 minimum order — fund more or raise `MAX_POSITION_FRAC`.

<div align="center">
<br>

`deterministic · auditable · non-ai`

</div>
