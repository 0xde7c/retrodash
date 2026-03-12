"""
Retrodash v3 — XAU/USD 5m RSI(14) Mean-Reversion Scalper.
Two-phase loop: SCANNING → IN_POSITION.
"""

import asyncio
import logging
import signal
import time
from datetime import datetime, timezone

from config import *
from bot import TradingBot
from signals import compute_rsi, compute_atr, compute_adx, evaluate_signal
from telegram_handler import TelegramHandler

# ══════════════════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE),
    ],
)
log = logging.getLogger("retrodash.main")


# ══════════════════════════════════════════════════════════════════════════
# MAIN ENGINE
# ══════════════════════════════════════════════════════════════════════════
class RetrodashEngine:
    def __init__(self):
        self.bot = TradingBot()
        self.tg = TelegramHandler(self.bot)
        self.running = False

        # Cached data
        self.m5_candles = []
        self.h1_candles = []
        self.last_candle_fetch = 0
        self.last_h1_fetch = 0
        self.last_pos_check = 0
        self.last_periodic_update = time.time()
        self.last_scan_log = 0
        self.last_pos_log = 0

    # ──────────────────────────────────────────────────────────────────────
    # CANDLE FETCHING
    # ──────────────────────────────────────────────────────────────────────
    async def refresh_candles(self, interval):
        now = time.time()
        if now - self.last_candle_fetch < interval:
            return False
        candles = await self.bot.fetch_candles("5m", CANDLE_COUNT)
        if candles:
            self.m5_candles = candles
            self.last_candle_fetch = now
            return True
        return False

    def _get_indicators(self):
        """Compute RSI, ATR, ADX from cached candles. Returns (rsi, prev_rsi, atr, adx, price)."""
        if len(self.m5_candles) < max(RSI_PERIOD, ATR_PERIOD) + 2:
            return None, None, None, None, None

        closes = [c['close'] for c in self.m5_candles]
        price = closes[-1]

        rsi = compute_rsi(closes, RSI_PERIOD)
        prev_rsi = compute_rsi(closes[:-1], RSI_PERIOD)
        atr = compute_atr(self.m5_candles, ATR_PERIOD)
        adx = compute_adx(self.m5_candles)

        return rsi, prev_rsi, atr, adx, price

    # ──────────────────────────────────────────────────────────────────────
    # HTF (1H) RSI — multi-timeframe confirmation
    # ──────────────────────────────────────────────────────────────────────
    async def _get_htf_rsi(self):
        """Fetch 1H candles and compute RSI. Cached for 5 min."""
        now = time.time()
        if now - self.last_h1_fetch < 300:  # refresh every 5 min
            if self.h1_candles:
                closes = [c['close'] for c in self.h1_candles]
                return compute_rsi(closes, HTF_RSI_PERIOD)
            return None

        candles = await self.bot.fetch_candles("1h", HTF_CANDLE_COUNT)
        if candles:
            self.h1_candles = candles
            self.last_h1_fetch = now
            closes = [c['close'] for c in candles]
            return compute_rsi(closes, HTF_RSI_PERIOD)
        return None

    # ──────────────────────────────────────────────────────────────────────
    # PHASE: SCANNING — look for RSI signals
    # ──────────────────────────────────────────────────────────────────────
    async def scan_for_signal(self):
        """Fetch 5m candles, compute RSI + ATR, check for signal."""
        refreshed = await self.refresh_candles(SCAN_POLL_INTERVAL)
        if not refreshed:
            return

        rsi, prev_rsi, atr, adx, price = self._get_indicators()
        if rsi is None:
            return

        # Store for status display
        self.bot.last_rsi = rsi
        self.bot.last_atr = atr
        self.bot.last_adx = adx

        # Session filter
        now = datetime.now(timezone.utc)
        in_session = SESSION_START <= now.hour < SESSION_END

        # Log periodically (every 60s)
        if time.time() - self.last_scan_log > 60:
            session_str = "in-session" if in_session else "out-of-session"
            adx_str = f"{adx:.1f}" if adx else "?"
            log.info(f"RSI={rsi:.1f} ATR=${atr:.2f} ADX={adx_str} price=${price:.2f} — {session_str}")
            self.last_scan_log = time.time()

        if not in_session:
            return

        # Risk checks
        ok, reason = self.bot.can_trade()
        if not ok:
            return

        # Check spread
        bid, ask, spread = await self.bot.get_current_price()
        if bid is None:
            return
        spread_pips = (spread / PIP_SIZE) if spread else 0
        if spread_pips > SPREAD_MAX_PIPS:
            log.info(f"Spread too wide: {spread_pips:.1f} > {SPREAD_MAX_PIPS}")
            return

        # Evaluate signal
        signal = evaluate_signal(rsi, prev_rsi, atr, price, adx)
        if signal is None:
            return

        # Multi-TF confirmation: check 1H RSI
        htf_rsi = await self._get_htf_rsi()
        self.bot.last_htf_rsi = htf_rsi
        if htf_rsi is not None:
            if signal["side"] == "buy" and htf_rsi < HTF_LONG_MIN:
                log.info(f"1H RSI {htf_rsi:.1f} < {HTF_LONG_MIN} — skipping LONG (downtrend on HTF)")
                return
            if signal["side"] == "sell" and htf_rsi > HTF_SHORT_MAX:
                log.info(f"1H RSI {htf_rsi:.1f} > {HTF_SHORT_MAX} — skipping SHORT (uptrend on HTF)")
                return

        signal["htf_rsi"] = htf_rsi

        # Signal found
        side_label = "LONG" if signal["side"] == "buy" else "SHORT"
        htf_str = f", 1H RSI={htf_rsi:.1f}" if htf_rsi else ""
        log.info(
            f"{side_label} signal: RSI={signal['prev_rsi']:.1f}->{signal['rsi']:.1f} "
            f"(turn {'+' if signal['side'] == 'buy' else '-'}{signal['turn_delta']:.1f}), "
            f"ATR=${signal['atr']:.2f}, SL=${signal['sl_distance']:.2f}, TP=${signal['tp_distance']:.2f}"
            f"{htf_str}"
        )

        entry_price = ask if signal["side"] == "buy" else bid
        pos = await self.bot.open_trade(signal, entry_price)
        if pos:
            await self.tg.notify_entry(signal, entry_price, pos["sl"], pos["tp"])

    # ──────────────────────────────────────────────────────────────────────
    # PHASE: IN_POSITION — monitor for exits
    # ──────────────────────────────────────────────────────────────────────
    async def monitor_position(self):
        """Monitor open position for RSI TP, trail, BE timeout, max hold."""
        if self.bot.open_position is None:
            self.bot.phase = "scanning"
            return

        now_ts = time.time()
        if now_ts - self.last_pos_check < POS_POLL_INTERVAL:
            return
        self.last_pos_check = now_ts

        bid, ask, _ = await self.bot.get_current_price()
        if bid is None:
            return

        pos = self.bot.open_position
        current_price = bid if pos["direction"] == "long" else ask

        # Live: check if MT4 closed position (SL/TP hit by broker)
        if not DEMO_MODE:
            still_open = await self.bot.check_position_on_server()
            if not still_open:
                deal_info = await self.bot.get_closed_deal_info(pos['id'])
                if deal_info:
                    log.info(
                        f"Position closed by MT4: {deal_info['reason']} "
                        f"@ {deal_info['exit_price']} pnl=${deal_info['pnl']}"
                    )
                    record = await self.bot.close_trade(current_price, deal_info=deal_info)
                else:
                    log.info("Position closed on server (deal info unavailable)")
                    record = await self.bot.close_trade(current_price, reason="server_close")
                await self.tg.notify_exit(record)
                return

        # SL/TP/Trail check
        hit, trail_sl = self.bot.check_sl_tp_trail(current_price)
        if hit == "sl":
            record = await self.bot.close_trade(pos["sl"], reason="stop_loss")
            await self.tg.notify_exit(record)
            return
        elif hit == "tp":
            record = await self.bot.close_trade(pos["tp"], reason="take_profit")
            await self.tg.notify_exit(record)
            return
        elif hit == "trail_exit":
            record = await self.bot.close_trade(current_price, reason="trail_exit")
            await self.tg.notify_exit(record)
            return
        elif hit == "trail_update" and trail_sl is not None:
            # Sync trailing SL to broker so it survives bot crash
            await self.bot.modify_position_sl(pos['id'], trail_sl)

        # RSI TP exit: close at RSI 50 if in profit
        await self.refresh_candles(POS_POLL_INTERVAL)
        rsi, prev_rsi, atr, adx, _ = self._get_indicators()
        self.bot.last_rsi = rsi
        self.bot.last_atr = atr
        self.bot.last_adx = adx

        if self.bot.check_rsi_tp(rsi, current_price):
            pnl_est = abs(current_price - pos['entry_price']) * LOT_SIZE * 100
            log.info(f"RSI crossed 50 ({rsi:.1f}) with +${pnl_est:.2f} profit — closing")
            record = await self.bot.close_trade(current_price, reason="rsi_tp")
            await self.tg.notify_exit(record)
            return

        # BE timeout (600s near flat)
        if self.bot.check_be_timeout(current_price):
            pnl_est = abs(current_price - pos['entry_price']) * LOT_SIZE * 100
            log.info(f"{BE_TIMEOUT}s near flat (${pnl_est:.2f}) — closing")
            record = await self.bot.close_trade(current_price, reason="be_timeout")
            await self.tg.notify_exit(record)
            return

        # Max hold (1800s)
        if self.bot.check_max_hold():
            log.info(f"Max hold time reached ({MAX_HOLD}s)")
            record = await self.bot.close_trade(current_price, reason="max_hold")
            await self.tg.notify_exit(record)
            return

        # Update demo equity
        if DEMO_MODE:
            if pos["direction"] == "long":
                unrealized = (current_price - pos["entry_price"]) * LOT_SIZE * 100
            else:
                unrealized = (pos["entry_price"] - current_price) * LOT_SIZE * 100
            self.bot.demo_equity = self.bot.demo_balance + unrealized

        # Periodic position log (every 60s to reduce noise)
        if time.time() - self.last_pos_log > 60:
            hold = self.bot.position_hold_time()
            if pos["direction"] == "long":
                unrealized = (current_price - pos["entry_price"]) * LOT_SIZE * 100
            else:
                unrealized = (pos["entry_price"] - current_price) * LOT_SIZE * 100
            trail_str = "active" if pos.get('trail_active') else "inactive"
            rsi_str = f"{rsi:.1f}" if rsi else "?"
            log.info(
                f"Holding {pos['direction'].upper()} {hold}s, "
                f"PnL=${unrealized:+.2f}, RSI={rsi_str}, trail={trail_str}"
            )
            self.last_pos_log = time.time()

    # ──────────────────────────────────────────────────────────────────────
    # PERIODIC UPDATE (4h)
    # ──────────────────────────────────────────────────────────────────────
    async def periodic_update(self):
        now = time.time()
        if now - self.last_periodic_update < 4 * 3600:
            return
        self.last_periodic_update = now

        bot = self.bot
        info = await bot.get_account_info()
        balance = info.get("balance", 0)
        total = bot.daily_wins + bot.daily_losses
        wr = f"{bot.daily_wins / total * 100:.0f}%" if total > 0 else "N/A"

        rsi_str = f"{bot.last_rsi:.1f}" if bot.last_rsi else "?"
        atr_str = f"${bot.last_atr:.2f}" if bot.last_atr else "?"

        now_utc = datetime.now(timezone.utc)
        in_session = SESSION_START <= now_utc.hour < SESSION_END
        session_str = "ACTIVE" if in_session else "INACTIVE"

        msg = (
            f"4H UPDATE | Phase: {bot.phase} | Session: {session_str}\n"
            f"RSI: {rsi_str} | ATR: {atr_str}\n"
            f"{bot.daily_wins}W {bot.daily_losses}L | {wr}\n"
            f"P&L: ${bot.daily_pnl:+.2f}\n"
            f"Balance: ${balance:,.2f}"
        )
        await self.tg.send(msg)

    # ──────────────────────────────────────────────────────────────────────
    # DAILY TASKS
    # ──────────────────────────────────────────────────────────────────────
    async def daily_tasks(self):
        summary = self.bot.check_daily_reset()
        if summary:
            await self.tg.notify_daily_summary(summary)

    # ──────────────────────────────────────────────────────────────────────
    # MAIN LOOP
    # ──────────────────────────────────────────────────────────────────────
    async def run(self):
        self.running = True
        await self.bot.connect()
        await self.tg.start()
        await self.tg.notify_startup()

        mode = "DEMO" if DEMO_MODE else "LIVE"
        log.info(f"Retrodash v3 started — {mode} mode — 5m RSI(9) Scalper")

        try:
            while self.running:
                try:
                    phase = self.bot.phase

                    if phase == "scanning":
                        await self.scan_for_signal()
                    elif phase == "in_position":
                        await self.monitor_position()

                    # Safety: if position exists but phase is scanning, fix it
                    if self.bot.open_position and self.bot.phase == "scanning":
                        self.bot.phase = "in_position"

                    await self.periodic_update()
                    await self.daily_tasks()

                except Exception as e:
                    log.error(f"Loop error: {e}", exc_info=True)

                await asyncio.sleep(MAIN_LOOP_SLEEP)

        except asyncio.CancelledError:
            log.info("Main loop cancelled")
        finally:
            await self.shutdown()

    async def shutdown(self):
        log.info("Shutting down...")
        self.running = False

        if self.bot.open_position:
            log.info("Closing open position on shutdown")
            record = await self.bot.close_all_positions()
            if record:
                await self.tg.notify_exit(record)

        if self.bot.daily_trades > 0:
            summary = self.bot.get_daily_summary()
            await self.tg.notify_daily_summary(summary)

        await self.tg.send("RETRODASH v3 STOPPED")
        await self.tg.stop()
        await self.bot.disconnect()
        log.info("Shutdown complete")


# ══════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════
def main():
    mode = "DEMO" if DEMO_MODE else "LIVE"
    print(f"Retrodash v3 starting in {mode} mode — 5m RSI(9) Scalper")

    engine = RetrodashEngine()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def handle_signal(sig, frame):
        log.info(f"Received signal {sig}")
        engine.running = False

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        loop.run_until_complete(engine.run())
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt")
        loop.run_until_complete(engine.shutdown())
    finally:
        loop.close()


if __name__ == "__main__":
    main()
