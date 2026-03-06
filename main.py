"""
Retrodash v2 — XAU/USD EMA crossover scalper.
Entry point: async main loop with candle polling, signal evaluation, position management.
"""

import asyncio
import logging
import signal
import sys
import time
from datetime import datetime, timezone

from config import *
from bot import TradingBot
from signals import build_dataframe, evaluate_signal
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
# MAIN CLASS
# ══════════════════════════════════════════════════════════════════════════
class RetrodashEngine:
    def __init__(self):
        self.bot = TradingBot()
        self.tg = TelegramHandler(self.bot)
        self.running = False

        # Cached candle DataFrames
        self.m1_candles = []
        self.m5_candles = []
        self.h1_candles = []

        # Last fetch timestamps
        self.last_m1_fetch = 0
        self.last_m5_fetch = 0
        self.last_h1_fetch = 0
        self.last_price_check = 0

        # Daily summary sent flag
        self.daily_summary_sent = False
        self.last_periodic_update = time.time()  # don't fire immediately on startup

    # ──────────────────────────────────────────────────────────────────────
    # CANDLE FETCHING
    # ──────────────────────────────────────────────────────────────────────
    async def refresh_candles(self):
        """Fetch candles for each timeframe based on their poll intervals."""
        now = time.time()
        fetched_m1 = False

        if now - self.last_m1_fetch >= M1_POLL_INTERVAL:
            candles = await self.bot.fetch_candles("1m", M1_CANDLE_COUNT)
            log.info(f"M1 fetch: {len(candles)} candles")
            if candles:
                self.m1_candles = candles
                self.last_m1_fetch = now
                fetched_m1 = True

        if now - self.last_m5_fetch >= M5_POLL_INTERVAL:
            candles = await self.bot.fetch_candles("5m", M5_CANDLE_COUNT)
            log.info(f"M5 fetch: {len(candles)} candles")
            if candles:
                self.m5_candles = candles
                self.last_m5_fetch = now

        if now - self.last_h1_fetch >= H1_POLL_INTERVAL:
            candles = await self.bot.fetch_candles("1h", H1_CANDLE_COUNT)
            log.info(f"H1 fetch: {len(candles)} candles")
            if candles:
                self.h1_candles = candles
                self.last_h1_fetch = now

        return fetched_m1

    # ──────────────────────────────────────────────────────────────────────
    # SIGNAL EVALUATION
    # ──────────────────────────────────────────────────────────────────────
    async def check_signals(self):
        """Evaluate entry signal. Returns True if a trade was opened."""
        ok, reason = self.bot.can_trade()
        if not ok:
            return False

        if not self.m1_candles or not self.m5_candles:
            return False

        # Build DataFrames
        m1_df = build_dataframe(self.m1_candles)
        m5_df = build_dataframe(self.m5_candles)
        h1_df = build_dataframe(self.h1_candles)

        # Get current spread
        bid, ask, spread = await self.bot.get_current_price()
        if bid is None:
            return False

        # Evaluate
        direction, skip_reason, indicators = evaluate_signal(m1_df, m5_df, h1_df, spread)

        if direction is None:
            if skip_reason:
                log.info(f"Signal skipped: {skip_reason}")
                await self.tg.notify_skip(indicators.get("crossover"), skip_reason, indicators)
            return False

        # Deduplicate: don't trade the same candle twice
        last_candle_time = self.m1_candles[-1].get("time", "") if self.m1_candles else ""
        if last_candle_time == self.bot.last_signal_candle:
            log.debug("Same candle as last signal — skipping")
            return False
        self.bot.last_signal_candle = last_candle_time

        # Determine entry price
        entry_price = ask if direction == "long" else bid

        # Open trade
        pos = await self.bot.open_trade(direction, entry_price)
        if pos:
            await self.tg.notify_entry(direction, entry_price, pos["sl"], pos["tp"], indicators)
            return True
        return False

    # ──────────────────────────────────────────────────────────────────────
    # POSITION MONITORING
    # ──────────────────────────────────────────────────────────────────────
    async def monitor_position(self):
        """Check open position for SL/TP hit or timeout."""
        if self.bot.open_position is None:
            return

        now = time.time()
        if now - self.last_price_check < PRICE_POLL_INTERVAL:
            return
        self.last_price_check = now

        bid, ask, _ = await self.bot.get_current_price()
        if bid is None:
            return

        pos = self.bot.open_position
        current_price = bid if pos["direction"] == "long" else ask

        # Demo mode: check SL/TP
        if DEMO_MODE:
            hit = self.bot.check_demo_sl_tp(current_price)
            if hit == "sl":
                record = await self.bot.close_trade(pos["sl"], reason="stop_loss")
                await self.tg.notify_exit(record)
                return
            elif hit == "tp":
                record = await self.bot.close_trade(pos["tp"], reason="take_profit")
                await self.tg.notify_exit(record)
                return

        # Timeout check
        if self.bot.check_position_timeout():
            record = await self.bot.close_trade(current_price, reason="timeout")
            await self.tg.notify_exit(record)
            return

        # Update demo equity
        if DEMO_MODE:
            if pos["direction"] == "long":
                unrealized = (current_price - pos["entry_price"]) * LOT_SIZE * 100
            else:
                unrealized = (pos["entry_price"] - current_price) * LOT_SIZE * 100
            self.bot.demo_equity = self.bot.demo_balance + unrealized

    # ──────────────────────────────────────────────────────────────────────
    # PERIODIC 4-HOUR UPDATE
    # ──────────────────────────────────────────────────────────────────────
    async def periodic_update(self):
        """Send a status update to Telegram every 4 hours."""
        now = time.time()
        if now - self.last_periodic_update < 4 * 3600:
            return
        self.last_periodic_update = now

        bot = self.bot
        info = await bot.get_account_info()
        balance = info.get("balance", 0)

        total = bot.daily_wins + bot.daily_losses
        wr = f"{bot.daily_wins / total * 100:.0f}%" if total > 0 else "N/A"

        msg = (
            f"🕐 4H UPDATE\n"
            f"{bot.daily_wins}W {bot.daily_losses}L | {wr}\n"
            f"P&L: ${bot.daily_pnl:+.2f}\n"
            f"Balance: ${balance:,.2f}"
        )
        await self.tg.send(msg)

    # ──────────────────────────────────────────────────────────────────────
    # DAILY TASKS
    # ──────────────────────────────────────────────────────────────────────
    async def daily_tasks(self):
        """Check for daily reset and send end-of-session summary."""
        # Midnight reset
        summary = self.bot.check_daily_reset()
        if summary:
            await self.tg.notify_daily_summary(summary)
            self.daily_summary_sent = False

        # End-of-session summary at 17:00 UTC
        now = datetime.now(timezone.utc)
        if now.hour == SESSION_END_HOUR and not self.daily_summary_sent:
            if self.bot.daily_trades > 0:
                summary = self.bot.get_daily_summary()
                await self.tg.notify_daily_summary(summary)
            self.daily_summary_sent = True
        elif now.hour != SESSION_END_HOUR:
            self.daily_summary_sent = False

    # ──────────────────────────────────────────────────────────────────────
    # MAIN LOOP
    # ──────────────────────────────────────────────────────────────────────
    async def run(self):
        """Main entry point."""
        self.running = True

        # Connect
        await self.bot.connect()
        await self.tg.start()
        await self.tg.notify_startup()

        mode = "DEMO" if DEMO_MODE else "LIVE"
        log.info(f"Retrodash started — {mode} mode")

        try:
            while self.running:
                try:
                    # Refresh candles (returns True if M1 was fetched)
                    m1_refreshed = await self.refresh_candles()

                    # Evaluate signals only when M1 data refreshes and session is active
                    if m1_refreshed and is_session_active():
                        await self.check_signals()

                    # Monitor open position
                    await self.monitor_position()

                    # Periodic 4-hour TG update
                    await self.periodic_update()

                    # Daily housekeeping
                    await self.daily_tasks()

                except Exception as e:
                    log.error(f"Loop error: {e}", exc_info=True)

                await asyncio.sleep(MAIN_LOOP_SLEEP)

        except asyncio.CancelledError:
            log.info("Main loop cancelled")
        finally:
            await self.shutdown()

    async def shutdown(self):
        """Graceful shutdown."""
        log.info("Shutting down...")
        self.running = False

        # Close any open position
        if self.bot.open_position:
            log.info("Closing open position on shutdown")
            record = await self.bot.close_all_positions()
            if record:
                await self.tg.notify_exit(record)

        # Send final summary if we had trades today
        if self.bot.daily_trades > 0:
            summary = self.bot.get_daily_summary()
            await self.tg.notify_daily_summary(summary)

        await self.tg.send(f"{self.tg._prefix()}RETRODASH STOPPED")
        await self.tg.stop()
        await self.bot.disconnect()
        log.info("Shutdown complete")


# ══════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════
def main():
    # SAFETY: Refuse to start if not in demo mode
    if not DEMO_MODE:
        print("FATAL: DEMO_MODE must be true. This bot NEVER trades real money.")
        sys.exit(1)

    engine = RetrodashEngine()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    # Handle SIGINT/SIGTERM for graceful shutdown
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
