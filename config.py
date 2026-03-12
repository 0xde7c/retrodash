import os
from dotenv import load_dotenv

load_dotenv()

# ══════════════════════════════════════════════════════════════════════════
# MODE
# ══════════════════════════════════════════════════════════════════════════
DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"

# ══════════════════════════════════════════════════════════════════════════
# MetaAPI
# ══════════════════════════════════════════════════════════════════════════
METAAPI_TOKEN = os.getenv("METAAPI_TOKEN", "")
ACCOUNT_ID = os.getenv("ACCOUNT_ID", "")
METAAPI_DATA_URL = "https://mt-market-data-client-api-v1.london.agiliumtrade.ai"
METAAPI_TRADE_URL = "https://mt-client-api-v1.london.agiliumtrade.ai"

# ══════════════════════════════════════════════════════════════════════════
# Telegram
# ══════════════════════════════════════════════════════════════════════════
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ══════════════════════════════════════════════════════════════════════════
# Symbol
# ══════════════════════════════════════════════════════════════════════════
SYMBOL = "XAUUSD"
PIP_SIZE = 0.10
PIP_VALUE = 0.10

# ══════════════════════════════════════════════════════════════════════════
# RSI
# ══════════════════════════════════════════════════════════════════════════
RSI_PERIOD = 9            # faster RSI for 5m (research: 7-9 optimal)
RSI_OS = 25               # oversold entry (gold-specific: tighter = higher quality)
RSI_OB = 75               # overbought entry
RSI_TURN_DELTA = 3.0      # confirmation: RSI must reverse by this much
RSI_TP_LEVEL = 50         # close at RSI 50 if in profit

# ══════════════════════════════════════════════════════════════════════════
# Multi-TF Confirmation (1H RSI)
# ══════════════════════════════════════════════════════════════════════════
HTF_RSI_PERIOD = 9        # 1H RSI period (same as 5m for consistency)
HTF_CANDLE_COUNT = 30     # 1H candles to fetch
HTF_LONG_MIN = 40         # 1H RSI must be > 40 for longs (not downtrend)
HTF_SHORT_MAX = 60        # 1H RSI must be < 60 for shorts (not uptrend)

# ══════════════════════════════════════════════════════════════════════════
# ATR
# ══════════════════════════════════════════════════════════════════════════
ATR_PERIOD = 14
ATR_MIN = 2.0             # skip if ATR < $2 (not enough movement)
ATR_MAX = 8.0             # skip if ATR > $8 (too volatile)

# ══════════════════════════════════════════════════════════════════════════
# ADX — range filter
# ══════════════════════════════════════════════════════════════════════════
ADX_PERIOD = 10           # faster ADX for 5m
ADX_MAX = 30              # only trade in ranging/mild-trend markets (ADX < 30)

# ══════════════════════════════════════════════════════════════════════════
# SL / TP — ATR-based
# ══════════════════════════════════════════════════════════════════════════
SL_ATR_MULT = 1.0
SL_MIN = 2.0
SL_MAX = 5.0
SL_HARD = 7.0
TP_RR = 1.5               # 1:1.5 R:R (research: sweet spot for mean-reversion)
TP_MIN = 3.0
TP_MAX = 8.0

# ══════════════════════════════════════════════════════════════════════════
# Trailing Stop (backup — fixed TP is primary exit)
# ══════════════════════════════════════════════════════════════════════════
TRAIL_ACTIVATE_R = 1.0    # activate at 1R profit
TRAIL_ATR_MULT = 0.8      # trail ATR * 0.8 behind price

# ══════════════════════════════════════════════════════════════════════════
# Time-Based Exits
# ══════════════════════════════════════════════════════════════════════════
BE_TIMEOUT = 900           # 15 min near flat -> close (3 candles)
BE_THRESHOLD = 1.5         # within $1.50 of entry = "near flat"
MAX_HOLD = 1800            # 30 min max hold

# ══════════════════════════════════════════════════════════════════════════
# Session Filter (UTC hours)
# ══════════════════════════════════════════════════════════════════════════
SESSION_START = 7          # 07:00 UTC
SESSION_END = 17           # 17:00 UTC (London + NY overlap)

# ══════════════════════════════════════════════════════════════════════════
# Risk Controls
# ══════════════════════════════════════════════════════════════════════════
LOT_SIZE = 0.01
DAILY_LOSS_LIMIT = 8.0     # $8 max daily loss
MAX_TRADES_PER_DAY = 6
TRADE_COOLDOWN = 180       # 3 min between trades
STARTING_BALANCE = 175.0
SPREAD_MAX_PIPS = 20

# ══════════════════════════════════════════════════════════════════════════
# Polling Intervals (seconds)
# ══════════════════════════════════════════════════════════════════════════
SCAN_POLL_INTERVAL = 15    # candle fetch interval while scanning
POS_POLL_INTERVAL = 10     # price/RSI check interval while in position
MAIN_LOOP_SLEEP = 3
CANDLE_COUNT = 100         # 5m candles to fetch

# ══════════════════════════════════════════════════════════════════════════
# Logging
# ══════════════════════════════════════════════════════════════════════════
LOG_FILE = "trades.log"
