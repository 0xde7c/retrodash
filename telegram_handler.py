"""
Telegram handler — commands, notifications, message formatting.
Uses python-telegram-bot v20+ (async).
"""

import logging
from datetime import datetime, timezone
from telegram import Update, Bot
from telegram.ext import Application, CommandHandler, ContextTypes
from config import *

log = logging.getLogger("retrodash.telegram")


class TelegramHandler:
    def __init__(self, trading_bot):
        self.bot_ref = trading_bot
        self.app = None
        self.bot = None

    # ══════════════════════════════════════════════════════════════════════
    # SETUP
    # ══════════════════════════════════════════════════════════════════════
    async def start(self):
        if not TELEGRAM_BOT_TOKEN:
            log.warning("No TELEGRAM_BOT_TOKEN — Telegram disabled")
            return

        self.app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        self.bot = self.app.bot

        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("pause", self.cmd_pause))
        self.app.add_handler(CommandHandler("resume", self.cmd_resume))
        self.app.add_handler(CommandHandler("close", self.cmd_close))
        self.app.add_handler(CommandHandler("stats", self.cmd_stats))

        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        log.info("Telegram polling started")

    async def stop(self):
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
            log.info("Telegram stopped")

    # ══════════════════════════════════════════════════════════════════════
    # SEND
    # ══════════════════════════════════════════════════════════════════════
    async def send(self, text):
        if not self.bot or not TELEGRAM_CHAT_ID:
            return
        try:
            await self.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=text)
        except Exception as e:
            log.error(f"TG send failed: {e}")

    # ══════════════════════════════════════════════════════════════════════
    # NOTIFICATIONS
    # ══════════════════════════════════════════════════════════════════════
    def _prefix(self):
        return "[DEMO] " if DEMO_MODE else ""

    async def notify_startup(self):
        mode = "DEMO" if DEMO_MODE else "LIVE"
        session = "ACTIVE" if is_session_active() else "INACTIVE"
        msg = (
            f"⚡ RETRODASH STARTED\n"
            f"Mode: {mode} | {SYMBOL}\n"
            f"Session: {session}"
        )
        await self.send(msg)

    async def notify_entry(self, direction, entry_price, sl, tp, indicators):
        emoji = "🟢" if direction == "long" else "🔴"
        label = "LONG" if direction == "long" else "SHORT"
        rsi = indicators.get("rsi7")
        rsi_str = f"{rsi:.1f}" if rsi else "N/A"
        score = indicators.get("score")
        score_str = f"{score:.0f}" if score else "N/A"

        msg = (
            f"{emoji} {label} ${entry_price:,.2f}\n"
            f"RSI {rsi_str} | Score {score_str}/100\n"
            f"TP: ${tp:,.2f}\n"
            f"SL: ${sl:,.2f}"
        )
        await self.send(msg)

    async def notify_exit(self, trade_record):
        if not trade_record:
            return
        pnl = trade_record["pnl"]
        entry = trade_record["entry"]
        exit_p = trade_record["exit"]
        reason = trade_record["reason"]
        hold = trade_record["hold_secs"]

        emoji = "✅" if pnl >= 0 else "❌"
        hold_str = f"{hold // 60}m {hold % 60}s" if hold >= 60 else f"{hold}s"

        msg = (
            f"{emoji} CLOSED ${entry:,.2f} → ${exit_p:,.2f}\n"
            f"P&L: ${pnl:+.2f} | {reason}\n"
            f"Hold: {hold_str}"
        )
        await self.send(msg)

    async def notify_skip(self, direction, reason, indicators):
        pass

    async def notify_daily_summary(self, summary):
        if not summary:
            return
        total = summary['trades']
        msg = (
            f"📊 DAILY SUMMARY\n"
            f"{summary['wins']}W {summary['losses']}L | {summary['win_rate']}\n"
            f"P&L: ${summary['pnl']:+.2f}"
        )
        if summary.get("balance") is not None:
            msg += f"\nBalance: ${summary['balance']:,.2f}"
        await self.send(msg)

    # ══════════════════════════════════════════════════════════════════════
    # COMMANDS
    # ══════════════════════════════════════════════════════════════════════
    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        bot = self.bot_ref
        info = await bot.get_account_info()
        balance = info.get("balance", 0)

        pos_str = "FLAT"
        if bot.open_position:
            p = bot.open_position
            hold = bot.position_hold_time()
            hold_str = f"{hold // 60}m {hold % 60}s" if hold >= 60 else f"{hold}s"
            emoji = "🟢" if p['direction'] == 'long' else "🔴"
            pos_str = f"{emoji} {p['direction'].upper()} @ ${p['entry_price']:,.2f} ({hold_str})"

        total = bot.daily_wins + bot.daily_losses
        wr = f"{bot.daily_wins / total * 100:.0f}%" if total > 0 else "N/A"

        msg = (
            f"📈 RETRODASH STATUS\n"
            f"Balance: ${balance:,.2f}\n"
            f"Position: {pos_str}\n"
            f"Today: {total} trades | {wr} win rate\n"
            f"P&L: ${bot.daily_pnl:+.2f}"
        )
        await update.message.reply_text(msg)

    async def cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.bot_ref.paused = True
        await update.message.reply_text("⏸ Trading PAUSED")

    async def cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.bot_ref.paused = False
        await update.message.reply_text("▶️ Trading RESUMED")

    async def cmd_close(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if self.bot_ref.open_position is None:
            await update.message.reply_text("No open position.")
            return
        record = await self.bot_ref.close_all_positions()
        if record:
            await self.notify_exit(record)
        else:
            await update.message.reply_text("Close attempted.")

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        bot = self.bot_ref
        total = bot.daily_wins + bot.daily_losses
        wr = f"{bot.daily_wins / total * 100:.0f}%" if total > 0 else "N/A"

        # Streak
        streak = 0
        streak_type = ""
        for t in reversed(bot.trade_history):
            if streak == 0:
                streak_type = "W" if t["pnl"] >= 0 else "L"
                streak = 1
            elif (t["pnl"] >= 0 and streak_type == "W") or (t["pnl"] < 0 and streak_type == "L"):
                streak += 1
            else:
                break
        streak_str = f"{streak}{streak_type}" if streak > 0 else "-"

        # Profit factor
        gross_win = sum(t["pnl"] for t in bot.trade_history if t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in bot.trade_history if t["pnl"] < 0))
        pf = f"{gross_win / gross_loss:.2f}" if gross_loss > 0 else "∞" if gross_win > 0 else "-"

        info = await bot.get_account_info()
        balance = info.get("balance", 0)

        msg = (
            f"📊 RETRODASH STATS\n"
            f"{bot.daily_wins}W {bot.daily_losses}L | {wr}\n"
            f"Streak: {streak_str} | PF: {pf}\n"
            f"P&L: ${bot.daily_pnl:+.2f}\n"
            f"Balance: ${balance:,.2f}"
        )
        await update.message.reply_text(msg)
