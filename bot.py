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


STATE_FILE = os.path.join(os.path.dirname(__file__) or ".", "state.json")


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

        # All-time counters (persisted)
        self.all_time_trades = []   # every trade ever
        self.all_time_pnl = 0.0
        self.all_time_wins = 0
        self.all_time_losses = 0

        # Demo state
        self.demo_balance = STARTING_BALANCE
        self.demo_equity = STARTING_BALANCE

        # Load persisted state
        self._load_state()

    # ══════════════════════════════════════════════════════════════════════
    # STATE PERSISTENCE
    # ══════════════════════════════════════════════════════════════════════
    def _load_state(self):
        """Load persisted state from state.json on startup."""
        if not os.path.exists(STATE_FILE):
            log.info("No state file — starting fresh")
            return
        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)

            self.all_time_trades = state.get("all_time_trades", [])
            self.all_time_pnl = state.get("all_time_pnl", 0.0)
            self.all_time_wins = state.get("all_time_wins", 0)
            self.all_time_losses = state.get("all_time_losses", 0)

            # Reconcile: balance = starting + sum(all trades) — single source of truth
            computed_pnl = round(sum(t.get("pnl", 0) for t in self.all_time_trades), 2)
            if abs(computed_pnl - self.all_time_pnl) > 0.01:
                log.warning(f"PnL mismatch: stored={self.all_time_pnl}, computed={computed_pnl} — using computed")
                self.all_time_pnl = computed_pnl
            self.demo_balance = round(STARTING_BALANCE + self.all_time_pnl, 2)
            self.demo_equity = self.demo_balance
            self.last_day = state.get("last_day", self.last_day)

            # Rebuild today's counters from all_time_trades
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if self.last_day == today:
                self.daily_trades = state.get("daily_trades", 0)
                self.daily_pnl = state.get("daily_pnl", 0.0)
                self.daily_wins = state.get("daily_wins", 0)
                self.daily_losses = state.get("daily_losses", 0)
                # Restore today's trade history
                self.trade_history = [
                    t for t in self.all_time_trades
                    if t.get("time", "").startswith(today)
                ]

            log.info(
                f"State loaded: balance=${self.demo_balance:.2f}, "
                f"all_time={len(self.all_time_trades)} trades, "
                f"pnl=${self.all_time_pnl:+.2f}, "
                f"today={self.daily_trades} trades"
            )
        except Exception as e:
            log.error(f"Failed to load state: {e} — starting fresh")

    def _save_state(self):
        """Persist state to state.json after every trade."""
        state = {
            "demo_balance": round(self.demo_balance, 2),
            "all_time_trades": self.all_time_trades,
            "all_time_pnl": round(self.all_time_pnl, 2),
            "all_time_wins": self.all_time_wins,
            "all_time_losses": self.all_time_losses,
            "last_day": self.last_day,
            "daily_trades": self.daily_trades,
            "daily_pnl": round(self.daily_pnl, 2),
            "daily_wins": self.daily_wins,
            "daily_losses": self.daily_losses,
        }
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as e:
            log.error(f"Failed to save state: {e}")

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

    async def open_trade(self, direction, entry_price, atr=None):
        """
        Open a SIMULATED trade. Returns position dict or None.
        *** NEVER sends real orders. All trades are fake. ***
        """
        _assert_no_real_trading()

        # ATR-based SL/TP if ATR is available
        if atr and atr > 0:
            sl_dist = atr * ATR_SL_MULTIPLIER
            tp_dist = atr * ATR_TP_MULTIPLIER

            # Clamp to min/max pips
            sl_dist = max(sl_dist, SL_MIN_PIPS * PIP_SIZE)
            sl_dist = min(sl_dist, SL_MAX_PIPS * PIP_SIZE)
            tp_dist = max(tp_dist, TP_MIN_PIPS * PIP_SIZE)
            tp_dist = min(tp_dist, TP_MAX_PIPS * PIP_SIZE)
        else:
            sl_dist = SL_DISTANCE
            tp_dist = TP_DISTANCE

        if direction == 'long':
            sl = round(entry_price - sl_dist, 2)
            tp = round(entry_price + tp_dist, 2)
        else:
            sl = round(entry_price + sl_dist, 2)
            tp = round(entry_price - tp_dist, 2)

        pos = {
            'id': f'DEMO_{int(time.time())}',
            'direction': direction,
            'entry_price': entry_price,
            'sl': sl,
            'tp': tp,
            'open_time': time.time(),
            'volume': LOT_SIZE,
            'atr': atr or 0,
            'trail_active': False,
            'trail_price': None,        # best price seen since trail activated
        }

        self.open_position = pos
        sl_pips = round(sl_dist / PIP_SIZE, 1)
        tp_pips = round(tp_dist / PIP_SIZE, 1)
        log.info(f"[DEMO] OPEN {direction.upper()} @ {entry_price:.2f} SL={sl:.2f}({sl_pips}p) TP={tp:.2f}({tp_pips}p) ATR={atr:.2f}" if atr else f"[DEMO] OPEN {direction.upper()} @ {entry_price:.2f} SL={sl:.2f} TP={tp:.2f}")
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

        # Update all-time counters
        self.all_time_trades.append(trade_record)
        self.all_time_pnl += pnl
        if pnl >= 0:
            self.all_time_wins += 1
        else:
            self.all_time_losses += 1

        log.info(f"CLOSE {pos['direction'].upper()} @ {exit_price:.2f} P&L: ${pnl:+.2f} ({reason}) hold={hold_secs}s")

        self.open_position = None
        self._save_state()
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
        """In demo mode, check SL/TP/trailing. Returns 'sl', 'tp', 'trail_exit', or None."""
        if not DEMO_MODE or self.open_position is None:
            return None
        pos = self.open_position
        atr = pos.get('atr', 0)

        # ── Trailing stop logic ──────────────────────────────────────────
        if atr > 0:
            activate_dist = atr * TRAIL_ACTIVATE_ATR
            trail_dist = atr * TRAIL_DISTANCE_ATR

            if pos['direction'] == 'long':
                unrealized = current_price - pos['entry_price']
                # Activate trailing if profit exceeds threshold
                if unrealized >= activate_dist and not pos['trail_active']:
                    pos['trail_active'] = True
                    pos['trail_price'] = current_price
                    log.info(f"TRAIL activated: profit={unrealized:.2f} >= {activate_dist:.2f}")

                if pos['trail_active']:
                    # Update high-water mark
                    if current_price > (pos['trail_price'] or 0):
                        pos['trail_price'] = current_price
                    # Check trail stop
                    trail_sl = pos['trail_price'] - trail_dist
                    if current_price <= trail_sl:
                        return 'trail_exit'
            else:
                unrealized = pos['entry_price'] - current_price
                if unrealized >= activate_dist and not pos['trail_active']:
                    pos['trail_active'] = True
                    pos['trail_price'] = current_price
                    log.info(f"TRAIL activated: profit={unrealized:.2f} >= {activate_dist:.2f}")

                if pos['trail_active']:
                    if current_price < (pos['trail_price'] or float('inf')):
                        pos['trail_price'] = current_price
                    trail_sl = pos['trail_price'] + trail_dist
                    if current_price >= trail_sl:
                        return 'trail_exit'

        # ── Normal SL/TP check ───────────────────────────────────────────
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
        """Check if position exceeded max hold time. Extended if trailing active."""
        if self.open_position is None:
            return False
        elapsed = time.time() - self.open_position['open_time']
        max_time = TRAIL_MAX_HOLD_SECS if self.open_position.get('trail_active') else MAX_POSITION_TIME
        return elapsed > max_time

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
            self._save_state()
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
