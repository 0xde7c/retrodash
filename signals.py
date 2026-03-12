"""
Signal generation — 5m RSI(9) mean-reversion scalper.
Pure math functions, no external TA libraries.
"""

import logging
from datetime import datetime, timezone
from config import (
    RSI_PERIOD, RSI_OS, RSI_OB, RSI_TURN_DELTA,
    ATR_PERIOD, ATR_MIN, ATR_MAX,
    ADX_PERIOD, ADX_MAX,
    SL_ATR_MULT, SL_MIN, SL_MAX, SL_HARD,
    TP_RR, TP_MIN, TP_MAX, SESSION_START, SESSION_END,
)

log = logging.getLogger("retrodash.signals")


def compute_rsi(closes: list, period: int = RSI_PERIOD):
    """
    Wilder's smoothed RSI.
    Needs at least period + 1 closes.
    Returns float RSI value or None if insufficient data.
    """
    if len(closes) < period + 1:
        return None

    gains = []
    losses = []
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        if delta > 0:
            gains.append(delta)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(delta))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    for i in range(period + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gain = delta if delta > 0 else 0.0
        loss = abs(delta) if delta < 0 else 0.0

        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return round(rsi, 2)


def compute_atr(candles: list, period: int = ATR_PERIOD):
    """
    True Range with Wilder's smoothing.
    Each candle needs 'high', 'low', 'close' keys.
    Needs at least period + 1 candles.
    Returns float ATR value or None if insufficient data.
    """
    if len(candles) < period + 1:
        return None

    true_ranges = []
    for i in range(1, len(candles)):
        high = candles[i]['high']
        low = candles[i]['low']
        prev_close = candles[i - 1]['close']

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )
        true_ranges.append(tr)

    if len(true_ranges) < period:
        return None

    atr = sum(true_ranges[:period]) / period

    for i in range(period, len(true_ranges)):
        atr = (atr * (period - 1) + true_ranges[i]) / period

    return round(atr, 2)


def compute_adx(candles: list, period: int = ADX_PERIOD):
    """
    ADX (Average Directional Index) with Wilder's smoothing.
    Needs at least period * 2 + 1 candles.
    Returns float ADX value or None if insufficient data.
    """
    if len(candles) < period * 2 + 1:
        return None

    # Compute +DM, -DM, TR for each bar
    plus_dm_list = []
    minus_dm_list = []
    tr_list = []

    for i in range(1, len(candles)):
        high = candles[i]['high']
        low = candles[i]['low']
        prev_high = candles[i - 1]['high']
        prev_low = candles[i - 1]['low']
        prev_close = candles[i - 1]['close']

        up_move = high - prev_high
        down_move = prev_low - low

        plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
        minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )

        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)
        tr_list.append(tr)

    if len(tr_list) < period:
        return None

    # Initial smoothed values (simple sum of first `period`)
    smoothed_plus_dm = sum(plus_dm_list[:period])
    smoothed_minus_dm = sum(minus_dm_list[:period])
    smoothed_tr = sum(tr_list[:period])

    # Compute DX values for ADX averaging
    dx_values = []

    for i in range(period, len(tr_list)):
        # Wilder's smoothing
        smoothed_plus_dm = smoothed_plus_dm - (smoothed_plus_dm / period) + plus_dm_list[i]
        smoothed_minus_dm = smoothed_minus_dm - (smoothed_minus_dm / period) + minus_dm_list[i]
        smoothed_tr = smoothed_tr - (smoothed_tr / period) + tr_list[i]

        if smoothed_tr == 0:
            dx_values.append(0.0)
            continue

        plus_di = 100 * smoothed_plus_dm / smoothed_tr
        minus_di = 100 * smoothed_minus_dm / smoothed_tr

        di_sum = plus_di + minus_di
        if di_sum == 0:
            dx_values.append(0.0)
        else:
            dx = 100 * abs(plus_di - minus_di) / di_sum
            dx_values.append(dx)

    if len(dx_values) < period:
        return None

    # ADX = Wilder's smoothed average of DX
    adx = sum(dx_values[:period]) / period
    for i in range(period, len(dx_values)):
        adx = (adx * (period - 1) + dx_values[i]) / period

    return round(adx, 2)


def evaluate_signal(rsi: float, prev_rsi: float, atr: float, price: float, adx: float = None):
    """
    Check for RSI mean-reversion entry signal.

    Oversold LONG:  prev_rsi <= RSI_OS and rsi > prev_rsi + RSI_TURN_DELTA
    Overbought SHORT: prev_rsi >= RSI_OB and rsi < prev_rsi - RSI_TURN_DELTA

    Filters: session, ATR floor/ceiling, ADX range filter.
    Returns dict with signal info or None.
    """
    if rsi is None or prev_rsi is None or atr is None:
        return None

    # Session filter
    now = datetime.now(timezone.utc)
    if not (SESSION_START <= now.hour < SESSION_END):
        return None

    # ATR volatility filter — floor and ceiling
    if atr < ATR_MIN:
        log.info(f"ATR ${atr:.2f} < ${ATR_MIN:.2f} — too quiet, skipping")
        return None
    if atr > ATR_MAX:
        log.info(f"ATR ${atr:.2f} > ${ATR_MAX:.2f} — too volatile, skipping")
        return None

    side = None

    # Oversold → LONG
    if prev_rsi <= RSI_OS and rsi > prev_rsi + RSI_TURN_DELTA:
        side = "buy"

    # Overbought → SHORT
    elif prev_rsi >= RSI_OB and rsi < prev_rsi - RSI_TURN_DELTA:
        side = "sell"

    if side is None:
        return None

    # SL = ATR * mult, clamped
    sl_distance = atr * SL_ATR_MULT
    sl_distance = max(sl_distance, SL_MIN)
    sl_distance = min(sl_distance, SL_MAX)

    if sl_distance > SL_HARD:
        sl_distance = SL_HARD

    # TP = SL * R:R, clamped
    tp_distance = sl_distance * TP_RR
    tp_distance = max(tp_distance, TP_MIN)
    tp_distance = min(tp_distance, TP_MAX)

    return {
        "side": side,
        "sl_distance": round(sl_distance, 2),
        "tp_distance": round(tp_distance, 2),
        "atr": atr,
        "adx": adx,
        "rsi": rsi,
        "prev_rsi": prev_rsi,
        "turn_delta": round(abs(rsi - prev_rsi), 2),
    }
