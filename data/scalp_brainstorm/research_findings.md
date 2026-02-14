# Crypto Futures Scalping Research Findings

## Context
- Exchange: BingX perpetual futures
- Fee structure: **0.02% maker / 0.05% taker** (standard tier)
- Leverage: 15x
- Timeframe: 1-minute candles
- Execution: Automated Python bot, 3-5 second loop
- Available data: OHLCV candles, order book (up to 100 levels), ticker (best bid/ask/last/volume), WebSocket real-time klines

---

## 1. Best Indicators for 1-Minute Crypto Scalping

### 1.1 EMA Crossovers

**Recommended: EMA 9 / EMA 21**
- The 9/21 EMA crossover is the most widely validated combination for 1-minute crypto scalping
- EMA 9 crossing above EMA 21 = bullish signal; below = bearish
- Backtests on Bitcoin show >65% accuracy in trending conditions
- **Critical limitation**: generates many false signals in ranging/choppy markets. MUST be combined with a trend/chop filter
- **Enhancement**: add EMA 50 as a trend context filter. Only take longs when price is above EMA 50, shorts when below
- **EMA slope**: the rate of change of EMA 9 (current - 3 bars ago) provides momentum confirmation. Steeper slope = stronger signal

### 1.2 RSI

**Recommended: RSI period 7, levels 80/20 (or 75/25)**
- Standard RSI-14 is too slow for 1-minute crypto. Period 7 provides the right balance of speed and smoothness
- For scalping, the standard 70/30 levels are too conservative on 1-minute charts. Use **80/20** for extremes (mean reversion) or **75/25** for moderate sensitivity
- **Usage patterns**:
  - RSI < 20: extreme oversold, potential mean reversion LONG
  - RSI > 80: extreme overbought, potential mean reversion SHORT
  - RSI 55-75 in uptrend: continuation LONG zone (momentum)
  - RSI 25-45 in downtrend: continuation SHORT zone (momentum)
  - RSI 40-60: neutral/no-trade zone (unless other signals are strong)
- **RSI divergence on 1m**: rarely useful due to noise. Skip it

### 1.3 VWAP (Volume Weighted Average Price)

**Can be approximated from 1m OHLCV data. Recommended.**
- Formula: `VWAP = cumulative(typical_price * volume) / cumulative(volume)`
- Typical price = `(High + Low + Close) / 3`
- Reset at midnight UTC (standard crypto session)
- **Usage**: Price above VWAP = bullish bias, below = bearish bias
- **Scalp entries**: Buy pullbacks to VWAP in uptrend, sell rallies to VWAP in downtrend
- VWAP acts as a dynamic support/resistance level that institutional algos respect
- **Implementation note**: straightforward with 1m candles. Just maintain running cumulative sums, reset at 00:00 UTC

### 1.4 Cumulative Volume Delta (CVD)

**Can be approximated from 1m candles. Recommended.**
- True CVD requires tick data (buy vs sell classified trades), but an approximation works:
  - If close > open: delta = +volume (buyers dominated)
  - If close < open: delta = -volume (sellers dominated)
  - If close == open: delta = 0
- Track cumulative delta over a rolling window (e.g., 20-30 bars)
- **Divergence signals**:
  - Price rising + CVD falling = bearish divergence (sellers absorbing, potential reversal)
  - Price falling + CVD rising = bullish divergence (buyers accumulating, potential bounce)
- This is one of the most underused but effective scalping signals

### 1.5 Order Book Imbalance

**Available via BingX API. Highly recommended for scalping.**
- BingX provides up to 100 levels of order book depth
- **Bid/Ask Imbalance Ratio**: `sum(bid_qty, top N levels) / sum(ask_qty, top N levels)`
  - Ratio > 1.5: strong buying pressure (short-term bullish)
  - Ratio < 0.67: strong selling pressure (short-term bearish)
  - Use top 5-10 levels for immediate pressure, top 20 for broader context
- **Implementation**: poll order book every loop cycle (3-5s), compute ratio
- **Limitations**: large resting orders can be spoofed (placed and cancelled). Use as confirmation, not primary signal
- **Enhancement**: track imbalance velocity (how fast the ratio is changing) - rapid shifts are more reliable than static imbalances

### 1.6 Bollinger Bands

**Recommended: BB(10, 1.5) for scalping, standard BB(20, 2.0) as reference**
- For 1-minute scalping, use shorter period (10) and tighter bands (1.5 std dev) for faster reaction
- **Bollinger Band Width (BBW)**: `(upper - lower) / middle * 100`
  - Low BBW = squeeze = low volatility = breakout imminent
  - High BBW = expansion = trend in progress
- **Squeeze detection**: BBW below 20th percentile of last 100 bars = squeeze
- **Scalp entries**:
  - Breakout from squeeze with volume = momentum trade
  - Touch of outer band + reversal candle = mean reversion trade
  - Price walking along upper band = strong trend, don't counter-trade

### 1.7 Rate of Change (ROC)

**Optional. Simple but useful as a momentum filter.**
- ROC = `(close - close[N]) / close[N] * 100`
- For 1m: use ROC(5) or ROC(10)
- Useful as a **filter**: only take momentum trades when ROC aligns with direction
- Not recommended as a primary signal

### 1.8 ATR (Average True Range)

**Essential for risk management, useful for regime detection.**
- ATR(14) on 1m candles measures current volatility
- **ATR ratio**: `current_ATR / ATR_SMA(100)` - above 1.5 = high volatility, below 0.7 = low volatility
- Use for dynamic SL/TP sizing (see Section 7)
- Use for regime classification (see Section 6)

### Recommended Indicator Stack (Priority Order)
1. **EMA 9/21 + EMA 50** (trend direction + context)
2. **RSI 7** with 80/20 levels (momentum/extremes)
3. **VWAP** (institutional reference level)
4. **Order Book Imbalance** (short-term pressure)
5. **BBW / Bollinger Squeeze** (volatility state)
6. **CVD approximation** (hidden divergences)
7. **ATR 14** (volatility measurement for SL/TP)

---

## 2. Proven Scalp Strategies for Crypto

### 2.1 Momentum Breakout Scalping

**Setup**: Price breaks above resistance (or below support) with volume confirmation.

**Entry conditions (LONG)**:
- Price crosses above recent resistance or EMA 21
- Volume ratio >= 1.2x (above average)
- RSI 55-75 (not overbought yet)
- EMA 9 > EMA 21 (trend alignment)
- Order book: bid imbalance > 1.2

**Exit**: ATR-based TP (1.5-2.0x ATR), or trailing stop

**Win rate expectation**: 50-60% with proper filters
**Best in**: trending markets, high-volatility sessions
**Avoid in**: low volume, ranging/choppy markets

### 2.2 Mean Reversion at Key Levels

**Setup**: Price reaches extreme (S/R level, outer BB, extreme RSI) and reverses.

**Entry conditions (LONG)**:
- Price touches or pierces support / BB lower band
- RSI < 25 (extreme oversold on RSI-7)
- Reversal candle pattern (bullish engulfing, pin bar)
- CVD showing buying absorption (CVD divergence)
- Order book: heavy bids stacking near support

**Exit**: Target middle of range (VWAP or BB middle), or 1.0-1.5x ATR
**Win rate expectation**: 55-65% (higher win rate, smaller targets)
**Best in**: ranging markets, near strong S/R levels
**Avoid in**: strong trending markets (will get run over)

### 2.3 Order Flow Scalping (Order Book Imbalance)

**Setup**: Detect short-term supply/demand imbalance from order book.

**Entry conditions (LONG)**:
- Bid/Ask imbalance ratio > 1.5 (top 10 levels)
- Imbalance is increasing (velocity positive)
- Price is at or near bid side (not already moved up)
- EMA 9 not strongly bearish

**Exit**: Very fast (0.05-0.15% target), or imbalance reversal
**Win rate expectation**: 55-65% (many small wins)
**Best in**: liquid markets (BTC, ETH), active sessions
**Avoid in**: thin liquidity periods (weekends, Asian night)

### 2.4 VWAP Bounce Scalping

**Setup**: Price pulls back to VWAP and bounces in the direction of the prevailing trend.

**Entry conditions (LONG)**:
- Overall trend is bullish (price above VWAP for the session)
- Price pulls back to within 0.1% of VWAP
- RSI pulling back from overbought toward 50 but not oversold
- Volume spike on the touch
- Order book shows bids stacking at VWAP level

**Exit**: 1.0-1.5x ATR target or previous swing high
**Win rate expectation**: 55-65%
**Best in**: trending days with clear direction
**Avoid in**: choppy days where price chops around VWAP

### 2.5 Bollinger Squeeze Breakout

**Setup**: Volatility contracts (BB squeeze), then expands with a directional move.

**Entry conditions (LONG)**:
- BBW is below 20th percentile (squeeze)
- Price closes above upper BB on breakout candle
- Volume spike (>= 1.5x average)
- EMA 9 crosses above EMA 21 (or already above)

**Exit**: Ride the expansion, trail stop at 1.0x ATR below price
**Win rate expectation**: 45-55% (lower win rate, larger wins)
**Best in**: after periods of consolidation
**Avoid in**: already-expanded volatility

### Strategy Selection Matrix

| Market Condition | Primary Strategy | Secondary Strategy |
|---|---|---|
| Strong trend + high volume | Momentum Breakout | VWAP Bounce |
| Ranging + normal volume | Mean Reversion | Range Scalp at S/R |
| Low volatility (squeeze) | Wait for breakout | BB Squeeze Breakout |
| High liquidity + visible imbalance | Order Flow Scalp | Momentum Breakout |
| Near strong S/R + reversal signs | Mean Reversion | Order Flow confirmation |
| Choppy / no clear direction | **NO TRADE** | Wait |

---

## 3. Risk Management for Scalping

### 3.1 Risk Per Trade

**Recommended: 0.5-1.0% of account balance per trade (at 15x leverage)**

With 15x leverage:
- 1% account risk = a 0.067% adverse price move wipes the risk budget
- This is approximately 0.5-1.0x ATR on most 1m crypto pairs
- Position size should be calculated to risk exactly the target % at the SL distance

**Formula**: `position_size = (balance * risk_pct) / (sl_distance_pct * leverage)`

Example: $10,000 balance, 1% risk ($100), 0.1% SL distance, 15x leverage
- Position = $100 / (0.001 * 15) = $6,667 notional
- This is 66.7% of balance at 15x = ~$444 margin

### 3.2 Risk/Reward Ratios

**For scalping, 1:1 R/R is viable BUT only with win rate > 55%**

Break-even win rates (including round-trip fees):

| R/R Ratio | Break-even Win Rate (no fees) | Break-even Win Rate (0.07% RT fee*) |
|---|---|---|
| 1:1 | 50.0% | ~53.5% |
| 1:1.2 | 45.5% | ~49% |
| 1:1.5 | 40.0% | ~44% |
| 1:2.0 | 33.3% | ~37% |

*Round-trip fee: 0.02% maker + 0.05% taker = 0.07%, applied to notional position*

**Recommendation**: Target minimum 1:1.2 R/R for scalping. This gives enough cushion for slippage and allows a 50% win rate to be profitable.

### 3.3 Consecutive Loss Management

**Recommended: Pause after 3 consecutive losses in a row**

- After 3 consecutive losses: pause for 5 minutes (wait 5 candle cycles)
- After 5 consecutive losses: pause for 15 minutes
- After 7 consecutive losses: stop trading for the session (1-2 hours)
- Reset streak counter after any winning trade

### 3.4 Daily Loss Limits

**Recommended: 3% daily loss limit, 5% weekly loss limit**

- Track cumulative realized PnL per calendar day (UTC)
- When daily loss reaches -3% of starting balance: stop all new entries for the day
- Allow existing positions to close at SL/TP, but no new trades
- Weekly loss limit of -5%: if hit, reduce position size by 50% for the next week or stop entirely

### 3.5 Position Hold Time Limits

**For SCALP mode: max 15 minutes (15 candles on 1m)**

- If a position hasn't hit TP or SL within 15 candles, evaluate:
  - If in profit: close or trail stop to breakeven
  - If flat: close (dead money = opportunity cost)
  - If in small loss: close unless the original setup is still valid
- **Hard max**: 30 minutes. Close any position regardless

### 3.6 Leverage Impact (15x)

At 15x leverage:
- Every 1% price move = 15% account impact
- Liquidation distance (without margin): ~6.67% adverse move
- **With 1% account risk per trade**: you can survive 100 consecutive losing trades before ruin
- **Key principle**: leverage amplifies returns, but SL must be tight and respected

### 3.7 Max Concurrent Positions

**Recommended: 1 position per symbol, max 2-3 total**

- Scalping requires focus. Multiple positions dilute attention and increase correlation risk
- If trading multiple symbols, use uncorrelated pairs (e.g., BTC + SOL, not BTC + ETH)

---

## 4. Commission Impact Analysis

### 4.1 BingX Fee Structure (Perpetual Futures)

| Tier | Maker Fee | Taker Fee | Round-Trip (Maker/Taker) | Round-Trip (Taker/Taker) |
|---|---|---|---|---|
| Standard | 0.02% | 0.05% | 0.07% | 0.10% |
| VIP 1 | 0.014% | 0.04% | 0.054% | 0.08% |
| Supreme VIP | 0.00% | 0.028% | 0.028% | 0.056% |

### 4.2 Minimum Profitable Move

With standard fees (0.02% maker, 0.05% taker):
- **Best case (maker open, maker close)**: round-trip = 0.04%. Min profitable move = 0.04%
- **Typical case (taker open, maker close)**: round-trip = 0.07%. Min profitable move = 0.07%
- **Worst case (taker open, taker close)**: round-trip = 0.10%. Min profitable move = 0.10%

At 15x leverage, on the account:
- 0.07% price move * 15x = 1.05% account impact
- So the minimum profitable trade returns ~1% of account (before slippage)

**Key insight**: Using **limit orders** (maker fee) instead of market orders (taker fee) reduces round-trip cost by 30-57%. This is the single biggest edge for automated scalping.

### 4.3 Slippage Estimation

On BingX perpetual futures:
- BTC-USDT: typical slippage < 0.01% for positions under $50k
- ETH-USDT: typical slippage < 0.01% for positions under $20k
- Altcoins (SOL, DOGE): slippage 0.01-0.05% depending on size and time of day
- **During high volatility or news events**: slippage can spike to 0.1-0.5%

**Effective cost model**: fees (0.07%) + slippage (0.01-0.03%) = **0.08-0.10% round-trip**

### 4.4 Break-Even Analysis

With 0.08% effective round-trip cost (fees + slippage):
- At 1:1 R/R: need >54% win rate to break even
- At 1:1.5 R/R: need >44% win rate to break even

**Trades per hour sustainability**:
- Each trade costs ~0.08% of position = ~1.2% of account at 15x
- With 1% risk per trade, 10 trades/hour = 10% at-risk capital/hour
- **Recommended maximum**: 5-10 trades per hour, 30-60 trades per day
- More than this and fees eat profitability unless win rate is >60%

### 4.5 Fee Optimization Strategy

1. **Use limit orders whenever possible** (0.02% maker vs 0.05% taker)
   - For entries: place limit at current bid (long) or ask (short) with small offset
   - For exits: place limit TP orders
   - Only use market orders for emergency SL exits
2. **Increase position size, decrease trade frequency** (fewer trades, bigger moves)
3. **Trade higher-liquidity pairs** (BTC, ETH) for lower slippage
4. **Target moves >= 0.15%** as minimum (gives 2:1 ratio over costs)
5. Work toward VIP status for fee reduction

---

## 5. Time-of-Day Analysis

### 5.1 Peak Trading Hours (UTC)

| Session | UTC Hours | Characteristics |
|---|---|---|
| **Asian Open** | 00:00-03:00 | Moderate volume, often continuation |
| **Asian Active** | 03:00-08:00 | Steady but lower than Europe/US |
| **Europe Open** | 08:00-09:00 | Volume ramp-up, volatility increase |
| **Europe Active** | 09:00-14:00 | Good liquidity, medium volatility |
| **US Pre-Market** | 13:00-14:30 | Building anticipation, moderate |
| **US-Europe Overlap** | 14:30-17:00 | **PEAK LIQUIDITY & VOLATILITY** |
| **US Active** | 17:00-21:00 | High volume, major moves |
| **US Wind-Down** | 21:00-00:00 | Declining volume, often ranging |

### 5.2 Optimal Scalping Windows

**Tier 1 (Best)**: 14:30-19:00 UTC (US-Europe overlap + US active)
- Highest BTC/ETH volume
- Best liquidity (tightest spreads)
- Most predictable momentum moves
- Order book depth at maximum (e.g., $3.86M within 10bps on Binance at peak vs $2.71M off-peak)

**Tier 2 (Good)**: 08:00-12:00 UTC (Europe session)
- Good liquidity, steady trends
- Less volatile than US overlap but cleaner moves

**Tier 3 (Acceptable)**: 00:00-06:00 UTC (Asian session)
- Lower volume, wider spreads
- Smaller moves but can work for range scalping
- Altcoins especially thin

**Tier 4 (Avoid for scalping)**: 21:00-00:00 UTC (dead zone)
- Low volume, choppy
- Wider spreads, more slippage
- Higher false signal rate

### 5.3 Weekend Patterns

- Saturday/Sunday volume drops 30-50% compared to weekdays
- Spreads widen, slippage increases
- Fewer institutional participants = more random moves
- **Recommendation**: reduce position size by 50% on weekends, or avoid scalping entirely

### 5.4 Should the Bot Have Trading Sessions?

**YES - strongly recommended.** Implement session-aware trading:
- **Active mode** (14:30-19:00 UTC weekdays): full position size, all strategies
- **Normal mode** (08:00-14:30, 19:00-21:00 UTC weekdays): standard position size, momentum + mean reversion only
- **Reduced mode** (all other times + weekends): 50% position size, mean reversion only
- **Off mode** (configurable blackout windows, e.g., around major news events): no new trades

---

## 6. Market Regime Awareness

### 6.1 When NOT to Scalp

**Hard stops (no trading)**:
1. **Dead market**: ATR ratio < 0.5 AND volume ratio < 0.3 (nothing is moving)
2. **Extreme volatility spike**: ATR ratio > 3.0 (news event / black swan - spreads blow out)
3. **Consecutive loss limit hit** (see Section 3.3)
4. **Daily loss limit hit** (see Section 3.4)

### 6.2 Choppiness Detection

**Choppiness Index approach**:
- Track directional consistency of last 10-20 candles
- Count: how many candles closed in same direction as the one before them?
- If < 40% consistency = choppy market = reduce activity
- Alternative: `choppiness = sum(abs(close - open)) / abs(close[-N] - close[0])` over N bars
  - Choppiness > 2.0 = very choppy (price traveled far but went nowhere)
  - Choppiness < 1.2 = trending cleanly

**EMA 9/21 distance filter**:
- If `abs(EMA9 - EMA21) / EMA21 * 100 < 0.02%`: EMAs are flat/intertwined = choppy
- Don't trade EMA crossover signals when EMAs are this close

### 6.3 Volatility Regime Classification

| Regime | ATR Ratio | BBW Percentile | Action |
|---|---|---|---|
| **Very Low** | < 0.5 | < 10th | No trade (wait for squeeze breakout) |
| **Low** | 0.5 - 0.8 | 10th - 30th | Reduce size, mean reversion only |
| **Normal** | 0.8 - 1.5 | 30th - 70th | All strategies, normal size |
| **High** | 1.5 - 2.5 | 70th - 90th | Momentum only, tighten SL |
| **Extreme** | > 2.5 | > 90th | No new entries, manage existing |

### 6.4 Practical Chop Filter for the Bot

Implement a **combined score** (0-100):
- Directional consistency (40% weight): % of last 15 candles following trend
- EMA separation (30% weight): distance between EMA9 and EMA21 relative to ATR
- Volume trend (30% weight): recent volume vs average

Score < 30 = "CHOP" (no trade)
Score 30-50 = "UNCERTAIN" (only high-confidence setups)
Score > 50 = "TRADEABLE"

---

## 7. Position Management

### 7.1 Trailing Stop Strategies

**Recommended: ATR-based trailing stop**

For 1-minute crypto scalping:
- **Initial SL**: 1.0-1.5x ATR from entry
- **After +0.5x ATR profit**: move SL to breakeven (entry price + 1 tick)
- **After +1.0x ATR profit**: trail SL at 0.75x ATR behind current price
- **After +1.5x ATR profit**: tighten trail to 0.5x ATR behind current price

**Alternative: Candle-based trailing**
- After each new candle that closes in profit direction, move SL to low of previous candle (longs) or high of previous candle (shorts)
- Simple, effective, and adapts to volatility

### 7.2 Partial Close Strategy

**Recommended: 50/50 split**

1. **TP1** at 1.0x ATR: close 50% of position
2. **Remaining 50%**: move SL to breakeven, trail with 0.75x ATR
3. **TP2** (final): either hit trailing stop or 2.5x ATR target

**Why 50/50?**
- Locking in 50% profit reduces psychological pressure
- The trailing portion captures runners (occasional big moves)
- Mathematically: guaranteed partial profit + unlimited upside on remainder

### 7.3 Breakeven Stop - When to Move SL to Entry

Move to breakeven when:
- Price has moved >= 0.5x ATR in your favor
- At least 2 candles have closed in profit direction
- Volume is not declining sharply

**Do NOT move to breakeven too early** - being stopped at breakeven on noise is worse than holding the original SL. Give the trade room to breathe.

### 7.4 Time-Based Exits

| Candles Since Entry | Position State | Action |
|---|---|---|
| 5 candles (5 min) | In profit | Trail or partial close |
| 5 candles | Flat | Close (dead money) |
| 5 candles | Small loss | Close unless setup still valid |
| 10 candles (10 min) | In profit | Tighten trail to 0.5x ATR |
| 10 candles | Not in profit | Close regardless |
| 15 candles (15 min) | Any | Close (max hold time for scalp) |

### 7.5 Exit Signal Integration

Close position immediately (regardless of SL/TP) if:
- EMA 9 crosses against your direction AND volume spikes
- RSI hits extreme against you (RSI > 85 when long, RSI < 15 when short)
- Order book flips heavily against you (imbalance reversal)
- Large candle closes against you (> 2x ATR body)

---

## 8. Profitability Reality Check

### 8.1 Realistic Performance Expectations

Based on aggregated data from automated crypto scalping bots in 2025:

| Metric | Conservative | Moderate | Aggressive |
|---|---|---|---|
| Win rate | 52-55% | 55-62% | 62-70% |
| Avg win size | 0.15% | 0.20% | 0.25% |
| Avg loss size | 0.12% | 0.15% | 0.20% |
| R/R ratio | 1.25:1 | 1.33:1 | 1.25:1 |
| Trades per day | 10-20 | 20-40 | 40-80 |
| Daily net PnL (on capital at 15x) | 0.5-1.5% | 1.5-3.0% | 2.0-5.0% |
| Monthly return | 10-30% | 30-60% | 40-100%+ |
| Max drawdown | 5-10% | 10-20% | 15-30% |

**Caveat**: These numbers assume good execution, low slippage, and well-tuned parameters. Real-world performance is typically on the lower end.

### 8.2 Realistic Trade Count

- **Viable range**: 15-40 trades per day
- Below 10: not enough to smooth out variance (any losing streak hurts disproportionately)
- Above 60: commission drag becomes significant, signals degrade in quality
- **Sweet spot for this bot**: 20-30 trades/day (~2-4 trades/hour during active sessions)

### 8.3 Achievable Win Rate

With proper indicator stack and filters:
- **Momentum strategy**: 50-58% win rate (lower WR, higher R/R)
- **Mean reversion**: 58-65% win rate (higher WR, lower R/R)
- **Order flow**: 55-65% win rate (moderate both)
- **Blended approach**: 54-60% overall

### 8.4 Slippage on BingX

- **Market orders (BTC)**: 0.005-0.02% typical slippage
- **Market orders (altcoins)**: 0.01-0.05% typical slippage
- **Limit orders**: zero slippage (but risk of non-fill)
- **During volatility events**: slippage can be 5-10x normal
- **Recommendation**: use limit orders for entries (accept occasional non-fills), market orders only for SL

### 8.5 Automated vs Manual Scalping

| Aspect | Automated Bot | Manual Trader |
|---|---|---|
| Speed | 3-5s reaction | 1-5s+ reaction |
| Consistency | 100% rule-following | Emotional variance |
| Coverage | 24/7 possible | 4-8 hours max |
| Adaptability | Fixed rules (unless AI) | Can read context |
| Commission handling | Optimized (limit orders) | Often uses market orders |
| Drawdown control | Programmatic limits | Human discipline varies |

**Key advantage of automation**: discipline. The bot never revenge-trades, never moves SL, never hesitates.
**Key advantage of AI-augmented**: the LLM can provide contextual veto ("news event incoming, skip this trade") that pure rule-based systems miss.

---

## 9. Specific Recommendations for OpenProducerBot SCALP Strategy

### 9.1 What to Keep from Current Implementation
- ATR-based SL/TP (good foundation)
- Multiple setup types (Momentum, Mean Reversion, Range, Liquidity Grab)
- Warning system for risky conditions
- Position management rules

### 9.2 What to Add
1. **VWAP indicator** - compute from 1m candles, reset at 00:00 UTC
2. **Order book imbalance** - use existing `get_order_book()` method, compute bid/ask ratio
3. **RSI period change** - switch from 14 to 7 for SCALP mode
4. **Shorter Bollinger Bands** - BB(10, 1.5) alongside standard BB(20, 2.0)
5. **Choppiness filter** - prevent trading in directionless markets
6. **CVD approximation** - classify each 1m candle as buy/sell, track cumulative
7. **Session awareness** - adjust behavior based on UTC hour
8. **Consecutive loss tracking** - pause mechanism after 3+ losses
9. **Daily PnL tracking** - stop trading after -3% daily loss
10. **Partial close capability** - close 50% at TP1, trail rest

### 9.3 What to Change
1. **Signal scoring system** - adapt HYBRID's tiered scoring for SCALP with faster indicators
2. **Leverage adjustment** - consider 10x instead of 15x for better risk management
3. **Hold time limits** - enforce 15-candle max programmatically
4. **Fee awareness** - prioritize limit order placement, calculate net-of-fee targets
5. **Prompt simplification** - shorter, more focused prompt since decisions must be fast

### 9.4 Key Metrics to Track
- Win rate (overall and per strategy type)
- Average R/R achieved
- Average hold time
- Commission paid as % of gross PnL
- Trades per hour
- PnL by time of day (to optimize session schedule)
- PnL by regime (to tune regime filters)

---

## 10. Summary: Actionable Priorities

### Must-Have (Critical for profitability)
1. **Limit order execution** for entries (reduce fees from 0.07% to 0.04% round-trip)
2. **Choppiness filter** (avoid the #1 killer of scalping bots: trading in chop)
3. **RSI 7 with 80/20 levels** (faster signals for 1m timeframe)
4. **Consecutive loss pause** (prevent tilt/drawdown spirals)
5. **Daily loss limit** (3% hard stop)
6. **Hold time enforcement** (15 candle max)

### Should-Have (Significant edge improvement)
7. **VWAP** (institutional reference, improves entry quality)
8. **Order book imbalance** (short-term directional edge)
9. **Session-aware trading** (trade more during US-Europe overlap)
10. **Partial close** at TP1 (lock profits, let runners run)

### Nice-to-Have (Optimization)
11. **CVD approximation** (divergence detection)
12. **BB squeeze detection** (breakout anticipation)
13. **PnL-by-hour tracking** (continuous session optimization)
14. **Adaptive position sizing** (reduce after losses, increase after wins)
