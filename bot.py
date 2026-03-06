"""
Trading bot — MetaAPI connection, order management, position tracking.
Uses aiohttp for async HTTP to MetaAPI REST endpoints.

*** SAFETY: This bot NEVER sends real orders. All trades are simulated. ***
*** The trade endpoint is permanently blocked. This is non-negotiable.  ***
"""

import os
import time
import json
import logging
import aiohttp
from datetime import datetime, timezone
from config import *

log = logging.getLogger("retrodash.bot")

# ══════════════════════════════════════════════════════════════════════════
# HARD SAFETY — NEVER SEND REAL ORDERS
# These guards exist independent of DEMO_MODE and cannot be overridden.
# ══════════════════════════════════════════════════════════════════════════
_REAL_TRADING_PERMANENTLY_DISABLED = True  # NEVER change this

def _assert_no_real_trading():
    """Kill the process immediately if someone tries to enable real trading."""
    if not _REAL_TRADING_PERMANENTLY_DISABLED:
        log.critical("SAFETY VIOLATION: Real trading flag was tampered with. Killing process.")
        os._exit(1)
    if not DEMO_MODE:
        log.critical("SAFETY VIOLATION: DEMO_MODE is False. Killing process.")
        os._exit(1)


class TradingBot:
    def __init__(self):
        _assert_no_real_trading()
        self.session = None
        self.connected = False

        # Position state
        self.open_position = None   # {id, direction, entry_price, sl, tp, open_time, volume}
        self.last_signal_candle = None  # time of last candle that triggered a signal

        # Daily counters
        self.daily_trades = 0
        self.daily_pnl = 0.0
        self.daily_wins = 0
        self.daily_losses = 0
        self.trade_history = []     # today's trades
        self.last_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.paused = False

        # Demo state
        self.demo_balance = STARTING_BALANCE
        self.demo_equity = STARTING_BALANCE

    # ══════════════════════════════════════════════════════════════════════
    # CONNECTION
    # ══════════════════════════════════════════════════════════════════════
    async def connect(self):
        timeout = aiohttp.ClientTimeout(total=30)
        self.session = aiohttp.ClientSession(timeout=timeout)
        self.connected = True
        log.info("HTTP session created")

    async def disconnect(self):
        if self.session:
            await self.session.close()
            self.connected = False
            log.info("HTTP session closed")

    async def ensure_connected(self):
        if not self.session or self.session.closed:
            await self.connect()

    def _headers(self):
        return {
            "auth-token": METAAPI_TOKEN,
            "Content-Type": "application/json",
        }

    # ══════════════════════════════════════════════════════════════════════
    # MARKET DATA (read-only, safe in all modes)
    # ══════════════════════════════════════════════════════════════════════
    async def fetch_candles(self, timeframe, limit=200):
        """Fetch historical candles from MetaAPI market data endpoint."""
        await self.ensure_connected()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        url = (
            f"{METAAPI_DATA_URL}/users/current/accounts/{ACCOUNT_ID}"
            f"/historical-market-data/symbols/{SYMBOL}/timeframes/{timeframe}"
            f"/candles?startTime={now}&limit={limit}"
        )
        try:
            async with self.session.get(url, headers=self._headers()) as resp:
                data = await resp.json()
                if not data or not isinstance(data, list):
                    log.warning(f"No candle data for {timeframe}: {type(data)}")
                    return []
                candles = [{
                    'time': c.get('time', ''),
                    'open': float(c.get('open', 0)),
                    'high': float(c.get('high', 0)),
                    'low': float(c.get('low', 0)),
                    'close': float(c.get('close', 0)),
                    'volume': float(c.get('tickVolume', 0)),
                } for c in data]
                candles.sort(key=lambda x: x['time'])
                return candles
        except Exception as e:
            log.error(f"Candle fetch failed ({timeframe}): {e}")
            return []

    async def get_symbol_price(self):
        """Get current bid/ask/spread from MetaAPI."""
        await self.ensure_connected()
        url = f"{METAAPI_TRADE_URL}/users/current/accounts/{ACCOUNT_ID}/symbols/{SYMBOL}/current-price"
        try:
            async with self.session.get(url, headers=self._headers()) as resp:
                if resp.status != 200:
                    return None, None, None
                data = await resp.json()
                bid = float(data.get('bid', 0))
                ask = float(data.get('ask', 0))
                spread = ask - bid
                return bid, ask, spread
        except Exception as e:
            log.warning(f"Price fetch failed: {e}")
            return None, None, None

    async def get_price_from_candle(self):
        """Fallback: get price from latest 1m candle."""
        candles = await self.fetch_candles("1m", 3)
        if candles:
            price = candles[-1]['close']
            spread = candles[-1]['high'] - candles[-1]['low']
            return price, price, spread
        return None, None, None

    async def get_current_price(self):
        """Get best available price and spread."""
        bid, ask, spread = await self.get_symbol_price()
        if bid and ask:
            return bid, ask, spread
        # Fallback to candle
        return await self.get_price_from_candle()

    # ══════════════════════════════════════════════════════════════════════
    # ACCOUNT INFO
    # ══════════════════════════════════════════════════════════════════════
    async def get_account_info(self):
        """Return simulated account info. Never touches real account."""
        return {
            'balance': self.demo_balance,
            'equity': self.demo_equity,
            'mode': 'DEMO (paper)',
        }

    async def open_trade(self, direction, entry_price):
        """
        Open a SIMULATED trade. Returns position dict or None.
        *** NEVER sends real orders. All trades are fake. ***
        """
        _assert_no_real_trading()

        if direction == 'long':
            sl = round(entry_price - SL_DISTANCE, 2)
            tp = round(entry_price + TP_DISTANCE, 2)
        else:
            sl = round(entry_price + SL_DISTANCE, 2)
            tp = round(entry_price - TP_DISTANCE, 2)

        pos = {
            'id': f'DEMO_{int(time.time())}',
            'direction': direction,
            'entry_price': entry_price,
            'sl': sl,
            'tp': tp,
            'open_time': time.time(),
            'volume': LOT_SIZE,
        }

        self.open_position = pos
        log.info(f"[DEMO] OPEN {direction.upper()} @ {entry_price:.2f} SL={sl:.2f} TP={tp:.2f}")
        return pos

    async def close_trade(self, exit_price, reason="manual"):
        """
        Close the current SIMULATED position. Returns trade record dict or None.
        *** NEVER sends real orders. All trades are fake. ***
        """
        _assert_no_real_trading()

        if self.open_position is None:
            return None

        pos = self.open_position

        # Calculate P&L
        if pos['direction'] == 'long':
            pnl = (exit_price - pos['entry_price']) * LOT_SIZE * 100
        else:
            pnl = (pos['entry_price'] - exit_price) * LOT_SIZE * 100
        pnl = round(pnl, 2)

        # Update counters
        self.daily_pnl += pnl
        self.daily_trades += 1
        if pnl >= 0:
            self.daily_wins += 1
        else:
            self.daily_losses += 1

        if DEMO_MODE:
            self.demo_balance += pnl
            self.demo_equity = self.demo_balance

        hold_secs = int(time.time() - pos['open_time'])
        trade_record = {
            'time': datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            'direction': pos['direction'],
            'entry': pos['entry_price'],
            'exit': exit_price,
            'sl': pos['sl'],
            'tp': pos['tp'],
            'pnl': pnl,
            'reason': reason,
            'hold_secs': hold_secs,
        }
        self.trade_history.append(trade_record)

        log.info(f"CLOSE {pos['direction'].upper()} @ {exit_price:.2f} P&L: ${pnl:+.2f} ({reason}) hold={hold_secs}s")

        self.open_position = None
        return trade_record

    async def close_all_positions(self):
        """Emergency close all SIMULATED positions."""
        _assert_no_real_trading()

        bid, ask, _ = await self.get_current_price()
        if self.open_position:
            if self.open_position['direction'] == 'long':
                exit_price = bid or self.open_position['entry_price']
            else:
                exit_price = ask or self.open_position['entry_price']
            return await self.close_trade(exit_price, reason="emergency_close")
        return None

    # ══════════════════════════════════════════════════════════════════════
    # POSITION MONITORING
    # ══════════════════════════════════════════════════════════════════════
    def check_demo_sl_tp(self, current_price):
        """In demo mode, check if price hit SL or TP. Returns 'sl', 'tp', or None."""
        if not DEMO_MODE or self.open_position is None:
            return None
        pos = self.open_position
        if pos['direction'] == 'long':
            if current_price <= pos['sl']:
                return 'sl'
            if current_price >= pos['tp']:
                return 'tp'
        else:
            if current_price >= pos['sl']:
                return 'sl'
            if current_price <= pos['tp']:
                return 'tp'
        return None

    def check_position_timeout(self):
        """Check if position exceeded max hold time. Returns True if timed out."""
        if self.open_position is None:
            return False
        elapsed = time.time() - self.open_position['open_time']
        return elapsed > MAX_POSITION_TIME

    def position_hold_time(self):
        if self.open_position is None:
            return 0
        return int(time.time() - self.open_position['open_time'])

    # ══════════════════════════════════════════════════════════════════════
    # RISK CHECKS
    # ══════════════════════════════════════════════════════════════════════
    def can_trade(self):
        """Check all risk controls. Returns (ok, reason)."""
        if self.paused:
            return False, "paused"
        if self.open_position is not None:
            return False, "position_open"
        if self.daily_trades >= MAX_TRADES_PER_DAY:
            return False, f"max_trades ({self.daily_trades}/{MAX_TRADES_PER_DAY})"
        if self.daily_pnl <= -DAILY_LOSS_LIMIT:
            return False, f"daily_loss_limit (${self.daily_pnl:+.2f})"
        return True, None

    def check_daily_reset(self):
        """Reset counters at midnight UTC. Returns True if reset happened."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.last_day:
            summary = self.get_daily_summary()
            self.daily_trades = 0
            self.daily_pnl = 0.0
            self.daily_wins = 0
            self.daily_losses = 0
            self.trade_history = []
            self.last_day = today
            log.info("Daily reset")
            return summary
        return None

    def get_daily_summary(self):
        """Build daily summary dict."""
        total = self.daily_wins + self.daily_losses
        wr = f"{self.daily_wins / total * 100:.0f}%" if total > 0 else "N/A"
        return {
            'date': self.last_day,
            'trades': total,
            'wins': self.daily_wins,
            'losses': self.daily_losses,
            'win_rate': wr,
            'pnl': self.daily_pnl,
            'balance': self.demo_balance if DEMO_MODE else None,
        }
