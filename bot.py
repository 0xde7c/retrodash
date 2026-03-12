"""
Trading bot — MetaAPI connection, order management, position tracking.
5m RSI(14) mean-reversion scalper.
"""

import os
import time
import json
import logging
import aiohttp
from datetime import datetime, timezone, timedelta
from config import *

log = logging.getLogger("retrodash.bot")

STATE_FILE = os.path.join(os.path.dirname(__file__) or ".", "state.json")


class TradingBot:
    def __init__(self):
        self.session = None
        self.connected = False

        # Position state
        self.open_position = None
        self.last_trade_close_time = 0

        # Phase state
        self.phase = "scanning"  # scanning, in_position

        # Daily counters
        self.daily_trades = 0
        self.daily_pnl = 0.0
        self.daily_wins = 0
        self.daily_losses = 0
        self.trade_history = []
        self.last_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.paused = False

        # Indicator tracking
        self.last_rsi = None
        self.last_atr = None
        self.last_adx = None
        self.last_htf_rsi = None

        # All-time counters (persisted)
        self.all_time_trades = []
        self.all_time_pnl = 0.0
        self.all_time_wins = 0
        self.all_time_losses = 0

        # Demo state
        self.demo_balance = STARTING_BALANCE
        self.demo_equity = STARTING_BALANCE

        self._load_state()

    # ══════════════════════════════════════════════════════════════════════
    # STATE PERSISTENCE
    # ══════════════════════════════════════════════════════════════════════
    def _load_state(self):
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

            computed_pnl = round(sum(t.get("pnl", 0) for t in self.all_time_trades), 2)
            if abs(computed_pnl - self.all_time_pnl) > 0.01:
                log.warning(f"PnL mismatch: stored={self.all_time_pnl}, computed={computed_pnl}")
                self.all_time_pnl = computed_pnl

            self.demo_balance = round(STARTING_BALANCE + self.all_time_pnl, 2)
            self.demo_equity = self.demo_balance
            self.last_day = state.get("last_day", self.last_day)

            # Phase state
            self.phase = state.get("phase", "scanning")
            self.last_rsi = state.get("last_rsi")
            self.last_atr = state.get("last_atr")
            self.last_adx = state.get("last_adx")

            # Open position
            pos = state.get("open_position")
            if pos and isinstance(pos, dict) and pos.get("entry_price"):
                self.open_position = pos
                self.phase = "in_position"

            # Rebuild today's counters
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if self.last_day == today:
                self.daily_trades = state.get("daily_trades", 0)
                self.daily_pnl = state.get("daily_pnl", 0.0)
                self.daily_wins = state.get("daily_wins", 0)
                self.daily_losses = state.get("daily_losses", 0)
                self.trade_history = [
                    t for t in self.all_time_trades
                    if t.get("time", "").startswith(today)
                ]

            log.info(
                f"State loaded: balance=${self.demo_balance:.2f}, "
                f"phase={self.phase}, trades_today={self.daily_trades}"
            )
        except Exception as e:
            log.error(f"Failed to load state: {e} — starting fresh")

    def _save_state(self):
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
            "phase": self.phase,
            "last_rsi": self.last_rsi,
            "last_atr": self.last_atr,
            "last_adx": self.last_adx,
            "open_position": self.open_position,
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
    # MARKET DATA
    # ══════════════════════════════════════════════════════════════════════
    async def fetch_candles(self, timeframe, limit=200):
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
        candles = await self.fetch_candles("1m", 3)
        if candles:
            price = candles[-1]['close']
            spread = candles[-1]['high'] - candles[-1]['low']
            return price, price, spread
        return None, None, None

    async def get_current_price(self):
        bid, ask, spread = await self.get_symbol_price()
        if bid and ask:
            return bid, ask, spread
        return await self.get_price_from_candle()

    # ══════════════════════════════════════════════════════════════════════
    # ACCOUNT INFO
    # ══════════════════════════════════════════════════════════════════════
    async def get_account_info(self):
        if DEMO_MODE:
            return {
                'balance': self.demo_balance,
                'equity': self.demo_equity,
                'mode': 'DEMO',
            }
        await self.ensure_connected()
        url = f"{METAAPI_TRADE_URL}/users/current/accounts/{ACCOUNT_ID}/account-information"
        try:
            async with self.session.get(url, headers=self._headers()) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return {
                        'balance': float(data.get('balance', 0)),
                        'equity': float(data.get('equity', 0)),
                        'mode': 'LIVE',
                    }
        except Exception as e:
            log.warning(f"Account info fetch failed: {e}")
        return {'balance': 0, 'equity': 0, 'mode': 'LIVE'}

    # ══════════════════════════════════════════════════════════════════════
    # TRADE EXECUTION — ATR-based SL/TP
    # ══════════════════════════════════════════════════════════════════════
    async def open_trade(self, signal, entry_price):
        """Open a trade with ATR-based SL/TP from signal dict."""
        direction = "long" if signal["side"] == "buy" else "short"
        sl_dist = signal["sl_distance"]
        tp_dist = signal["tp_distance"]
        atr = signal["atr"]

        if direction == "long":
            sl = round(entry_price - sl_dist, 2)
            tp = round(entry_price + tp_dist, 2)
        else:
            sl = round(entry_price + sl_dist, 2)
            tp = round(entry_price - tp_dist, 2)

        mode_tag = "[DEMO]" if DEMO_MODE else "[LIVE]"
        position_id = f'DEMO_{int(time.time())}'

        # Live mode: send real order via MetaAPI
        if not DEMO_MODE:
            position_id = await self._send_market_order(direction, LOT_SIZE, sl, tp)
            if position_id is None:
                log.error(f"LIVE order FAILED — {direction.upper()} @ {entry_price:.2f}")
                return None
            import asyncio
            await asyncio.sleep(1)
            fill_price = await self._get_entry_fill_price(position_id)
            if fill_price and abs(fill_price - entry_price) > 0.01:
                entry_price = fill_price
                if direction == "long":
                    sl = round(entry_price - sl_dist, 2)
                    tp = round(entry_price + tp_dist, 2)
                else:
                    sl = round(entry_price + sl_dist, 2)
                    tp = round(entry_price - tp_dist, 2)
                log.info(f"LIVE fill price: {entry_price:.2f} — updating SL/TP on server")
                await self._modify_position_sl_tp(position_id, sl, tp)

        pos = {
            'id': position_id,
            'direction': direction,
            'entry_price': entry_price,
            'sl': sl,
            'tp': tp,
            'sl_dist': sl_dist,
            'tp_dist': tp_dist,
            'open_time': time.time(),
            'volume': LOT_SIZE,
            'trail_active': False,
            'trail_price': None,
            'atr_at_entry': atr,
            'entry_rsi': signal["rsi"],
        }

        self.open_position = pos
        self.last_trade_close_time = 0
        self.phase = "in_position"
        self._save_state()

        log.info(
            f"{mode_tag} OPEN {direction.upper()} @ {entry_price:.2f} "
            f"SL={sl:.2f} (${sl_dist:.2f}) TP={tp:.2f} (${tp_dist:.2f}) "
            f"RSI={signal['rsi']:.1f} ATR=${atr:.2f}"
        )
        return pos

    async def close_trade(self, exit_price, reason="manual", deal_info=None):
        if self.open_position is None:
            return None

        pos = self.open_position

        # Live mode: close real position on server (for bot-initiated closes)
        if not DEMO_MODE and reason in (
            "trail_exit", "manual", "emergency_close",
            "be_timeout", "max_hold", "rsi_tp",
        ):
            closed = await self._close_position_on_server(pos['id'])
            if not closed:
                log.error(f"LIVE close FAILED for {pos['id']} — reason={reason}")
                return None

        # Use actual MT4 deal data if available
        if deal_info:
            if deal_info.get('entry_price'):
                pos['entry_price'] = deal_info['entry_price']
            exit_price = deal_info.get('exit_price', exit_price)
            reason = deal_info.get('reason', reason)
            pnl = round(deal_info.get('pnl', 0), 2)
        else:
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

        self.last_trade_close_time = time.time()

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
            'entry_rsi': pos.get('entry_rsi'),
            'atr': pos.get('atr_at_entry'),
        }
        self.trade_history.append(trade_record)

        # Update all-time counters
        self.all_time_trades.append(trade_record)
        self.all_time_pnl += pnl
        if pnl >= 0:
            self.all_time_wins += 1
        else:
            self.all_time_losses += 1

        log.info(
            f"CLOSE {pos['direction'].upper()} @ {exit_price:.2f} "
            f"P&L: ${pnl:+.2f} ({reason}) hold={hold_secs}s"
        )

        self.open_position = None

        # Back to scanning (or done for day if limits hit)
        if self.daily_trades >= MAX_TRADES_PER_DAY:
            log.info(f"Max trades reached ({MAX_TRADES_PER_DAY})")
        if self.daily_pnl <= -DAILY_LOSS_LIMIT:
            log.info(f"Daily loss limit hit (${self.daily_pnl:+.2f})")

        self.phase = "scanning"
        self._save_state()
        return trade_record

    async def close_all_positions(self):
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
    def check_sl_tp_trail(self, current_price):
        """Check SL/TP (demo) and trailing stop (both modes).
        Returns: (action, trail_sl) where action is 'sl'/'tp'/'trail_exit'/'trail_update'/None
        trail_sl is the new SL level when trail updates (for server sync).
        """
        if self.open_position is None:
            return None, None
        pos = self.open_position
        sl_dist = pos.get('sl_dist', 3.0)

        # Trail distance uses clamped SL distance (not raw ATR) for consistency
        activate_dist = sl_dist * TRAIL_ACTIVATE_R
        trail_dist = sl_dist * TRAIL_ATR_MULT

        trail_sl = None
        trail_updated = False

        if pos['direction'] == 'long':
            unrealized = current_price - pos['entry_price']
            if unrealized >= activate_dist and not pos['trail_active']:
                pos['trail_active'] = True
                pos['trail_price'] = current_price
                trail_sl = round(current_price - trail_dist, 2)
                trail_updated = True
                log.info(f"TRAIL activated: profit=${unrealized:.2f} >= ${activate_dist:.2f}, trail_dist=${trail_dist:.2f}")

            if pos['trail_active']:
                if current_price > (pos['trail_price'] or 0):
                    pos['trail_price'] = current_price
                    trail_sl = round(current_price - trail_dist, 2)
                    trail_updated = True
                trail_check = pos['trail_price'] - trail_dist
                if current_price <= trail_check:
                    self._save_state()
                    return 'trail_exit', None
        else:
            unrealized = pos['entry_price'] - current_price
            if unrealized >= activate_dist and not pos['trail_active']:
                pos['trail_active'] = True
                pos['trail_price'] = current_price
                trail_sl = round(current_price + trail_dist, 2)
                trail_updated = True
                log.info(f"TRAIL activated: profit=${unrealized:.2f} >= ${activate_dist:.2f}, trail_dist=${trail_dist:.2f}")

            if pos['trail_active']:
                if current_price < (pos['trail_price'] or float('inf')):
                    pos['trail_price'] = current_price
                    trail_sl = round(current_price + trail_dist, 2)
                    trail_updated = True
                trail_check = pos['trail_price'] + trail_dist
                if current_price >= trail_check:
                    self._save_state()
                    return 'trail_exit', None

        # Persist trail state changes
        if trail_updated:
            self._save_state()
            return 'trail_update', trail_sl

        # SL/TP check (demo only — in live, MT4 handles this)
        if DEMO_MODE:
            if pos['direction'] == 'long':
                if current_price <= pos['sl']:
                    return 'sl', None
                if current_price >= pos['tp']:
                    return 'tp', None
            else:
                if current_price >= pos['sl']:
                    return 'sl', None
                if current_price <= pos['tp']:
                    return 'tp', None
        return None, None

    def check_rsi_tp(self, current_rsi, current_price):
        """Close at RSI 50 if in profit (profit gate)."""
        if self.open_position is None or current_rsi is None:
            return False
        pos = self.open_position

        if pos['direction'] == 'long':
            in_profit = current_price > pos['entry_price']
            rsi_crossed = current_rsi >= RSI_TP_LEVEL
        else:
            in_profit = current_price < pos['entry_price']
            rsi_crossed = current_rsi <= RSI_TP_LEVEL

        return in_profit and rsi_crossed

    def check_be_timeout(self, current_price):
        """Close if held > BE_TIMEOUT and still near breakeven."""
        if self.open_position is None:
            return False
        elapsed = time.time() - self.open_position['open_time']
        if elapsed < BE_TIMEOUT:
            return False
        return abs(current_price - self.open_position['entry_price']) < BE_THRESHOLD

    def check_max_hold(self):
        if self.open_position is None:
            return False
        elapsed = time.time() - self.open_position['open_time']
        return elapsed > MAX_HOLD

    def position_hold_time(self):
        if self.open_position is None:
            return 0
        return int(time.time() - self.open_position['open_time'])

    # ══════════════════════════════════════════════════════════════════════
    # RISK CHECKS
    # ══════════════════════════════════════════════════════════════════════
    def can_trade(self):
        if self.paused:
            return False, "paused"
        if self.open_position is not None:
            return False, "position_open"
        if self.daily_trades >= MAX_TRADES_PER_DAY:
            return False, f"max_trades ({self.daily_trades}/{MAX_TRADES_PER_DAY})"
        if self.daily_pnl <= -DAILY_LOSS_LIMIT:
            return False, f"daily_loss_limit (${self.daily_pnl:+.2f})"
        # Cooldown between trades
        if self.last_trade_close_time > 0:
            elapsed = time.time() - self.last_trade_close_time
            if elapsed < TRADE_COOLDOWN:
                return False, f"cooldown ({int(TRADE_COOLDOWN - elapsed)}s left)"
        return True, None

    def check_daily_reset(self):
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

    # ══════════════════════════════════════════════════════════════════════
    # LIVE TRADING — MetaAPI order execution
    # ══════════════════════════════════════════════════════════════════════
    async def _send_market_order(self, direction, volume, sl, tp):
        await self.ensure_connected()
        action = "ORDER_TYPE_BUY" if direction == "long" else "ORDER_TYPE_SELL"
        body = {
            "actionType": action,
            "symbol": SYMBOL,
            "volume": volume,
            "stopLoss": sl,
            "takeProfit": tp,
        }
        url = f"{METAAPI_TRADE_URL}/users/current/accounts/{ACCOUNT_ID}/trade"
        try:
            async with self.session.post(url, json=body, headers=self._headers()) as resp:
                data = await resp.json()
                if resp.status == 200:
                    pid = data.get("positionId") or data.get("orderId") or str(data)
                    log.info(f"LIVE order OK: {action} {volume} {SYMBOL} -> position={pid}")
                    return pid
                else:
                    log.error(f"LIVE order FAILED ({resp.status}): {data}")
                    return None
        except Exception as e:
            log.error(f"LIVE order exception: {e}")
            return None

    async def _close_position_on_server(self, position_id):
        await self.ensure_connected()
        body = {
            "actionType": "POSITION_CLOSE_ID",
            "positionId": str(position_id),
        }
        url = f"{METAAPI_TRADE_URL}/users/current/accounts/{ACCOUNT_ID}/trade"
        try:
            async with self.session.post(url, json=body, headers=self._headers()) as resp:
                if resp.status == 200:
                    log.info(f"LIVE close OK: position={position_id}")
                    return True
                data = await resp.json()
                log.error(f"LIVE close FAILED ({resp.status}): {data}")
                return False
        except Exception as e:
            log.error(f"LIVE close exception: {e}")
            return False

    async def modify_position_sl(self, position_id, new_sl):
        """Update server-side SL for trailing stop."""
        if DEMO_MODE:
            return True
        await self.ensure_connected()
        body = {
            "actionType": "POSITION_MODIFY",
            "positionId": str(position_id),
            "stopLoss": new_sl,
        }
        url = f"{METAAPI_TRADE_URL}/users/current/accounts/{ACCOUNT_ID}/trade"
        try:
            async with self.session.post(url, json=body, headers=self._headers()) as resp:
                if resp.status == 200:
                    log.info(f"SL modified: position={position_id} new_sl={new_sl:.2f}")
                    return True
                data = await resp.json()
                log.error(f"SL modify FAILED ({resp.status}): {data}")
                return False
        except Exception as e:
            log.error(f"SL modify exception: {e}")
            return False

    async def _modify_position_sl_tp(self, position_id, new_sl, new_tp):
        """Update server-side SL and TP (used after fill price correction)."""
        if DEMO_MODE:
            return True
        await self.ensure_connected()
        body = {
            "actionType": "POSITION_MODIFY",
            "positionId": str(position_id),
            "stopLoss": new_sl,
            "takeProfit": new_tp,
        }
        url = f"{METAAPI_TRADE_URL}/users/current/accounts/{ACCOUNT_ID}/trade"
        try:
            async with self.session.post(url, json=body, headers=self._headers()) as resp:
                if resp.status == 200:
                    log.info(f"SL/TP modified: position={position_id} sl={new_sl:.2f} tp={new_tp:.2f}")
                    return True
                data = await resp.json()
                log.error(f"SL/TP modify FAILED ({resp.status}): {data}")
                return False
        except Exception as e:
            log.error(f"SL/TP modify exception: {e}")
            return False

    async def check_position_on_server(self):
        if DEMO_MODE or self.open_position is None:
            return True
        await self.ensure_connected()
        url = f"{METAAPI_TRADE_URL}/users/current/accounts/{ACCOUNT_ID}/positions"
        try:
            async with self.session.get(url, headers=self._headers()) as resp:
                if resp.status == 200:
                    positions = await resp.json()
                    pos_ids = [str(p.get('id', '')) for p in positions]
                    return str(self.open_position['id']) in pos_ids
        except Exception as e:
            log.warning(f"Position check failed: {e}")
        return True  # assume exists on error

    async def _get_entry_fill_price(self, position_id):
        await self.ensure_connected()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        from_time = "2026-01-01T00:00:00.000Z"
        url = (f"{METAAPI_TRADE_URL}/users/current/accounts/{ACCOUNT_ID}"
               f"/history-deals/time/{from_time}/{now}")
        try:
            async with self.session.get(url, headers=self._headers()) as resp:
                if resp.status == 200:
                    deals = await resp.json()
                    for d in deals:
                        if (str(d.get('positionId', '')) == str(position_id)
                                and d.get('entryType') == 'DEAL_ENTRY_IN'):
                            return d.get('price')
        except Exception as e:
            log.warning(f"Entry fill price fetch failed: {e}")
        return None

    async def get_closed_deal_info(self, position_id):
        await self.ensure_connected()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        from_time = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        url = (f"{METAAPI_TRADE_URL}/users/current/accounts/{ACCOUNT_ID}"
               f"/history-deals/time/{from_time}/{now}")
        try:
            async with self.session.get(url, headers=self._headers()) as resp:
                if resp.status == 200:
                    deals = await resp.json()
                    entry_deal = None
                    exit_deal = None
                    for d in deals:
                        if str(d.get('positionId', '')) == str(position_id):
                            if d.get('entryType') == 'DEAL_ENTRY_IN':
                                entry_deal = d
                            elif d.get('entryType') == 'DEAL_ENTRY_OUT':
                                exit_deal = d
                    if exit_deal:
                        comment = exit_deal.get('brokerComment', '')
                        if '[tp]' in comment:
                            reason = 'take_profit'
                        elif '[sl]' in comment:
                            reason = 'stop_loss'
                        else:
                            reason = 'server_close'
                        return {
                            'entry_price': entry_deal.get('price') if entry_deal else None,
                            'exit_price': exit_deal.get('price'),
                            'pnl': exit_deal.get('profit', 0),
                            'commission': exit_deal.get('commission', 0),
                            'reason': reason,
                        }
        except Exception as e:
            log.warning(f"Deal history fetch failed: {e}")
        return None
