# SCALP Strategy Quantitative Analysis

## Executive Summary

This document analyzes the quantitative model needed for a real 1m-timeframe scalp strategy
on BingX perpetual futures. All recommendations are grounded in:
- The existing HYBRID signal generator architecture (tiered scoring, regime detection)
- Live market data: BTC ~$69,780 (1m ATR ~$40-80), ETH ~$2,083 (1m ATR ~$1.5-3.0)
- BingX fee structure: 0.05% per trade (0.10% round-trip)
- Current system constraints (3-5s loop, 15x leverage, REST + WebSocket data)

---

## 1. Indicator Tuning for 1m Timeframe

### 1.1 EMA Periods

**Current:** EMA(9,21) -- designed for 5m HYBRID (45min/105min lookback)
**On 1m:** EMA(9) = 9 minutes, EMA(21) = 21 minutes

**Analysis:** EMA(9,21) on 1m is actually reasonable for scalping. The 9-minute EMA
captures immediate momentum, while the 21-minute EMA provides a medium anchor. Faster
EMAs like (3,8) would be too noisy on crypto 1m candles; they whipsaw constantly.

**Recommendation:**
- **Primary pair: EMA(5,13)** -- compromise between reactivity and noise filtering.
  EMA(5) = 5-minute momentum, EMA(13) = ~13-minute trend.
- **Secondary filter: EMA(21)** -- kept as a "macro" context filter. If price is below
  EMA(21) and we get a LONG signal, apply a friction penalty.
- **Rationale:** On live BTC 1m data, the spread between consecutive candle closes
  averages $20-50 (0.03-0.07%). EMA(3) would produce crossover signals every 2-3 candles.
  EMA(5) provides ~1 crossover per 5-8 candles, which is manageable.

### 1.2 RSI Period

**Current:** RSI(14) -- standard, but 14 minutes on 1m is a long lookback for scalp.

**Analysis:** From live ETH data, price moved $6.19 over 50 minutes (2079->2085), with
oscillations of $1-3 per candle. RSI(14) on 1m smooths out micro-moves well but is slow
to reach extremes. In the 50-candle sample, RSI(14) would rarely touch 30/70.

**Recommendation:**
- **RSI(7)** for scalp entry/exit signals. 7-minute lookback is responsive enough to
  catch 5-10 candle momentum bursts while filtering single-candle noise.
- **RSI zones adjusted:**
  - Long zone: 25-40 (was 20-43)
  - Short zone: 60-75 (was 57-80)
  - Critical exit long: > 75 (was 80)
  - Critical exit short: < 25 (was 20)
- **Rationale:** With RSI(7), values reach extremes faster. The narrower zones prevent
  entering counter-trend during strong moves. RSI(7) on the live BTC sample would have
  reached 65+ during the $69,670->$69,860 spike (18:02-18:09).

### 1.3 MACD Parameters

**Current:** MACD(12,26,9) -- 26 minutes lookback on 1m. Very slow for scalping.

**Analysis:** Standard MACD was designed for daily charts. On 1m, MACD(12,26,9) generates
signals with 26-minute lag. For a trade targeting 3-10 minute hold times, this is too slow.
The histogram is essentially a lagging confirmation of what EMA already shows.

**Recommendation:**
- **MACD(6,13,5)** -- halved parameters. Fast EMA = 6min, Slow EMA = 13min, Signal = 5min.
- **Primary use: Histogram direction and zero-cross, NOT absolute values.**
  - Histogram > 0 AND increasing = bullish momentum
  - Histogram < 0 AND decreasing = bearish momentum
  - Zero-cross = directional shift
- **Weight reduction:** From weight=1 to weight=0.5 in scoring (or keep at 1 but with
  stricter conditions). MACD on 1m is a secondary confirmation, not a driver.

### 1.4 ATR Period

**Current:** ATR(14) -- 14-minute volatility.

**Analysis from live data:**
- BTC 1m candle ranges (H-L):
  - Min: $6.00 (18:28, tight candle)
  - Max: $107.36 (18:09, spike candle)
  - Median: ~$35-50
  - ATR(14) on this sample: approximately **$42-55**
- ETH 1m candle ranges (H-L):
  - Min: $0.41 (17:57)
  - Max: $3.10 (18:09)
  - Median: ~$1.2-2.0
  - ATR(14) on this sample: approximately **$1.6-2.2**

**Recommendation:**
- **ATR(10)** for SL/TP calculation. 10-minute volatility is more responsive to
  regime shifts within a session.
- **ATR(5)** as a secondary "immediate volatility" gauge for dynamic position sizing
  and entry filtering. If ATR(5) > 2 * ATR(10), market is spiking -- reduce size or skip.

### 1.5 Additional Indicators

#### VWAP (Volume Weighted Average Price)
**Verdict: YES -- add as Tier 2 indicator.**
- VWAP resets at session boundaries but is valuable as an intraday anchor.
- Crypto doesn't have a true "session open," so use rolling 4h VWAP or reset at 00:00 UTC.
- **Scoring: Price above VWAP = +1 for longs, below VWAP = +1 for shorts.**
- **Implementation:** Calculate incrementally from candle data:
  ```
  cumulative_volume += volume
  cumulative_tp_volume += (H+L+C)/3 * volume
  vwap = cumulative_tp_volume / cumulative_volume
  ```

#### Cumulative Volume Delta (CVD)
**Verdict: NO -- skip for now.**
- Requires tick-level data or at minimum buy/sell volume split.
- BingX REST API provides only total volume per candle, not buy/sell breakdown.
- Would require WebSocket trade stream parsing, adding complexity.
- Consider for V2 if WebSocket trade data is available.

#### OBV (On Balance Volume)
**Verdict: MAYBE -- useful but redundant with volume_ratio.**
- OBV trend can confirm price momentum. Rising OBV + rising price = healthy trend.
- But our existing volume_ratio (current vs 20-period average) captures the
  same essence more simply.
- **Recommendation:** Skip OBV, enhance volume analysis with volume momentum instead:
  - volume_momentum = average volume of last 3 candles / average volume of last 10 candles
  - If > 1.3, volume is accelerating (bullish for entry quality).

#### Order Book Imbalance (from get_order_book())
**Verdict: YES -- add as Tier 3 indicator. See Section 5.**

---

## 2. Signal Scoring System for SCALP

### 2.1 Proposed SCALP Scoring Architecture

The SCALP scoring system should be **faster, simpler, and more momentum-focused** than
the HYBRID system. Fewer indicators, faster parameters, lower thresholds.

```
SCALP SIGNAL SCORING v1
========================

Tier 1: MOMENTUM (at least 1 required, drives direction)
  - EMA(5,13) alignment:     +2 (fast EMA above/below slow)
  - Price momentum (3-candle): +1 (3 consecutive candles in one direction)

Tier 2: CONFIRMATION (at least 1 required)
  - RSI(7) zone:              +2 (long: 25-40, short: 60-75)
  - VWAP position:            +1 (price above/below VWAP)

Tier 3: SUPPORT (optional, increases conviction)
  - Volume surge:             +1 (volume > 1.3x average)
  - Order book imbalance:     +1 (bid/ask ratio > 1.5 or < 0.67)
  - MACD(6,13,5) histogram:  +1 (histogram direction confirms)
  - BB touch/breach:          +1 (price at/beyond BB boundary)

Max base score: 10
Interaction bonuses: up to +2
Total possible: 12

Min score thresholds (regime-adaptive):
  TRENDING:      3 (ride the trend)
  RANGING:       6 (only strong mean-reversion)
  VOLATILE:      5 (momentum plays with caution)
  TRANSITIONAL:  7 (avoid unless very clear)
```

### 2.2 Key Differences from HYBRID Scoring

| Aspect | HYBRID (5m) | SCALP (1m) |
|--------|-------------|------------|
| EMA periods | 9,21 | 5,13 |
| RSI period | 14 | 7 |
| MACD params | 12,26,9 | 6,13,5 |
| S/R weight | 2 (important) | 0 (removed) |
| VWAP weight | 0 (absent) | 1 (new) |
| Order book | 0 (absent) | 1 (new) |
| Min score (default) | 5 | 4 |
| Momentum emphasis | Equal | Higher |

**Why remove S/R from SCALP scoring:**
- S/R levels from `calculate_support_resistance()` use a rolling window that looks back
  20+ candles. On 1m, these levels are 20+ minutes old and may already be invalidated.
- S/R on 1m is extremely noisy. A "support" that held for 10 candles can break in 1 candle.
- VWAP and order book imbalance provide better "microstructure support/resistance" for scalp.
- If S/R is desired, use the higher-timeframe (5m or 15m) S/R levels as context, not as a
  scoring element. This can be a hard filter: "do not open LONG within 0.1% of 15m resistance."

### 2.3 Asymmetric Scoring: Momentum vs Mean Reversion

**Recommendation: Favor momentum entries in TRENDING/VOLATILE regimes,
mean-reversion entries in RANGING regime.**

Implementation via regime-dependent weight adjustments:

```
TRENDING regime:
  EMA weight: 3 (from 2) -- momentum matters more
  RSI weight: 1 (from 2) -- RSI can stay extreme in trends
  BB weight:  0 (from 1) -- BB touch in trend = continuation, not reversal

RANGING regime:
  EMA weight: 1 (from 2) -- EMAs whipsaw in ranges
  RSI weight: 3 (from 2) -- RSI extremes are reliable reversal signals
  BB weight:  2 (from 1) -- BB boundaries are reliable in ranges

VOLATILE regime:
  EMA weight: 2 (unchanged)
  Volume weight: 2 (from 1) -- volume confirms real moves vs fakeouts
```

### 2.4 Interaction Bonuses for SCALP

```
Momentum Burst Bonus (+2):
  Conditions: EMA aligned + Volume > 1.5x + 3+ consecutive directional candles
  Rationale: Strong momentum with volume confirmation. High probability continuation.

VWAP Bounce Bonus (+1):
  Conditions: Price touches VWAP (within 0.05%) + RSI in zone + EMA confirms direction
  Rationale: VWAP acts as dynamic support/resistance. Bounce with confirmation is high quality.

Order Book Confluence (+1):
  Conditions: Order book imbalance + EMA alignment + Volume confirms
  Rationale: Smart money positioning + technical alignment.

Conflict Penalty (-2):
  Conditions: EMA says BUY but RSI > 70, or EMA says SELL but RSI < 30
  Rationale: Counter-momentum RSI extreme = likely correction about to reverse.

Spike Penalty (-1):
  Conditions: ATR(5) > 2.0 * ATR(10) at signal generation time
  Rationale: Entering during a spike = buying the top/selling the bottom.
```

---

## 3. SL/TP Optimization for 1m Scalp

### 3.1 Fee Impact Analysis

**Critical constraint: 0.1% round-trip fees at 15x leverage.**

```
At 15x leverage, $100 margin = $1,500 position
Round-trip fee: $1,500 * 0.001 = $1.50
Break-even move: 0.1% price change
Fee as % of margin: 1.5%

For BTC at $69,780:
  0.1% = $69.78 minimum price move to break even
  ATR(14) on 1m ~ $45 average
  So ATR(14) * 1.0 = ~$45, which is LESS than the break-even move

This means: SL/TP based on 1.0x ATR would result in negative expectancy
after fees. The minimum TP must be > $69.78, and SL should be tight.
```

### 3.2 Revised SL/TP Multipliers

**Current SCALP preset: ATR(14) * 1.5 for SL, ATR(14) * 2.0 for TP**

With ATR(10) ~ $45 for BTC:
- Current SL: $67.50 (0.097% of price) -- too close to break-even
- Current TP: $90.00 (0.129% of price) -- barely profitable after fees

**Proposed:**
```
SL: ATR(10) * 1.2 = ~$54 (0.077% of price)
TP: ATR(10) * 2.5 = ~$112 (0.161% of price)

Effective R/R after fees:
  Risk = $54 + $69.78 (fees) = $123.78 effective risk at 15x on margin
  Reward = $112 - $69.78 (fees) = $42.22 effective reward at 15x on margin

Wait -- this means the effective R/R is 42.22/123.78 = 0.34. Terrible.
```

**The fundamental problem: At 0.1% round-trip fees, tight scalp SL/TP ratios
are destroyed by fees. Let me recalculate properly.**

```
Position value = margin * leverage = M * 15
Fee per side = position_value * 0.0005
Total fees = position_value * 0.001 = M * 15 * 0.001 = M * 0.015

For margin $100:
  Position: $1,500
  Total fees: $1.50 (1.5% of margin)

If price moves +0.15% (TP):
  Gross profit: $1,500 * 0.0015 = $2.25
  Net profit: $2.25 - $1.50 = $0.75 (0.75% ROE)

If price moves -0.08% (SL):
  Gross loss: $1,500 * 0.0008 = $1.20
  Net loss: $1.20 + $1.50 = $2.70 (2.70% ROE)

This means: to achieve 1:1 net R/R, we need TP/SL gross ratio of about 2.8:1
```

### 3.3 Realistic SL/TP for 1m BTC/ETH Scalps

**The math demands wider TP relative to SL, or we must accept low win rate.**

**Option A: Wide TP, Tight SL (Momentum Strategy)**
```
SL: ATR(10) * 0.8 = ~$36 BTC, ~$1.30 ETH
TP: ATR(10) * 3.0 = ~$135 BTC, ~$5.00 ETH

Price moves needed:
  BTC SL: 0.052%, TP: 0.193%
  ETH SL: 0.062%, TP: 0.240%

Net R/R (after 0.1% RT fees):
  BTC: ($135-$69.78) / ($36+$69.78) = $65.22/$105.78 = 0.62
  ETH: ($5.00-$2.08) / ($1.30+$2.08) = $2.92/$3.38 = 0.86

Required win rate for profitability:
  BTC: 1/(1+0.62) = 62% win rate needed
  ETH: 1/(1+0.86) = 54% win rate needed
```

**Option B: Asymmetric with Trailing Stop (Recommended)**
```
Initial SL: ATR(10) * 1.0 = ~$45 BTC, ~$1.70 ETH
Initial TP: ATR(10) * 3.5 = ~$157 BTC, ~$6.00 ETH

After reaching 1.5x ATR profit, activate trailing stop at 0.5x ATR behind price.
This allows runners to capture 4-6x ATR on strong moves.

Expected outcomes:
  30% of trades: Hit initial SL = -$45 - $69.78 fees = -$114.78 per $1500 pos
  40% of trades: Hit trailing stop at ~2x ATR = +$90 - $69.78 fees = +$20.22
  20% of trades: Hit TP at 3.5x ATR = +$157 - $69.78 = +$87.22
  10% of trades: Runner via trailing = +$250 - $69.78 = +$180.22

EV per trade = 0.3*(-114.78) + 0.4*(20.22) + 0.2*(87.22) + 0.1*(180.22)
             = -34.43 + 8.09 + 17.44 + 18.02
             = $9.12 positive per trade (on $100 margin, 9.12% ROE)
```

**Recommendation: Option B (Asymmetric with Trailing Stop).**

### 3.4 Time-Based Exits

**YES -- critical for scalp. Add time-based exit rules:**

```
Rule 1: Maximum hold time = 15 minutes
  If position has been open for 15 candles without hitting TP, close at market.
  Rationale: Scalp trades should resolve quickly. A stagnant position ties up
  capital and exposure to black swan events.

Rule 2: Breakeven timeout = 8 minutes
  If position is in profit after 8 minutes but has not reached 1.5x ATR,
  tighten SL to breakeven (entry price + fees equivalent).
  Rationale: Protect gains from reversal.

Rule 3: Loss timeout = 5 minutes
  If position is at a loss after 5 minutes AND momentum indicators have flipped
  (EMA crossed against, MACD histogram reversed), close immediately.
  Rationale: The setup has failed; don't wait for SL hit.
```

### 3.5 Trailing Stop Implementation

```python
# Trailing stop logic for SCALP
class ScalpTrailingStop:
    def __init__(self, atr, activation_mult=1.5, trail_mult=0.5):
        self.activation_price_offset = atr * activation_mult
        self.trail_distance = atr * trail_mult
        self.activated = False
        self.trail_price = None

    def update(self, current_price, entry_price, side):
        """Call every candle. Returns (should_close, reason)."""
        if side == "BUY":
            profit = current_price - entry_price
            if not self.activated and profit >= self.activation_price_offset:
                self.activated = True
                self.trail_price = current_price - self.trail_distance
            elif self.activated:
                new_trail = current_price - self.trail_distance
                self.trail_price = max(self.trail_price, new_trail)
                if current_price <= self.trail_price:
                    return True, f"Trailing stop hit at {self.trail_price:.2f}"
        elif side == "SELL":
            profit = entry_price - current_price
            if not self.activated and profit >= self.activation_price_offset:
                self.activated = True
                self.trail_price = current_price + self.trail_distance
            elif self.activated:
                new_trail = current_price + self.trail_distance
                self.trail_price = min(self.trail_price, new_trail)
                if current_price >= self.trail_price:
                    return True, f"Trailing stop hit at {self.trail_price:.2f}"
        return False, ""
```

---

## 4. Risk Management for SCALP

### 4.1 Minimum Profitable Move at 15x Leverage

```
Fee per side: 0.05%
Round-trip: 0.10%
Minimum price move for gross profit: 0.10% / 15 = 0.0067% of margin... wait, no.

Actually:
  Position = margin * leverage
  Fee = position * 0.001 (round trip)
  Profit = position * price_change_pct

  Break-even: position * price_change = position * 0.001
  price_change = 0.1% minimum

  At BTC $69,780: minimum move = $69.78
  At ETH $2,083: minimum move = $2.08

  In ROE terms (return on margin):
  ROE = (price_change * leverage) - (fee_rate * leverage)
  Break-even ROE: 0.1% * 15 = 1.5% of margin eaten by fees
  So any trade that profits less than 1.5% ROE is a net loss.
```

### 4.2 Position Sizing Model

**Recommendation: Smaller base size than INTRADAY, with quality scaling.**

```
Base position size: 5% of balance (vs 10% for INTRADAY)
Rationale: Higher frequency = more exposure events. Smaller per-trade risk.

Dynamic sizing factors:
  - Quality factor: 0.5 + (quality * 0.8) = range [0.5x to 1.3x]
  - Regime factor:
    TRENDING: 1.2x (momentum is reliable)
    RANGING: 0.6x (mean reversion has lower success rate)
    VOLATILE: 0.5x (unpredictable)
    TRANSITIONAL: 0.4x (avoid)
  - Streak factor (same as current):
    Cold streak (WR < 30%): 0.5x
    Hot streak (WR > 60%): 1.1x

Effective range: 5% * 0.4 * 0.5 = 1.0% minimum
                 5% * 1.3 * 1.2 * 1.1 = 8.58% maximum

This keeps individual trade risk between 1-8.6% of balance.
```

### 4.3 Loss Limits

```
Per-trade max risk: ATR * 1.0 at 15x leverage
  BTC example: $45 / $69,780 * 15 = 0.97% of margin per trade
  With 5% position size on $1000 balance = $50 margin
  Max loss per trade: $50 * 0.0097 * 15 = $0.73... that's wrong.

Let me recalculate:
  Balance: $1000
  Position size: 5% = $50 margin
  Leveraged position: $50 * 15 = $750
  SL distance: ATR * 1.0 = $45 on BTC ($69,780)
  SL as % of price: 45/69780 = 0.064%
  Loss at SL: $750 * 0.00064 = $0.48
  Plus fees: $750 * 0.001 = $0.75
  Total loss: $1.23 per trade = 0.123% of balance

  Consecutive loss limit: 10 trades
  Daily loss limit: 3% of balance
  At 0.123% per trade: 3% / 0.123% = ~24 trades to hit daily limit

  But with 15x leverage and wider SL:
  If SL is ATR * 1.5 = $67.5:
  Loss: $750 * 0.00097 = $0.73
  Plus fees: $0.75
  Total: $1.48 per trade = 0.148% of balance
  Daily limit at 3%: ~20 losing trades
```

**Proposed limits:**

```
Consecutive loss limit: 5 trades
  Action: Pause trading for 30 minutes
  Rationale: 5 consecutive losses suggests regime mismatch. Wait for reset.

Daily loss limit: 3% of balance
  Action: Stop all SCALP trading for the day
  Rationale: Preserves capital. 3% daily drawdown is aggressive but survivable.

Per-hour loss limit: 1% of balance
  Action: Pause for 15 minutes
  Rationale: Prevents rapid drawdown during adverse conditions.

Maximum position hold time: 15 minutes
  Action: Market close
  Rationale: Scalp trades that haven't resolved are likely failed setups.

Maximum concurrent positions: 1
  Rationale: Focus capital on highest-quality signal. Multiple scalp positions
  increase correlation risk (all crypto moves together in 1m).
```

### 4.4 Daily Trade Frequency Caps

```
Maximum trades per hour: 6 (one every 10 minutes average)
Maximum trades per day: 50
Minimum cooldown between trades: 2 minutes (same symbol)

Rationale: Prevents overtrading. Even with 5s loop, we should NOT be entering
every minute. Wait for quality setups.
```

---

## 5. Microstructure Analysis

### 5.1 Order Book Imbalance Scoring

**BingX provides `get_order_book(symbol, limit=20)` returning top 20 bids/asks.**

From live BTC order book snapshot:
```
Best bid: $69,779.00 (0.017 BTC)
Best ask: $69,779.01 (0.312 BTC)
Spread: $0.01 (0.00001% -- essentially zero)

Top 10 bid volume: ~0.50 BTC ($34,890)
Top 10 ask volume: ~1.16 BTC ($80,943)
Ask/Bid ratio: 2.32 (heavy sell pressure in this snapshot)
```

**Imbalance scoring algorithm:**

```python
def calculate_ob_imbalance(order_book: dict, levels: int = 10) -> dict:
    """
    Calculate order book imbalance for scalp scoring.

    Returns:
        {
            "imbalance": float,  # -1.0 (sell heavy) to +1.0 (buy heavy)
            "signal": str,       # "BUY", "SELL", or "NEUTRAL"
            "spread_bps": float, # Spread in basis points
            "score": int         # 0 or 1 for scoring
        }
    """
    bids = order_book.get("bids", [])[:levels]
    asks = order_book.get("asks", [])[:levels]

    bid_volume = sum(qty for _, qty in bids)
    ask_volume = sum(qty for _, qty in asks)

    total = bid_volume + ask_volume
    if total == 0:
        return {"imbalance": 0, "signal": "NEUTRAL", "spread_bps": 0, "score": 0}

    # Imbalance: positive = buy pressure, negative = sell pressure
    imbalance = (bid_volume - ask_volume) / total  # range [-1, 1]

    # Spread in basis points
    if bids and asks:
        spread_bps = (asks[0][0] - bids[0][0]) / bids[0][0] * 10000
    else:
        spread_bps = 0

    # Signal determination
    if imbalance > 0.3:
        signal = "BUY"
        score = 1
    elif imbalance < -0.3:
        signal = "SELL"
        score = 1
    else:
        signal = "NEUTRAL"
        score = 0

    return {
        "imbalance": round(imbalance, 3),
        "signal": signal,
        "spread_bps": round(spread_bps, 2),
        "score": score
    }
```

**Important caveats:**
- Order book snapshots via REST have 100-500ms latency. For 1m candle scalping this
  is acceptable (we're not doing sub-second HFT).
- Order book data is easily spoofed by market makers. Use imbalance as a Tier 3
  indicator (+1 score) only, never as a primary signal.
- BingX limit is 100 levels, but top 10-20 levels are most relevant for scalp.
- Cache order book data for 3-5 seconds (same as position cache) to avoid rate limits.

### 5.2 Volume Profile Analysis

**For 1m candles, simple volume analysis is sufficient:**

```
Volume momentum: avg(volume[-3:]) / avg(volume[-10:])
  > 1.5: Volume accelerating (momentum confirmation)
  > 2.0: Volume spike (breakout or capitulation)
  < 0.5: Volume declining (avoid entry, fading move)

Volume-price confirmation:
  Rising price + Rising volume = Strong (score bonus)
  Rising price + Declining volume = Weak (no bonus, possible penalty)
  Falling price + Rising volume = Capitulation (look for reversal signals)
  Falling price + Declining volume = Orderly decline (trend-following short OK)
```

### 5.3 Spread Analysis

From live BTC data: spread = $0.01 = 0.00001% = 0.0001 bps.
This is negligible for BTC/ETH on BingX perpetual futures.

**However, for altcoins (DOGE, ALGO, UNI), spread can be significant:**
- If spread > 0.02% (2 bps), add a slippage buffer to SL/TP calculations.
- If spread > 0.05% (5 bps), avoid scalping that pair entirely.

**Recommendation:** Add a spread filter as a hard gate:
```
if spread_bps > 5.0:
    skip_scalp("Spread too wide for scalp: {spread_bps} bps")
```

### 5.4 Tick-Level Data Value

**Verdict: NOT worth it for this architecture.**
- The bot runs on a 3-5 second loop with REST API calls.
- True tick data requires persistent WebSocket connection with sub-second processing.
- The existing `ws_data_provider.py` caches kline data, not individual trades.
- Adding tick-level analysis would require:
  - New WebSocket subscription to trade stream
  - In-memory order flow processing
  - Significant architecture changes
- The marginal value for 1m-candle scalping is low. Tick data matters for
  sub-second market making, not 3-5 second loop scalping.

---

## 6. Entry/Exit Rules

### 6.1 Momentum Scalp (Breakout with Volume)

**Setup identification:**
```
Conditions (ALL must be true):
  1. EMA(5) > EMA(13) [for LONG] or EMA(5) < EMA(13) [for SHORT]
  2. Price breaks above/below the high/low of the last 5 candles
  3. Current candle volume > 1.3x average volume (last 10 candles)
  4. RSI(7) in 45-65 range (not yet extreme -- room to run)
  5. ATR(5) > 0.7 * ATR(10) (minimum volatility)

Entry:
  Market order at next candle open (3-5s loop)

SL: Below the breakout candle low (LONG) or above breakout candle high (SHORT)
  Minimum: ATR(10) * 0.8
  Maximum: ATR(10) * 1.5

TP: ATR(10) * 3.0 (initial), with trailing stop activation at ATR(10) * 1.5

Quality score: Typically 6-10 (Tier 1 + Tier 2 + volume + momentum)
```

### 6.2 Mean Reversion Scalp (Overextension at Band Boundary)

**Setup identification:**
```
Conditions (ALL must be true):
  1. Price touches or breaches BB lower band (LONG) or BB upper band (SHORT)
  2. RSI(7) < 30 (LONG) or RSI(7) > 70 (SHORT) -- actually oversold/overbought
  3. Market regime = RANGING (critical -- mean reversion in trends = suicide)
  4. Volume is NOT spiking (volume_ratio < 1.5) -- volume spike = breakdown, not bounce
  5. Order book shows support (imbalance > 0.2 for LONG, < -0.2 for SHORT)

Entry:
  Market order (or limit order at BB band with 1-candle timeout)

SL: Beyond the band by ATR(10) * 0.5
  LONG SL: BB_lower - ATR(10) * 0.5
  SHORT SL: BB_upper + ATR(10) * 0.5

TP: BB middle (mean) -- this is the reversion target

Quality score: Typically 5-8 (RSI extreme + BB touch + regime bonus)

CRITICAL FILTER: Do NOT take mean reversion trades if:
  - EMA(21) is steep (trend is strong)
  - Volume is spiking (breakdown/breakout in progress)
  - Price has broken through band by more than ATR(10) * 1.0 (extreme move)
```

### 6.3 Pullback Scalp (With-Trend Pullback Entry)

**Setup identification:**
```
Conditions (ALL must be true):
  1. EMA(5) > EMA(13) > EMA(21) [LONG] or inverse [SHORT] -- stacked EMAs
  2. Price pulls back TO EMA(5) or EMA(13) (within ATR(10) * 0.3 of the EMA)
  3. RSI(7) has pulled back to 40-55 range (LONG) or 45-60 range (SHORT)
  4. MACD histogram is still positive (LONG) or negative (SHORT) -- trend intact
  5. The pullback is on declining volume (orderly, not panic selling)

Entry:
  Market order when price bounces off EMA (closes above EMA after touch)

SL: Below the EMA that price bounced from, minus ATR(10) * 0.3
  LONG: EMA(13) - ATR(10) * 0.3
  SHORT: EMA(13) + ATR(10) * 0.3

TP: Previous swing high (LONG) or swing low (SHORT)
  Minimum: ATR(10) * 2.5
  With trailing stop activation at ATR(10) * 1.5

Quality score: Typically 7-10 (strong trend + pullback + multiple confirmations)

This is the HIGHEST quality scalp setup because it trades with the trend
and enters on a temporary dip.
```

### 6.4 Mechanical Exit Rules (No AI Required)

```
EXIT IMMEDIATELY if any condition is true:

1. SL/TP hit (handled by exchange order)

2. Time exit: Position open > 15 minutes

3. Momentum reversal exit:
   LONG: EMA(5) crosses below EMA(13) AND RSI(7) < 45
   SHORT: EMA(5) crosses above EMA(13) AND RSI(7) > 55

4. Volume capitulation exit:
   Position at loss AND volume spikes > 2.0x average
   (Smart money dumping, get out)

5. RSI extreme exit:
   LONG: RSI(7) > 80 (take profit on overextension)
   SHORT: RSI(7) < 20 (take profit on overextension)

6. Trailing stop hit (see section 3.5)

7. Breakeven tightening:
   After 8 minutes in profit, move SL to entry + fee equivalent
   BTC: entry + $70 (0.1% to cover fees)
   ETH: entry + $2.10

8. Daily/hourly loss limit hit: Close ALL positions, stop trading
```

---

## 7. Regime Detection Tuning for SCALP

The current `MarketRegimeDetector` uses EMA(9,21) spread and BB width percentiles.
For SCALP on 1m, the regime detection should use faster parameters:

```
SCALP regime detection parameters:
  lookback_candles: 5 (was 10) -- faster regime shifts on 1m
  ema_spread_thresholds:
    no_trend: 0.05% (was 0.15%) -- 1m EMA spreads are tighter
    weak: 0.15% (was 0.5%)
    strong: 0.4% (was 1.5%)
  volatility_percentile_window: 50 (was 100) -- 50 minutes of history

SCALP regime parameters:
  TRENDING:
    min_score: 3
    sl_multiplier: 1.0
    tp_multiplier: 3.0
    position_size_factor: 1.2
  RANGING:
    min_score: 6
    sl_multiplier: 0.8
    tp_multiplier: 1.5
    position_size_factor: 0.6
  VOLATILE:
    min_score: 5
    sl_multiplier: 1.5
    tp_multiplier: 2.0
    position_size_factor: 0.5
  TRANSITIONAL:
    min_score: 7
    sl_multiplier: 1.2
    tp_multiplier: 2.0
    position_size_factor: 0.4
```

---

## 8. Configuration Proposal (bot_config.json additions)

```json
{
  "SCALP_SETTINGS": {
    "enabled": true,
    "signal_rules": {
      "ema_periods": [5, 13],
      "ema_macro": 21,
      "rsi_period": 7,
      "macd_params": [6, 13, 5],
      "atr_period": 10,
      "atr_fast_period": 5,
      "bb_period": 15,
      "bb_std": 2.0,

      "ema_weight": 2,
      "momentum_weight": 1,
      "rsi_weight": 2,
      "vwap_weight": 1,
      "volume_weight": 1,
      "ob_imbalance_weight": 1,
      "macd_weight": 1,
      "bb_weight": 1,

      "rsi_long_zone": [25, 40],
      "rsi_short_zone": [60, 75],
      "rsi_exit_long": 75,
      "rsi_exit_short": 25,
      "min_volume_ratio": 0.5,
      "min_atr_ratio": 0.3,
      "ob_imbalance_threshold": 0.3,
      "spread_max_bps": 5.0,

      "tier1_required": true,
      "conflict_friction_threshold": 2,
      "min_score_for_signal": 4
    },
    "sl_tp": {
      "sl_atr_mult": 1.0,
      "tp_atr_mult": 3.0,
      "trailing_activation_mult": 1.5,
      "trailing_distance_mult": 0.5,
      "max_hold_minutes": 15,
      "breakeven_timeout_minutes": 8,
      "loss_timeout_minutes": 5
    },
    "risk_limits": {
      "base_position_pct": 5.0,
      "max_consecutive_losses": 5,
      "consecutive_loss_cooldown_minutes": 30,
      "daily_loss_limit_pct": 3.0,
      "hourly_loss_limit_pct": 1.0,
      "max_trades_per_hour": 6,
      "max_trades_per_day": 50,
      "min_cooldown_seconds": 120,
      "max_concurrent_positions": 1
    },
    "regime_overrides": {
      "TRENDING": {
        "ema_weight": 3, "rsi_weight": 1, "bb_weight": 0,
        "min_score": 3
      },
      "RANGING": {
        "ema_weight": 1, "rsi_weight": 3, "bb_weight": 2,
        "min_score": 6
      },
      "VOLATILE": {
        "ema_weight": 2, "volume_weight": 2,
        "min_score": 5
      },
      "TRANSITIONAL": {
        "min_score": 7
      }
    },
    "interaction_rules": {
      "momentum_burst_bonus": 2,
      "vwap_bounce_bonus": 1,
      "ob_confluence_bonus": 1,
      "counter_momentum_penalty": -2,
      "spike_penalty": -1
    },
    "entry_types": {
      "momentum_breakout": true,
      "mean_reversion": true,
      "pullback": true
    }
  }
}
```

---

## 9. Summary of Recommendations

### Critical Changes from HYBRID

| Component | HYBRID (5m) | SCALP (1m) |
|-----------|-------------|------------|
| EMA periods | 9, 21 | 5, 13 (+ 21 macro filter) |
| RSI period | 14 | 7 |
| MACD | 12, 26, 9 | 6, 13, 5 |
| ATR | 14 | 10 (+ ATR(5) for spike detection) |
| S/R levels | Tier 2, weight 2 | Removed from scoring |
| VWAP | Not used | Tier 2, weight 1 |
| Order book | Not used | Tier 3, weight 1 |
| SL mult | 1.5x ATR | 1.0x ATR |
| TP mult | 3.0x ATR | 3.0x ATR (+ trailing) |
| Trailing stop | None | Activate at 1.5x ATR, trail 0.5x ATR |
| Time exits | None | 15-minute max hold |
| Base position | 10% | 5% |
| Max hold | Unlimited | 15 minutes |
| Daily loss cap | None | 3% |
| Cooldown | None | 2 minutes min between trades |

### Risk Assessment

**Biggest risk: Fee drag.** At 0.1% round-trip, the strategy needs to average > 0.15%
gross profit per trade to be viable. This is achievable with ATR * 3.0 TP targets
(0.19% for BTC), but requires disciplined entry selection (don't overtrade).

**Second risk: Overtrading.** A 5-second loop on 1m candles will see thousands of
potential entries per day. The scoring system must be selective. A quality threshold
of 4+ with Tier 1 requirement should filter to 5-15 signals per hour.

**Third risk: Slippage.** Market orders on BTC/ETH have minimal slippage (< 0.01%).
But altcoins (DOGE, ALGO) may have 0.02-0.05% slippage. Consider limiting SCALP
mode to BTC and ETH only, or top-5 by volume.

### Implementation Priority

1. **ScalpSignalGenerator** class (new, parallel to SignalGenerator)
2. **VWAP indicator** in analyzer.py
3. **Order book imbalance** scoring function
4. **Trailing stop** logic in position management
5. **Time-based exits** in process_worker.py
6. **Risk limits** (daily/hourly loss, consecutive losses, cooldowns)
7. **Regime detection tuning** for 1m parameters
8. **ScalpStrategy** prompt (for AI veto mode, if used)
