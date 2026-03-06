"""
Signal generation — indicator computation and entry evaluation.
Uses pandas + pandas_ta on OHLCV candles from MetaAPI.
"""

import pandas as pd
import pandas_ta as ta
import logging
from config import *
from config import get_session_tier, SESSION_MIN_SCORE

log = logging.getLogger("retrodash.signals")


def build_dataframe(candles):
    """Convert list of candle dicts to pandas DataFrame."""
    if not candles:
        return pd.DataFrame()
    df = pd.DataFrame(candles)
    for col in ['open', 'high', 'low', 'close']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    if 'volume' in df.columns:
        df['volume'] = pd.to_numeric(df['volume'], errors='coerce')
    return df


def compute_m1_indicators(df):
    """Compute all M1 indicators: EMA 5/13/34, RSI(7), ATR(14)."""
    if len(df) < EMA_TREND + 10:
        return df
    df['ema_fast'] = ta.ema(df['close'], length=EMA_FAST)
    df['ema_slow'] = ta.ema(df['close'], length=EMA_SLOW)
    df['ema_trend'] = ta.ema(df['close'], length=EMA_TREND)
    df['rsi7'] = ta.rsi(df['close'], length=RSI_PERIOD)
    df['atr14'] = ta.atr(df['high'], df['low'], df['close'], length=ATR_PERIOD)
    return df


def compute_m5_ema(df):
    """Compute M5 EMA 13 (confirmation)."""
    if len(df) < EMA_SLOW + 5:
        return df
    df['ema_slow'] = ta.ema(df['close'], length=EMA_SLOW)
    return df


def compute_h1_ema(df):
    """Compute H1 EMA 200."""
    if len(df) < EMA_BIAS + 5:
        return df
    df['ema200'] = ta.ema(df['close'], length=EMA_BIAS)
    return df


def detect_crossover(df):
    """
    Detect EMA fast/slow crossover on last two completed candles.
    Returns 'long', 'short', or None.
    """
    if len(df) < 3:
        return None

    prev_fast = df['ema_fast'].iloc[-2]
    prev_slow = df['ema_slow'].iloc[-2]
    curr_fast = df['ema_fast'].iloc[-1]
    curr_slow = df['ema_slow'].iloc[-1]

    if pd.isna(prev_fast) or pd.isna(curr_fast) or pd.isna(prev_slow) or pd.isna(curr_slow):
        return None

    # Bullish: EMA9 crosses above EMA21
    if prev_fast <= prev_slow and curr_fast > curr_slow:
        return 'long'
    # Bearish: EMA9 crosses below EMA21
    if prev_fast >= prev_slow and curr_fast < curr_slow:
        return 'short'

    return None


def ema_slope(df, col='ema_slow', lookback=3):
    """Calculate slope of an EMA over the last N candles. Returns float."""
    if len(df) < lookback + 1:
        return 0.0
    vals = df[col].iloc[-(lookback + 1):].values
    if any(pd.isna(vals)):
        return 0.0
    return float(vals[-1] - vals[0])


def evaluate_signal(m1_df, m5_df, h1_df, spread):
    """
    Evaluate all entry conditions.
    Primary: M1 candles. Confirmation: M5 slope. Bias: H1 EMA 200.

    Returns:
        (direction, skip_reason, indicators)
        direction: 'long', 'short', or None
        skip_reason: string explaining why signal was skipped, or None
        indicators: dict of current indicator values
    """
    indicators = {}

    # ── Compute M1 indicators ────────────────────────────────────────────
    m1_df = compute_m1_indicators(m1_df)
    if len(m1_df) < EMA_TREND + 10:
        return None, "not_enough_m1_data", indicators

    latest = m1_df.iloc[-1]
    price = float(latest['close'])
    indicators = {
        'price': price,
        'ema_fast': float(latest['ema_fast']) if not pd.isna(latest['ema_fast']) else None,
        'ema_slow': float(latest['ema_slow']) if not pd.isna(latest['ema_slow']) else None,
        'ema_trend': float(latest['ema_trend']) if not pd.isna(latest['ema_trend']) else None,
        'rsi7': float(latest['rsi7']) if not pd.isna(latest['rsi7']) else None,
        'atr14': float(latest['atr14']) if not pd.isna(latest['atr14']) else None,
    }

    # ── Check for EMA fast/slow crossover ─────────────────────────────────
    crossover = detect_crossover(m1_df)
    if crossover is None:
        return None, None, indicators     # No crossover — not a skip, just no signal

    direction = crossover
    indicators['crossover'] = direction

    # ── Session filter ───────────────────────────────────────────────────
    if not is_session_active():
        from datetime import datetime, timezone
        hour = datetime.now(timezone.utc).hour
        return None, f"session_filter (hour={hour}, allowed {SESSION_START_HOUR}-{SESSION_END_HOUR})", indicators

    # ── News blackout ────────────────────────────────────────────────────
    if is_news_blackout():
        return None, "news_blackout", indicators

    # ── Spread filter ────────────────────────────────────────────────────
    spread_pips = spread / PIP_SIZE if spread else 0
    indicators['spread_pips'] = round(spread_pips, 1)
    if spread_pips > SPREAD_MAX_PIPS:
        return None, f"spread_too_wide ({spread_pips:.1f} pips > {SPREAD_MAX_PIPS})", indicators

    # ── ATR filter ───────────────────────────────────────────────────────
    atr = indicators['atr14']
    if atr is None:
        return None, "atr_not_ready", indicators
    atr_pips = atr / PIP_SIZE
    indicators['atr_pips'] = round(atr_pips, 1)
    if atr_pips < ATR_MIN_PIPS:
        return None, f"atr_too_low ({atr_pips:.1f} < {ATR_MIN_PIPS})", indicators
    if atr_pips > ATR_MAX_PIPS:
        return None, f"atr_too_high ({atr_pips:.1f} > {ATR_MAX_PIPS})", indicators

    # ── Indicator readiness ──────────────────────────────────────────────
    ema_trend = indicators['ema_trend']
    rsi = indicators['rsi7']
    if ema_trend is None or rsi is None:
        return None, "indicators_not_ready", indicators

    # ── M5 EMA slope (confirmation) ──────────────────────────────────────
    m5_df = compute_m5_ema(m5_df)
    m5_sl = ema_slope(m5_df, 'ema_slow', lookback=3)
    indicators['m5_slope'] = round(m5_sl, 4)

    # ── H1 EMA 200 bias ─────────────────────────────────────────────────
    h1_df = compute_h1_ema(h1_df)
    h1_ema200 = None
    h1_price = None
    if len(h1_df) > EMA_BIAS and 'ema200' in h1_df.columns:
        val = h1_df['ema200'].iloc[-1]
        if not pd.isna(val):
            h1_ema200 = float(val)
    if len(h1_df) > 0:
        h1_price = float(h1_df['close'].iloc[-1])
    indicators['h1_ema200'] = h1_ema200
    indicators['h1_price'] = h1_price

    # ══════════════════════════════════════════════════════════════════════
    # DIRECTION-SPECIFIC CHECKS
    # ══════════════════════════════════════════════════════════════════════
    if direction == 'long':
        # 1. Price above EMA trend
        if price <= ema_trend:
            return None, f"price_below_ema_trend ({price:.2f} <= {ema_trend:.2f})", indicators

        # 2. RSI > 50
        if rsi <= RSI_LONG_MIN:
            return None, f"rsi_too_low ({rsi:.1f} <= {RSI_LONG_MIN})", indicators

        # Signal quality score — session-aware threshold
        score = _signal_score(direction, rsi, atr_pips, m5_sl, h1_ema200, h1_price, m1_df)
        indicators['score'] = score
        tier = get_session_tier()
        min_score = SESSION_MIN_SCORE.get(tier, MIN_SIGNAL_SCORE)
        indicators['session'] = tier
        if score < min_score:
            return None, f"weak_signal (score={score:.1f} < {min_score} [{tier}])", indicators

        return 'long', None, indicators

    else:  # short
        # 1. Price below EMA trend
        if price >= ema_trend:
            return None, f"price_above_ema_trend ({price:.2f} >= {ema_trend:.2f})", indicators

        # 2. RSI < 50
        if rsi >= RSI_SHORT_MAX:
            return None, f"rsi_too_high ({rsi:.1f} >= {RSI_SHORT_MAX})", indicators

        # Signal quality score — session-aware threshold
        score = _signal_score(direction, rsi, atr_pips, m5_sl, h1_ema200, h1_price, m1_df)
        indicators['score'] = score
        tier = get_session_tier()
        min_score = SESSION_MIN_SCORE.get(tier, MIN_SIGNAL_SCORE)
        indicators['session'] = tier
        if score < min_score:
            return None, f"weak_signal (score={score:.1f} < {min_score} [{tier}])", indicators

        return 'short', None, indicators


def _signal_score(direction, rsi, atr_pips, m5_slope, h1_ema200, h1_price, m1_df):
    """
    Score a signal 0-100. Higher = stronger setup.
    Factors: crossover gap, RSI momentum, ATR sweet spot, M5 slope, H1 alignment.
    """
    score = 0.0

    # 1. Crossover gap strength (0-25 pts)
    #    Bigger gap between fast and slow EMA = stronger momentum
    if len(m1_df) >= 2:
        fast = m1_df['ema_fast'].iloc[-1]
        slow = m1_df['ema_slow'].iloc[-1]
        if not pd.isna(fast) and not pd.isna(slow):
            gap = abs(fast - slow) / PIP_SIZE  # in pips
            score += min(gap * 2.5, 25)  # 10 pip gap = 25 pts

    # 2. RSI momentum (0-25 pts)
    #    Long: further above 50 = stronger. Short: further below 50 = stronger.
    if direction == 'long':
        rsi_strength = max(rsi - 50, 0)  # 0-50 range
    else:
        rsi_strength = max(50 - rsi, 0)
    score += min(rsi_strength, 25)  # 25+ away from 50 = max pts

    # 3. ATR sweet spot (0-20 pts)
    #    Best range: 8-25 pips. Too low = no movement. Too high = choppy.
    if 8 <= atr_pips <= 25:
        score += 20
    elif 5 <= atr_pips <= 40:
        score += 10
    else:
        score += 5

    # 4. M5 slope strength (0-15 pts)
    slope_strength = abs(m5_slope) / PIP_SIZE
    score += min(slope_strength * 3, 15)

    # 5. H1 alignment (0-15 pts)
    if h1_ema200 is not None and h1_price is not None:
        if direction == 'long' and h1_price > h1_ema200:
            h1_gap = (h1_price - h1_ema200) / h1_ema200 * 100  # % above
            score += min(h1_gap * 5, 15)
        elif direction == 'short' and h1_price < h1_ema200:
            h1_gap = (h1_ema200 - h1_price) / h1_ema200 * 100
            score += min(h1_gap * 5, 15)

    return round(score, 1)
