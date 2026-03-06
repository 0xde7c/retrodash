import os
from datetime import datetime, timezone
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
LOT_SIZE = 0.01
PIP_SIZE = 0.10     # 1 pip = $0.10 movement in XAUUSD price
PIP_VALUE = 0.10    # USD per pip at 0.01 lots (1 oz)

# ══════════════════════════════════════════════════════════════════════════
# Indicators
# ══════════════════════════════════════════════════════════════════════════
EMA_FAST = 5        # M1 fast EMA
EMA_SLOW = 13       # M1 slow EMA + M5 slope
EMA_TREND = 34      # M1 trend filter
EMA_BIAS = 200      # H1 directional bias
RSI_PERIOD = 7
ATR_PERIOD = 14

# ══════════════════════════════════════════════════════════════════════════
# Entry filters
# ══════════════════════════════════════════════════════════════════════════
RSI_LONG_MIN = 50
RSI_SHORT_MAX = 50
SPREAD_MAX_PIPS = 20
ATR_MIN_PIPS = 3
ATR_MAX_PIPS = 60
MIN_SIGNAL_SCORE = 20       # minimum quality score (0-100) — low for testing

# ══════════════════════════════════════════════════════════════════════════
# SL / TP (fixed pips)
# ══════════════════════════════════════════════════════════════════════════
SL_PIPS = 8
TP_PIPS = 10
SL_DISTANCE = SL_PIPS * PIP_SIZE   # $0.80 in price
TP_DISTANCE = TP_PIPS * PIP_SIZE   # $1.00 in price

# ══════════════════════════════════════════════════════════════════════════
# Risk controls
# ══════════════════════════════════════════════════════════════════════════
MAX_TRADES_PER_DAY = 30
DAILY_LOSS_LIMIT = 20.0             # USD
MAX_POSITION_TIME = 300             # 5 minutes
STARTING_BALANCE = 200.0

# ══════════════════════════════════════════════════════════════════════════
# Session (UTC hours)
# ══════════════════════════════════════════════════════════════════════════
SESSION_START_HOUR = 0
SESSION_END_HOUR = 24

# ══════════════════════════════════════════════════════════════════════════
# Polling intervals (seconds)
# ══════════════════════════════════════════════════════════════════════════
M1_POLL_INTERVAL = 5
M5_POLL_INTERVAL = 30
H1_POLL_INTERVAL = 300
PRICE_POLL_INTERVAL = 5
MAIN_LOOP_SLEEP = 3

# Candle counts (enough history for longest indicator)
M1_CANDLE_COUNT = 250       # EMA 34 + ATR 14 warmup
M5_CANDLE_COUNT = 100       # EMA 13
H1_CANDLE_COUNT = 250       # EMA 200

# ══════════════════════════════════════════════════════════════════════════
# Logging
# ══════════════════════════════════════════════════════════════════════════
LOG_FILE = "trades.log"

# ══════════════════════════════════════════════════════════════════════════
# News blackout — hardcoded high-impact dates for 2026
# Bot skips entries during 12:00-15:00 UTC on these days
# ══════════════════════════════════════════════════════════════════════════
NEWS_BLACKOUT_DATES = []  # disabled for testing
NEWS_BLACKOUT_START = 12    # UTC
NEWS_BLACKOUT_END = 15      # UTC


def is_news_blackout():
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")
    if date_str in NEWS_BLACKOUT_DATES:
        if NEWS_BLACKOUT_START <= now.hour < NEWS_BLACKOUT_END:
            return True
    return False


def is_session_active():
    hour = datetime.now(timezone.utc).hour
    return SESSION_START_HOUR <= hour < SESSION_END_HOUR
