# SCALP Pipeline Code Architecture

## Executive Summary

The current SCALP mode reuses the generic `process_worker.py` pipeline, which runs the same
collector -> analyzer -> AI predict -> executor -> monitor loop for all strategies. This is
fundamentally unsuitable for scalping because:

1. **Full cycle latency: 5-30s** -- the AI LLM call alone takes 3-15s, plus REST API data fetching
2. **No trailing stops** -- executor sets static SL/TP at entry and never updates them
3. **No time-based exits** -- stale positions can linger without forced close logic
4. **Analyzer is heavy** -- loads news, computes full S/R, SEB, MACD, BB every cycle
5. **No order book integration** -- `get_order_book()` and `get_ticker()` exist but are unused
6. **Signal generator is HYBRID-only** -- SCALP falls through to the raw AI path (line 279-282 in process_worker.py)

This document proposes a dedicated scalp engine with a dual-loop architecture, scalp-specific
signal generator, enhanced executor with trailing stops, and lightweight analyzer.

---

## 1. Dual-Loop Architecture for SCALP

### Current Problem

`process_worker.py:run_symbol_pipeline()` runs a single `while True` loop (line 61) that
executes every pipeline stage sequentially. For SCALP, the `loop_interval` is 3-5s (line 311
in config), but one full cycle including AI prediction takes much longer. The AI call dominates
latency and is unnecessary for most iterations.

### Proposed Design

Replace the single loop with two concurrent loops inside the worker process, using threading
(not multiprocessing, since they share state within the same worker process):

```
                    run_symbol_pipeline(symbol)
                             |
                    +--------+--------+
                    |                 |
            [Fast Loop]        [Slow Loop]
            Thread: main       Thread: daemon
            Interval: 1-2s     Interval: 30-60s
```

### Fast Loop (1-2 second interval)

Responsible for real-time position management and quick signal detection via the deterministic
`ScalpSignalGenerator`.

```python
# Pseudocode: fast_loop() in scalp_engine.py

def fast_loop(self):
    """Sub-second decision loop. Runs every 1-2s."""
    while self.running:
        try:
            start = time.time()

            # 1. Get fresh price data (from WS cache, <1ms)
            candles = self._get_candles_fast()  # ws_data_provider shared cache
            if not candles or len(candles) < 30:
                time.sleep(1)
                continue

            # 2. Get ticker (best bid/ask) - cached from WS or REST with 1s TTL
            ticker = self._get_ticker_fast()

            # 3. Compute lightweight indicators (incremental, <10ms)
            indicators = self.lightweight_analyzer.compute(candles, ticker)

            # 4. Check if we have an open position
            position = self.position_state  # In-memory, synced by slow loop

            if position:
                # === POSITION MANAGEMENT (highest priority) ===

                # 4a. Update trailing stop
                new_sl = self.trailing_manager.update(
                    position, indicators['current_price'], indicators['atr']
                )
                if new_sl != position.get('current_sl'):
                    self._update_stop_loss(new_sl)

                # 4b. Check time-based exit
                if self._should_time_exit(position):
                    self._close_position("Time-based exit")
                    continue

                # 4c. Check deterministic exit signals
                exit_signal = self.scalp_signal.check_exit(indicators, position)
                if exit_signal['should_close']:
                    self._close_position(exit_signal['reason'])
                    continue

                # 4d. Check breakeven move
                if self._should_move_to_breakeven(position, indicators):
                    self._move_to_breakeven(position)

            else:
                # === SIGNAL DETECTION (entry) ===

                # 5a. Check session limits (max trades/hour, daily loss)
                if not self.session.can_trade():
                    continue

                # 5b. Generate deterministic scalp signal
                signal = self.scalp_signal.generate(indicators, self.slow_context)

                if signal['signal'] != 'HOLD' and signal['quality'] >= self.min_quality:
                    # 5c. Quick risk check (no AI needed for high-quality signals)
                    if signal['quality'] >= self.auto_execute_quality:
                        self._execute_entry(signal, indicators)
                    else:
                        # Queue for AI veto in slow loop
                        self.pending_signal = signal

            elapsed = time.time() - start
            sleep_time = max(0.1, self.fast_interval - elapsed)
            time.sleep(sleep_time)

        except Exception as e:
            logger.error(f"Fast loop error: {e}")
            time.sleep(1)
```

### Slow Loop (30-60 second interval)

Responsible for full analysis, regime detection, AI consultation for borderline signals, and
position state synchronization.

```python
# Pseudocode: slow_loop() in scalp_engine.py

def slow_loop(self):
    """Full analysis loop. Runs every 30-60s in daemon thread."""
    while self.running:
        try:
            # 1. Sync position state from exchange (authoritative source)
            self._sync_position_from_exchange()

            # 2. Full indicator computation (with S/R, regime, etc.)
            full_analysis = self.full_analyzer.analyze(self.symbol)
            self.slow_context = full_analysis  # Shared with fast loop (thread-safe dict)

            # 3. Regime detection
            regime = detect_regime(full_analysis)
            self.slow_context['regime'] = regime

            # 4. Process pending AI veto (if fast loop queued a borderline signal)
            if self.pending_signal and not self.position_state:
                prediction = self._ai_veto(self.pending_signal, full_analysis)
                if prediction.get('action') in ('buy', 'sell'):
                    self._execute_entry_from_prediction(prediction)
                self.pending_signal = None

            # 5. Performance tracking + calibration
            self.session.update_stats()

            # 6. Config hot-reload check
            self._check_config_reload()

            time.sleep(self.slow_interval)

        except Exception as e:
            logger.error(f"Slow loop error: {e}")
            time.sleep(5)
```

### Integration Point: process_worker.py

The existing `run_symbol_pipeline()` (line 11) will get a strategy branch at the top:

```python
def run_symbol_pipeline(symbol: str, ws_cache=None, ws_ready=None):
    # ... existing setup code (lines 21-56) ...

    from src.config import STRATEGY_STYLE

    if STRATEGY_STYLE == "SCALP":
        # Dedicated scalp engine with dual-loop
        from src.core.scalp_engine import ScalpEngine
        engine = ScalpEngine(symbol, ws_cache, ws_ready)
        engine.run()  # Blocks forever (internal dual-loop)
        return

    # ... existing while True loop for other strategies (lines 61-395) ...
```

This is a clean branching point: SCALP gets its own engine, other strategies continue unchanged.

---

## 2. ScalpSignalGenerator

### Design Goals

- Compute in **< 5ms** per call (fast loop budget)
- Use only indicators that can be updated incrementally
- Separate entry signals from exit signals
- Score-based with configurable weights (like HYBRID's `SignalGenerator` but tuned for 1m TF)

### Class Structure

```python
# src/core/scalp_signal.py

class ScalpSignalGenerator:
    """
    Deterministic signal generator optimized for 1m scalping.
    Maintains incremental indicator state to avoid full recalculation.
    """

    def __init__(self, config: dict):
        self.config = config  # SCALP_SETTINGS from bot_config.json
        self.rules = config.get("signal_rules", {})

        # Incremental indicator cache
        self._ema_fast = None     # EMA-5 (ultra-fast)
        self._ema_medium = None   # EMA-13 (fast)
        self._ema_slow = None     # EMA-21 (medium)
        self._rsi_state = None    # Wilder's smoothed RSI state
        self._atr_state = None    # ATR running average
        self._vwap = None         # Volume-Weighted Average Price (session)
        self._last_candle_ts = 0  # For detecting new candles

    def generate(self, indicators: dict, slow_context: dict = None) -> dict:
        """
        Generate entry signal for scalp mode.

        Args:
            indicators: From LightweightAnalyzer (current_price, ema5, ema13, ema21,
                       rsi, atr, volume_ratio, bid_ask_imbalance, vwap, bb_upper, bb_lower)
            slow_context: From slow loop (regime, S/R levels, etc.) - may be stale

        Returns:
            dict: {signal, score, max_score, quality, confidence, reasons, details}
        """

    def check_exit(self, indicators: dict, position: dict) -> dict:
        """
        Check deterministic exit conditions for open position.

        Returns:
            dict: {should_close, reason, urgency}
        """
```

### Indicator Selection for SCALP

Chosen for speed and relevance to 1-minute timeframe:

| Indicator | Weight | Category | Reason |
|-----------|--------|----------|--------|
| EMA Cross (5/13) | 2 | Tier 1: Direction | Ultra-fast trend detection, responds to 1m moves |
| RSI (7-period) | 2 | Tier 1: Momentum | Shorter period = faster signal for scalp |
| Order Book Imbalance | 2 | Tier 2: Microstructure | Bid/ask volume ratio indicates short-term pressure |
| VWAP distance | 1 | Tier 2: Mean-reversion | Price vs session VWAP for reversion entries |
| Volume spike | 1 | Tier 3: Confirmation | Sudden volume = institutional interest |
| Bollinger Band touch | 1 | Tier 3: Confirmation | Mean reversion signal at extremes |
| Momentum (3-candle) | 1 | Tier 3: Confirmation | 3 consecutive candles in direction |

**Max score: 10.** Same scale as HYBRID for consistency, but different indicators and shorter periods.

### Entry Signal Patterns

Three distinct scalp entry patterns, each producing a directional signal:

**Pattern 1: Momentum Breakout**
- EMA5 > EMA13 > EMA21 (all aligned)
- RSI between 55-75 (bullish momentum, not overbought)
- Volume spike > 1.5x average
- Produces BUY with high confidence

**Pattern 2: Mean Reversion**
- Price touches or crosses lower Bollinger Band
- RSI < 30 (oversold)
- Order book bid imbalance > 1.5 (more buyers than sellers)
- VWAP is above current price (mean is higher)
- Produces BUY with medium confidence

**Pattern 3: Pullback Entry**
- EMA21 trending up (slow context)
- Price pulls back to EMA13 (1-3 candles red)
- RSI bounces off 40-50 zone (healthy retracement)
- Volume decreasing on pullback (exhaustion)
- Produces BUY with high confidence

Mirror patterns for SELL.

### Exit Signal Logic

```python
def check_exit(self, indicators: dict, position: dict) -> dict:
    """Deterministic exit rules for scalp positions."""
    current_price = indicators['current_price']
    entry_price = position['entry_price']
    pos_type = position['type']  # BUY or SELL
    age_seconds = time.time() - position['entry_time']

    # Calculate PnL
    if pos_type == 'BUY':
        pnl_pct = (current_price - entry_price) / entry_price * 100
    else:
        pnl_pct = (entry_price - current_price) / entry_price * 100

    # EXIT 1: RSI extreme reversal (highest priority)
    if pos_type == 'BUY' and indicators['rsi'] > 80:
        return {'should_close': True, 'reason': f'RSI extreme {indicators["rsi"]:.0f}', 'urgency': 'high'}
    if pos_type == 'SELL' and indicators['rsi'] < 20:
        return {'should_close': True, 'reason': f'RSI extreme {indicators["rsi"]:.0f}', 'urgency': 'high'}

    # EXIT 2: EMA cross against position
    if pos_type == 'BUY' and indicators['ema5'] < indicators['ema13'] and pnl_pct < 0.1:
        return {'should_close': True, 'reason': 'EMA cross against BUY', 'urgency': 'medium'}
    if pos_type == 'SELL' and indicators['ema5'] > indicators['ema13'] and pnl_pct < 0.1:
        return {'should_close': True, 'reason': 'EMA cross against SELL', 'urgency': 'medium'}

    # EXIT 3: Profit target reached (if trailing stop not active yet)
    max_hold_minutes = self.config.get('max_hold_minutes', 15)
    if pnl_pct >= 0.3 and age_seconds > 60:
        # Momentum fading check
        if pos_type == 'BUY' and indicators['rsi'] > 70:
            return {'should_close': True, 'reason': f'Profit lock +{pnl_pct:.2f}%', 'urgency': 'medium'}
        if pos_type == 'SELL' and indicators['rsi'] < 30:
            return {'should_close': True, 'reason': f'Profit lock +{pnl_pct:.2f}%', 'urgency': 'medium'}

    # EXIT 4: Time-based forced close (handled separately in fast loop, but as fallback here)
    if age_seconds > max_hold_minutes * 60:
        return {'should_close': True, 'reason': f'Max hold time ({max_hold_minutes}m)', 'urgency': 'high'}

    return {'should_close': False, 'reason': 'No exit signal', 'urgency': 'low'}
```

### Incremental Indicator Updates

For the fast loop to achieve < 5ms latency, indicators must be updated incrementally
rather than recalculated from scratch:

```python
def _update_ema_incremental(self, ema_prev: float, new_price: float, period: int) -> float:
    """O(1) EMA update using previous value."""
    multiplier = 2 / (period + 1)
    return (new_price - ema_prev) * multiplier + ema_prev

def _update_rsi_incremental(self, state: dict, new_close: float) -> float:
    """O(1) RSI update using Wilder's smoothing (no array operations)."""
    delta = new_close - state['prev_close']
    gain = max(0, delta)
    loss = max(0, -delta)

    period = state['period']
    state['avg_gain'] = (state['avg_gain'] * (period - 1) + gain) / period
    state['avg_loss'] = (state['avg_loss'] * (period - 1) + loss) / period
    state['prev_close'] = new_close

    if state['avg_loss'] == 0:
        return 100.0
    rs = state['avg_gain'] / state['avg_loss']
    return 100 - (100 / (1 + rs))
```

---

## 3. Scalp Executor Enhancements

### Current Problem

`executor.py:create_order()` (line 23) sets static SL/TP at entry time and never updates them.
For scalping, we need active position management with trailing stops and breakeven moves.

### Trailing Stop Implementation

```python
# src/core/scalp_engine.py (TrailingStopManager inner class or separate module)

class TrailingStopManager:
    """
    Manages trailing stop for scalp positions.

    Modes:
    - ATR-based: Trail at current_price - N*ATR (dynamic)
    - Percentage-based: Trail at current_price * (1 - trail_pct) (simple)
    - Stepped: Lock in profit at predefined levels
    """

    def __init__(self, config: dict):
        self.mode = config.get("trailing_mode", "atr")  # "atr", "percent", "stepped"
        self.atr_multiplier = config.get("trailing_atr_mult", 1.0)
        self.trail_pct = config.get("trailing_percent", 0.15)
        self.activation_pct = config.get("trailing_activation_pct", 0.2)
        self.stepped_levels = config.get("trailing_stepped_levels", [0.2, 0.4, 0.8, 1.2])

    def update(self, position: dict, current_price: float, atr: float) -> float:
        """
        Calculate new trailing stop level.
        Returns the new SL price (only moves in profitable direction, never backwards).

        Args:
            position: {type, entry_price, current_sl, highest_price/lowest_price}
            current_price: Current market price
            atr: Current ATR value

        Returns:
            float: New stop loss price (may be same as current if no update needed)
        """
        entry = position['entry_price']
        pos_type = position['type']
        current_sl = position.get('current_sl', 0)

        if pos_type == 'BUY':
            pnl_pct = (current_price - entry) / entry * 100

            # Don't activate trailing until minimum profit reached
            if pnl_pct < self.activation_pct:
                return current_sl

            # Track highest price since entry
            highest = max(position.get('highest_price', entry), current_price)
            position['highest_price'] = highest

            if self.mode == 'atr':
                new_sl = highest - (atr * self.atr_multiplier)
            elif self.mode == 'percent':
                new_sl = highest * (1 - self.trail_pct / 100)
            elif self.mode == 'stepped':
                new_sl = self._stepped_sl(entry, pnl_pct, pos_type)
            else:
                new_sl = current_sl

            # Trailing stop only moves UP for BUY
            return max(current_sl, new_sl) if new_sl > 0 else current_sl

        else:  # SELL
            pnl_pct = (entry - current_price) / entry * 100

            if pnl_pct < self.activation_pct:
                return current_sl

            lowest = min(position.get('lowest_price', entry), current_price)
            position['lowest_price'] = lowest

            if self.mode == 'atr':
                new_sl = lowest + (atr * self.atr_multiplier)
            elif self.mode == 'percent':
                new_sl = lowest * (1 + self.trail_pct / 100)
            elif self.mode == 'stepped':
                new_sl = self._stepped_sl(entry, pnl_pct, pos_type)
            else:
                new_sl = current_sl

            # Trailing stop only moves DOWN for SELL
            if current_sl > 0:
                return min(current_sl, new_sl) if new_sl > 0 else current_sl
            return new_sl

    def _stepped_sl(self, entry: float, pnl_pct: float, pos_type: str) -> float:
        """Stepped trailing: lock in profit at predefined levels."""
        # Find the highest level we've passed
        locked_level = 0
        for level in sorted(self.stepped_levels):
            if pnl_pct >= level:
                locked_level = level * 0.5  # Lock in 50% of the reached level
        if locked_level <= 0:
            return 0
        if pos_type == 'BUY':
            return entry * (1 + locked_level / 100)
        else:
            return entry * (1 - locked_level / 100)
```

### Breakeven Logic

```python
def _should_move_to_breakeven(self, position: dict, indicators: dict) -> bool:
    """Check if we should move SL to breakeven (entry price + small buffer)."""
    entry = position['entry_price']
    current = indicators['current_price']
    pos_type = position['type']
    breakeven_trigger = self.config.get('breakeven_trigger_pct', 0.3)
    already_at_breakeven = position.get('at_breakeven', False)

    if already_at_breakeven:
        return False

    if pos_type == 'BUY':
        pnl_pct = (current - entry) / entry * 100
    else:
        pnl_pct = (entry - current) / entry * 100

    return pnl_pct >= breakeven_trigger

def _move_to_breakeven(self, position: dict):
    """Move SL to entry price + buffer (covers fees)."""
    entry = position['entry_price']
    fee_buffer_pct = 0.05  # 0.05% to cover round-trip fees
    pos_type = position['type']

    if pos_type == 'BUY':
        new_sl = entry * (1 + fee_buffer_pct / 100)
    else:
        new_sl = entry * (1 - fee_buffer_pct / 100)

    self._update_stop_loss(new_sl)
    position['at_breakeven'] = True
    info(f"[SCALP] Moved to breakeven: SL={new_sl:.4f}")
```

### Time-Based Exit

```python
def _should_time_exit(self, position: dict) -> bool:
    """Force close after max hold time (configurable, default 15 minutes)."""
    max_hold_sec = self.config.get('max_hold_minutes', 15) * 60
    age = time.time() - position.get('entry_time', time.time())
    return age > max_hold_sec
```

### Partial Close Strategy

```python
def _check_partial_close(self, position: dict, indicators: dict) -> Optional[float]:
    """
    Returns fraction to close (0.0-1.0) or None if no partial close needed.
    Strategy: Close 50% at first TP target, let rest ride with trailing stop.
    """
    pnl_pct = self._calc_pnl_pct(position, indicators['current_price'])
    partial_trigger = self.config.get('partial_close_trigger_pct', 0.5)
    partial_fraction = self.config.get('partial_close_fraction', 0.5)

    if pnl_pct >= partial_trigger and not position.get('partial_closed'):
        position['partial_closed'] = True
        return partial_fraction

    return None
```

### Executor Integration

The existing `executor.py` does not need fundamental changes. The ScalpEngine will call
`create_order()` for entries and `client.close_position()` for exits directly. SL/TP updates
go through `client.set_sl_tp()`. The key change is that the **fast loop calls these methods
directly** rather than building prediction dicts and routing through `execute_prediction()`.

```python
# In ScalpEngine:
def _execute_entry(self, signal: dict, indicators: dict):
    """Execute entry from deterministic signal (no AI needed)."""
    from src.core.executor import create_order
    from src.core.risk_manager import calculate_dynamic_sl_tp

    sl_tp = calculate_dynamic_sl_tp(
        signal=signal['signal'],
        current_price=indicators['current_price'],
        atr=indicators['atr'],
        support=self.slow_context.get('support', 0),
        resistance=self.slow_context.get('resistance', 0),
        regime=self.slow_context.get('regime', {}),
        quality=signal['quality']
    )

    order_id = create_order(
        symbol=self.symbol,
        direction=signal['signal'],
        price=indicators['current_price'],
        ai_sl=sl_tp['stop_loss'],
        ai_tp=sl_tp['take_profit'],
        reason=f"[SCALP] {', '.join(signal['reasons'][:3])}",
        confidence=signal['confidence'],
        size_pct=None  # Use default from config
    )

    if order_id:
        self.position_state = {
            'type': signal['signal'],
            'entry_price': indicators['current_price'],
            'entry_time': time.time(),
            'current_sl': sl_tp['stop_loss'],
            'current_tp': sl_tp['take_profit'],
            'highest_price': indicators['current_price'],
            'lowest_price': indicators['current_price'],
            'at_breakeven': False,
            'partial_closed': False,
            'order_id': order_id,
        }
        self.session.record_entry(signal)
```

---

## 4. Lightweight Analyzer for SCALP

### Current Problem

`analyzer.py:analyze_symbol()` (line 402) performs the following on every call:
1. Loads full price data from JSON file on disk
2. Loads news data from JSON file
3. Computes full indicator suite (SMA, RSI, EMA, ATR, MACD, BB, SEB, S/R levels)
4. Builds smart-sampled candle history table for AI prompt
5. Assembles full PromptBuilder prompt

Steps 1-2 involve disk I/O. Step 3 is ~50-200ms with numpy (SEB uses rolling linear regression).
Steps 4-5 are wasted work if AI won't be called.

### Lightweight Analyzer Design

A new class that computes only scalp-relevant indicators using in-memory data:

```python
# src/core/lightweight_analyzer.py

class LightweightAnalyzer:
    """
    Minimal indicator computation for scalp fast loop.
    Uses WebSocket cache instead of disk I/O.
    Computes only fast indicators with incremental updates.
    Target: < 10ms per call.
    """

    def __init__(self, symbol: str, config: dict):
        self.symbol = symbol
        self.config = config

        # Incremental state
        self._ema5 = None
        self._ema13 = None
        self._ema21 = None
        self._rsi_state = None   # {avg_gain, avg_loss, prev_close, period}
        self._atr_buffer = []    # deque(maxlen=14) for rolling ATR
        self._volume_buffer = [] # deque(maxlen=20) for volume average
        self._bb_state = None    # {prices: deque(maxlen=20)}
        self._initialized = False
        self._last_candle_ts = 0

    def compute(self, candles: list, ticker: dict = None) -> dict:
        """
        Compute scalp indicators from candle data.

        Args:
            candles: List of candle dicts from WS cache
                     [{snapshotTimeUTC, openPrice, highPrice, lowPrice, closePrice, volume}, ...]
            ticker: Optional {bid, ask, last, volume} from get_ticker()

        Returns:
            dict with all scalp-relevant indicators
        """
        if not candles:
            return {}

        # Check for new candle
        latest = candles[-1]
        latest_ts = latest.get('timestamp', 0) or 0

        if not self._initialized:
            self._bootstrap(candles)
            self._initialized = True

        elif latest_ts != self._last_candle_ts:
            # New candle arrived: update incrementally
            self._update_incremental(latest)
            self._last_candle_ts = latest_ts

        else:
            # Same candle updating (intra-candle): update live price only
            self._update_live(latest)

        # Current price (prefer ticker for best precision)
        current_price = ticker['last'] if ticker and ticker.get('last') else \
                        float(latest.get('closePrice', 0))

        # Bid/Ask imbalance from ticker
        bid_ask_imbalance = 1.0
        if ticker and ticker.get('bid') and ticker.get('ask'):
            # Simple imbalance: > 1.0 means more buying pressure
            bid_ask_imbalance = ticker['bid'] / ticker['ask'] if ticker['ask'] > 0 else 1.0

        # Bollinger Bands
        bb_upper, bb_middle, bb_lower = self._compute_bb()

        # Volume ratio
        avg_vol = sum(self._volume_buffer) / len(self._volume_buffer) \
                  if self._volume_buffer else 1.0
        current_vol = float(latest.get('volume', 0))
        volume_ratio = current_vol / avg_vol if avg_vol > 0 else 1.0

        # Momentum: last 3 candles direction
        close_prices = [float(c.get('closePrice', 0)) for c in candles[-5:]]
        if len(close_prices) >= 4:
            up = sum(1 for i in range(1, len(close_prices)) if close_prices[i] > close_prices[i-1])
            momentum_3 = "UP" if up >= 3 else "DOWN" if up <= 1 else "MIXED"
        else:
            momentum_3 = "MIXED"

        return {
            'current_price': current_price,
            'ema5': self._ema5,
            'ema13': self._ema13,
            'ema21': self._ema21,
            'rsi': self._current_rsi,
            'atr': self._current_atr,
            'bb_upper': bb_upper,
            'bb_middle': bb_middle,
            'bb_lower': bb_lower,
            'volume_ratio': volume_ratio,
            'bid_ask_imbalance': bid_ask_imbalance,
            'momentum_3': momentum_3,
            'bid': ticker.get('bid', 0) if ticker else 0,
            'ask': ticker.get('ask', 0) if ticker else 0,
        }

    def _bootstrap(self, candles: list):
        """Initialize all indicators from full candle history (called once)."""
        closes = [float(c.get('closePrice', 0)) for c in candles]
        highs = [float(c.get('highPrice', 0)) for c in candles]
        lows = [float(c.get('lowPrice', 0)) for c in candles]
        volumes = [float(c.get('volume', 0)) for c in candles]

        # EMA bootstrap
        self._ema5 = self._calc_full_ema(closes, 5)
        self._ema13 = self._calc_full_ema(closes, 13)
        self._ema21 = self._calc_full_ema(closes, 21)

        # RSI bootstrap (Wilder smoothing)
        self._rsi_state = self._bootstrap_rsi(closes, period=7)
        self._current_rsi = self._rsi_state.get('current_rsi', 50)

        # ATR bootstrap
        self._current_atr = self._bootstrap_atr(highs, lows, closes, period=14)

        # Volume buffer
        from collections import deque
        self._volume_buffer = deque(volumes[-20:], maxlen=20)

        # BB state
        self._bb_prices = deque(closes[-20:], maxlen=20)

        self._last_candle_ts = candles[-1].get('timestamp', 0) or 0

    def _update_incremental(self, candle: dict):
        """O(1) update for new completed candle."""
        close = float(candle.get('closePrice', 0))
        high = float(candle.get('highPrice', 0))
        low = float(candle.get('lowPrice', 0))
        volume = float(candle.get('volume', 0))

        # EMA incremental
        self._ema5 = self._update_ema(self._ema5, close, 5)
        self._ema13 = self._update_ema(self._ema13, close, 13)
        self._ema21 = self._update_ema(self._ema21, close, 21)

        # RSI incremental
        self._current_rsi = self._update_rsi(self._rsi_state, close)

        # ATR (simplified: use close-to-close changes)
        # Full TR requires prev_close tracking (done in bootstrap)
        self._volume_buffer.append(volume)
        self._bb_prices.append(close)

    @staticmethod
    def _update_ema(prev_ema: float, new_price: float, period: int) -> float:
        k = 2 / (period + 1)
        return (new_price - prev_ema) * k + prev_ema

    def _compute_bb(self, period=20, mult=2.0):
        """Compute Bollinger Bands from deque."""
        if len(self._bb_prices) < period:
            return 0, 0, 0
        prices = list(self._bb_prices)[-period:]
        middle = sum(prices) / period
        variance = sum((p - middle) ** 2 for p in prices) / period
        std = variance ** 0.5
        return middle + mult * std, middle, middle - mult * std
```

### Data Flow: WebSocket Cache -> Lightweight Analyzer

```
WebSocket Provider (ws_data_provider.py)
    |
    | get_klines_from_shared_cache(symbol, limit=100)
    |   -- Returns list of candle dicts from Manager.dict() proxy
    |   -- Already in unified format (snapshotTimeUTC, OHLCV)
    |   -- Latest candle updates in real-time via WS
    v
LightweightAnalyzer.compute(candles, ticker)
    |
    | -- First call: _bootstrap() from 100 candles (~5ms)
    | -- Subsequent calls: _update_incremental() per new candle (~0.1ms)
    | -- Intra-candle: _update_live() updates only current price (~0.05ms)
    v
ScalpSignalGenerator.generate(indicators, slow_context)
```

Key difference from current flow: **no disk I/O, no JSON file loading, no news fetching,
no SEB computation, no prompt building** in the fast path.

### Full Analyzer for Slow Loop

The slow loop still uses the existing `analyzer.py` for full analysis, but with two modifications:
1. Skip news fetching (already controlled by `ENABLE_NEWS` config, set to false for scalp)
2. Pass results to `slow_context` dict shared with fast loop (read-only from fast loop's perspective)

The full analyzer provides S/R levels, regime data, and complete indicator suite that the fast
loop can reference for context without computing them itself.

---

## 5. Config Changes

### New SCALP_SETTINGS section in bot_config.json

```json
{
  "SCALP_SETTINGS": {
    "enabled": true,

    "signal_rules": {
      "ema_fast_period": 5,
      "ema_medium_period": 13,
      "ema_slow_period": 21,
      "rsi_period": 7,
      "bb_period": 20,
      "bb_multiplier": 2.0,

      "ema_cross_weight": 2,
      "rsi_zone_weight": 2,
      "orderbook_weight": 2,
      "vwap_weight": 1,
      "volume_weight": 1,
      "bb_weight": 1,
      "momentum_weight": 1,

      "min_score_for_signal": 5,
      "min_volume_ratio": 0.5,
      "min_atr_ratio": 0.3,
      "auto_execute_quality": 0.6,
      "min_quality_for_entry": 0.3,

      "rsi_long_max": 45,
      "rsi_long_min": 15,
      "rsi_short_max": 85,
      "rsi_short_min": 55,

      "orderbook_imbalance_threshold": 1.3,
      "vwap_distance_pct": 0.3
    },

    "exit_rules": {
      "rsi_extreme_long": 80,
      "rsi_extreme_short": 20,
      "ema_cross_exit": true,
      "profit_lock_pct": 0.3,
      "profit_lock_rsi_long": 70,
      "profit_lock_rsi_short": 30
    },

    "trailing_stop": {
      "enabled": true,
      "mode": "atr",
      "trailing_atr_mult": 1.0,
      "trailing_percent": 0.15,
      "activation_pct": 0.2,
      "stepped_levels": [0.2, 0.4, 0.8, 1.2]
    },

    "breakeven": {
      "enabled": true,
      "trigger_pct": 0.3,
      "fee_buffer_pct": 0.05
    },

    "time_exit": {
      "enabled": true,
      "max_hold_minutes": 15,
      "stale_position_minutes": 30
    },

    "partial_close": {
      "enabled": false,
      "trigger_pct": 0.5,
      "close_fraction": 0.5
    },

    "session": {
      "max_trades_per_hour": 10,
      "max_trades_per_day": 50,
      "daily_loss_limit_pct": 5.0,
      "cooldown_after_loss_seconds": 30,
      "cooldown_after_3_losses_seconds": 300
    },

    "loops": {
      "fast_interval": 1.5,
      "slow_interval": 45,
      "ticker_cache_ttl": 1.0
    },

    "ai_integration": {
      "enabled": true,
      "invoke_on_borderline": true,
      "borderline_quality_threshold": 0.3,
      "max_ai_calls_per_hour": 20
    }
  }
}
```

### STYLE_PRESETS.SCALP updates

```json
{
  "SCALP": {
    "timeframe": "1m",
    "chart_period": "6h",
    "plotter_period": "30m",
    "loop_interval": 1.5,
    "position_check_interval": 1,
    "atr_sl_mult": 1.5,
    "atr_tp_mult": 2.0,
    "leverage": 15,
    "description": "Dual-loop scalp engine with deterministic signals and trailing stops."
  }
}
```

---

## 6. New File: src/core/scalp_engine.py

### Complete Class Structure

```python
"""
Dedicated Scalp Engine with dual-loop architecture.
Replaces the generic process_worker pipeline for SCALP strategy.

Architecture:
- Fast Loop (1-2s): Position management, trailing stops, quick signal detection
- Slow Loop (30-60s): Full analysis, regime detection, AI veto, state sync

Key differences from generic pipeline:
1. Uses ScalpSignalGenerator (not AI) for primary decisions
2. Maintains in-memory position state with trailing stop tracking
3. WebSocket-first data access (no disk I/O in fast path)
4. Session management (trade limits, daily loss limit)
"""

import time
import threading
from typing import Optional, Dict

from src.utils.logger import setup_symbol_logger, info, error, warning
from src.config import BOT_CONFIG


class ScalpEngine:
    """Main scalp engine class. One instance per symbol per worker process."""

    def __init__(self, symbol: str, ws_cache=None, ws_ready=None):
        self.symbol = symbol
        self.ws_cache = ws_cache
        self.ws_ready = ws_ready
        self.running = False

        # Configuration
        self.config = BOT_CONFIG.get("SCALP_SETTINGS", {})
        loop_config = self.config.get("loops", {})
        self.fast_interval = loop_config.get("fast_interval", 1.5)
        self.slow_interval = loop_config.get("slow_interval", 45)

        # State (shared between fast/slow loops via threading)
        self.position_state: Optional[Dict] = None   # In-memory position tracking
        self.slow_context: Dict = {}                  # Full analysis from slow loop
        self.pending_signal: Optional[Dict] = None    # Queued for AI veto
        self._lock = threading.Lock()                 # For shared state access

        # Sub-components (initialized in run())
        self.lightweight_analyzer = None   # LightweightAnalyzer instance
        self.scalp_signal = None           # ScalpSignalGenerator instance
        self.trailing_manager = None       # TrailingStopManager instance
        self.session = None                # ScalpSession instance

    def run(self):
        """
        Main entry point. Starts both loops and blocks forever.
        Called from process_worker.py when STRATEGY_STYLE == "SCALP".
        """
        setup_symbol_logger(self.symbol)
        info(f"[SCALP ENGINE] Starting for {self.symbol}")

        self._initialize_components()
        self._startup_sync()

        self.running = True

        # Start slow loop in daemon thread
        slow_thread = threading.Thread(target=self._slow_loop, daemon=True)
        slow_thread.start()

        # Run fast loop in main thread (blocks)
        try:
            self._fast_loop()
        except KeyboardInterrupt:
            info(f"[SCALP ENGINE] {self.symbol} stopped by user")
        finally:
            self.running = False

    def _initialize_components(self):
        """Create all sub-components."""
        from src.core.lightweight_analyzer import LightweightAnalyzer
        from src.core.scalp_signal import ScalpSignalGenerator
        # TrailingStopManager and ScalpSession defined in this file or separate

        self.lightweight_analyzer = LightweightAnalyzer(self.symbol, self.config)
        self.scalp_signal = ScalpSignalGenerator(self.config)
        self.trailing_manager = TrailingStopManager(self.config.get("trailing_stop", {}))
        self.session = ScalpSession(self.config.get("session", {}))

    def _startup_sync(self):
        """Sync position state from exchange on startup."""
        from src.exchanges.exchange_factory import get_exchange_client
        try:
            client = get_exchange_client()
            positions = client.get_positions()
            symbol_pos = positions.get(self.symbol, [])
            if symbol_pos:
                pos = symbol_pos[0]
                self.position_state = {
                    'type': pos['type'].upper(),
                    'entry_price': float(pos['entry']),
                    'entry_time': time.time(),  # Unknown actual, use now
                    'current_sl': 0,
                    'current_tp': 0,
                    'highest_price': float(pos['entry']),
                    'lowest_price': float(pos['entry']),
                    'at_breakeven': False,
                    'partial_closed': False,
                    'deal_id': pos.get('dealId', ''),
                }
                info(f"[SCALP ENGINE] Existing position found: {pos['type']} @ {pos['entry']}")
        except Exception as e:
            warning(f"[SCALP ENGINE] Startup sync failed: {e}")

    def _fast_loop(self):
        """Fast loop: 1-2s interval. Position management + quick signals."""
        # [See Section 1 pseudocode above for full implementation]
        ...

    def _slow_loop(self):
        """Slow loop: 30-60s interval. Full analysis + AI + sync."""
        # [See Section 1 pseudocode above for full implementation]
        ...

    # --- Data Access Methods ---

    def _get_candles_fast(self) -> list:
        """Get candles from WS shared cache (< 1ms)."""
        from src.exchanges.ws_data_provider import get_klines_from_shared_cache, is_cache_ready
        if not is_cache_ready(self.symbol):
            return []
        return get_klines_from_shared_cache(self.symbol, limit=100)

    def _get_ticker_fast(self) -> dict:
        """Get ticker with short-TTL cache."""
        # Cache ticker for 1 second to avoid hammering REST API
        now = time.time()
        if hasattr(self, '_ticker_cache') and now - self._ticker_cache_time < 1.0:
            return self._ticker_cache

        from src.exchanges.exchange_factory import get_exchange_client
        try:
            ticker = get_exchange_client().get_ticker(self.symbol)
            self._ticker_cache = ticker
            self._ticker_cache_time = now
            return ticker
        except Exception:
            return getattr(self, '_ticker_cache', {})

    # --- Execution Methods ---

    def _execute_entry(self, signal: dict, indicators: dict):
        """Execute entry from deterministic signal."""
        # [See Section 3 for full implementation]
        ...

    def _close_position(self, reason: str):
        """Close current position."""
        from src.exchanges.exchange_factory import get_exchange_client
        client = get_exchange_client()
        if self.position_state and self.position_state.get('deal_id'):
            client.close_position(self.symbol, self.position_state['deal_id'])
            self.session.record_exit(self.position_state, reason)
            self.position_state = None
            info(f"[SCALP] Position closed: {reason}")

    def _update_stop_loss(self, new_sl: float):
        """Update SL on exchange."""
        from src.exchanges.exchange_factory import get_exchange_client
        if not self.position_state:
            return
        client = get_exchange_client()
        pos_side = "LONG" if self.position_state['type'] == 'BUY' else "SHORT"
        try:
            client.set_sl_tp(self.symbol, pos_side, sl=new_sl)
            self.position_state['current_sl'] = new_sl
        except Exception as e:
            warning(f"[SCALP] SL update failed: {e}")


class TrailingStopManager:
    """[See Section 3 for full implementation]"""
    ...


class ScalpSession:
    """
    Tracks session-level statistics and enforces trading limits.
    """

    def __init__(self, config: dict):
        self.config = config
        self.max_trades_per_hour = config.get("max_trades_per_hour", 10)
        self.max_trades_per_day = config.get("max_trades_per_day", 50)
        self.daily_loss_limit_pct = config.get("daily_loss_limit_pct", 5.0)
        self.cooldown_after_loss = config.get("cooldown_after_loss_seconds", 30)
        self.cooldown_after_3_losses = config.get("cooldown_after_3_losses_seconds", 300)

        # State
        self.trades_this_hour = 0
        self.trades_today = 0
        self.wins_today = 0
        self.losses_today = 0
        self.consecutive_losses = 0
        self.daily_pnl_pct = 0.0
        self.last_trade_time = 0
        self.cooldown_until = 0
        self.hour_start = time.time()
        self.day_start = time.time()

    def can_trade(self) -> bool:
        """Check if we're allowed to take a new trade."""
        now = time.time()

        # Reset hourly counter
        if now - self.hour_start > 3600:
            self.trades_this_hour = 0
            self.hour_start = now

        # Reset daily counter
        if now - self.day_start > 86400:
            self._reset_daily()

        # Cooldown check
        if now < self.cooldown_until:
            return False

        # Trade limits
        if self.trades_this_hour >= self.max_trades_per_hour:
            return False
        if self.trades_today >= self.max_trades_per_day:
            return False

        # Daily loss limit
        if self.daily_pnl_pct <= -self.daily_loss_limit_pct:
            return False

        return True

    def record_entry(self, signal: dict):
        """Record a new trade entry."""
        self.trades_this_hour += 1
        self.trades_today += 1
        self.last_trade_time = time.time()

    def record_exit(self, position: dict, reason: str):
        """Record trade exit and update statistics."""
        # Calculate PnL would be done from position data
        pnl = position.get('last_pnl', 0)

        if pnl >= 0:
            self.wins_today += 1
            self.consecutive_losses = 0
        else:
            self.losses_today += 1
            self.consecutive_losses += 1

            # Apply cooldowns
            if self.consecutive_losses >= 3:
                self.cooldown_until = time.time() + self.cooldown_after_3_losses
                warning(f"[SCALP SESSION] 3 consecutive losses! Cooldown {self.cooldown_after_3_losses}s")
            else:
                self.cooldown_until = time.time() + self.cooldown_after_loss

        self.daily_pnl_pct += pnl
        info(f"[SCALP SESSION] Trade closed: PnL={pnl:+.2f}%, "
             f"W/L={self.wins_today}/{self.losses_today}, "
             f"Daily={self.daily_pnl_pct:+.2f}%")

    def update_stats(self):
        """Periodic stats update (called from slow loop)."""
        pass

    def _reset_daily(self):
        """Reset daily counters."""
        self.trades_today = 0
        self.wins_today = 0
        self.losses_today = 0
        self.consecutive_losses = 0
        self.daily_pnl_pct = 0.0
        self.day_start = time.time()

    def get_stats(self) -> dict:
        """Get current session statistics."""
        total = self.wins_today + self.losses_today
        win_rate = self.wins_today / total if total > 0 else 0
        return {
            'trades_today': self.trades_today,
            'wins': self.wins_today,
            'losses': self.losses_today,
            'win_rate': win_rate,
            'daily_pnl_pct': self.daily_pnl_pct,
            'consecutive_losses': self.consecutive_losses,
        }
```

---

## 7. File Change Summary

### New Files

| File | Purpose |
|------|---------|
| `src/core/scalp_engine.py` | Main ScalpEngine class, TrailingStopManager, ScalpSession |
| `src/core/scalp_signal.py` | ScalpSignalGenerator with entry/exit signal logic |
| `src/core/lightweight_analyzer.py` | Incremental indicator computation for fast loop |

### Modified Files

| File | Change |
|------|--------|
| `src/core/process_worker.py` | Add SCALP branch at top of `run_symbol_pipeline()` (~5 lines) |
| `bot_config.json` | Add `SCALP_SETTINGS` section |

### Unchanged Files

All other files remain unchanged. The scalp engine uses existing modules:
- `src/exchanges/bingx_client.py` -- `get_ticker()`, `get_order_book()`, `set_sl_tp()`, `close_position()`
- `src/exchanges/ws_data_provider.py` -- `get_klines_from_shared_cache()`, `is_cache_ready()`
- `src/core/executor.py` -- `create_order()` for entries
- `src/core/risk_manager.py` -- `calculate_dynamic_sl_tp()`
- `src/core/regime.py` -- `detect_regime()` (slow loop only)
- `src/core/analyzer.py` -- full analysis (slow loop only)
- `src/core/trade_tracker.py` -- trade history persistence (slow loop only)
- `src/core/predict.py` -- AI veto (slow loop only, borderline signals)

---

## 8. Data Flow Diagrams

### Fast Loop Data Flow

```
WS Cache ──> LightweightAnalyzer ──> ScalpSignalGenerator
                                           |
                      +--------------------+--------------------+
                      |                                         |
               [No Position]                            [Has Position]
                      |                                         |
          generate() -> signal                   TrailingStopManager.update()
                      |                          check_exit()
            quality >= threshold?                 _should_time_exit()
              |              |                   _should_move_to_breakeven()
           [HIGH]         [LOW]                         |
              |              |                   [Exit needed?]
        _execute_entry()  pending_signal          |          |
                            (-> slow loop)     [YES]       [NO]
                                            _close_pos()    (continue)
```

### Slow Loop Data Flow

```
Exchange REST ──> Position Sync ──> self.position_state
                                         |
Full Analyzer ──> slow_context ──> Regime Detection
                                         |
                               [Pending Signal?]
                                  |          |
                               [YES]       [NO]
                                  |
                            AI Veto Call
                                  |
                         [APPROVE / REJECT]
                            |          |
                     _execute_entry()  (clear)
```

---

## 9. Critical Design Decisions

### Why Threading (not asyncio or multiprocessing) for Dual Loop?

1. **Shared state**: Fast and slow loops need to share `position_state`, `slow_context`,
   and `pending_signal`. Threading with a lock is the simplest solution.
2. **Existing pattern**: The codebase uses `threading.Thread` in `ws_data_provider.py` (line 67)
   and the Telegram panel. Consistency matters.
3. **GIL is not a bottleneck**: The fast loop does pure Python math (< 5ms). The slow loop
   does I/O-bound REST calls. Neither is CPU-bound enough to warrant multiprocessing.
4. **asyncio would require rewriting** the exchange client and all callers. Too invasive.

### Why Not Modify SignalGenerator Directly?

The existing `SignalGenerator` (signal_generator.py) is tightly coupled to HYBRID mode
settings (`HYBRID_SETTINGS.signal_rules`). Scalp needs different indicators (order book
imbalance, VWAP, shorter EMA/RSI periods) and different scoring logic. A separate
`ScalpSignalGenerator` avoids config namespace collisions and keeps both strategies
independently tunable.

### Why WebSocket Cache is Mandatory for SCALP

The current SCALP preset uses `loop_interval: 5` with REST API data fetching. Each REST
call to `get_kline_data()` takes 200-800ms with network overhead. For a 1-2s fast loop,
we cannot afford REST calls for price data. WebSocket cache provides < 1ms access to
the latest candle data. The `ws_data_provider.py` already implements this with shared
multiprocessing Manager dictionaries -- the scalp engine simply reads from the same cache.

If WebSocket is unavailable (connection failure), the fast loop should degrade gracefully
to a slower REST-based mode (5s interval) rather than crash.

### SL/TP Update Throttling

BingX's `set_sl_tp()` implementation (bingx_client.py line 609) cancels all open orders
then places new SL/TP orders. This is expensive (2-4 API calls). The fast loop should
**not** call `set_sl_tp()` every iteration. Instead:

1. Only update when the new SL differs from current by more than 0.05%
2. Rate-limit updates to max 1 per 5 seconds
3. Use a "pending SL update" queue that batches changes

```python
def _should_update_sl(self, current_sl: float, new_sl: float) -> bool:
    """Avoid API spam: only update if change is significant."""
    if current_sl <= 0:
        return new_sl > 0
    change_pct = abs(new_sl - current_sl) / current_sl * 100
    min_change = self.config.get('min_sl_change_pct', 0.05)
    return change_pct >= min_change

def _rate_limited_sl_update(self, new_sl: float):
    """Rate limit SL updates to 1 per 5 seconds."""
    now = time.time()
    if now - self._last_sl_update_time < 5:
        self._pending_sl = new_sl  # Will be sent next allowed window
        return
    self._update_stop_loss(new_sl)
    self._last_sl_update_time = now
    self._pending_sl = None
```
