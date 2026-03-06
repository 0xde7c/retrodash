"""
Telegram handler — commands, inline buttons, notifications.
Uses python-telegram-bot v20+ (async).
"""

import logging
from datetime import datetime, timezone
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
from config import *

log = logging.getLogger("retrodash.telegram")

# ══════════════════════════════════════════════════════════════════════════
# INLINE KEYBOARD
# ══════════════════════════════════════════════════════════════════════════
MAIN_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("📈 Status", callback_data="status"),
        InlineKeyboardButton("📊 Stats", callback_data="stats"),
    ],
    [
        InlineKeyboardButton("⏸ Pause", callback_data="pause"),
        InlineKeyboardButton("▶️ Resume", callback_data="resume"),
    ],
    [
        InlineKeyboardButton("🔴 Close Position", callback_data="close"),
    ],
])


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

        # Slash commands
        self.app.add_handler(CommandHandler("start", self.cmd_menu))
        self.app.add_handler(CommandHandler("menu", self.cmd_menu))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("stats", self.cmd_stats))
        self.app.add_handler(CommandHandler("pause", self.cmd_pause))
        self.app.add_handler(CommandHandler("resume", self.cmd_resume))
        self.app.add_handler(CommandHandler("close", self.cmd_close))

        # Inline button callbacks
        self.app.add_handler(CallbackQueryHandler(self.on_button))

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
    async def send(self, text, keyboard=None):
        if not self.bot or not TELEGRAM_CHAT_ID:
            return
        try:
            await self.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=text,
                reply_markup=keyboard,
            )
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
        await self.send(msg, keyboard=MAIN_KEYBOARD)

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
        msg = (
            f"📊 DAILY SUMMARY\n"
            f"{summary['wins']}W {summary['losses']}L | {summary['win_rate']}\n"
            f"P&L: ${summary['pnl']:+.2f}"
        )
        if summary.get("balance") is not None:
            msg += f"\nBalance: ${summary['balance']:,.2f}"
        await self.send(msg)

    # ══════════════════════════════════════════════════════════════════════
    # BUTTON CALLBACK
    # ══════════════════════════════════════════════════════════════════════
    async def on_button(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()

        action = query.data
        if action == "status":
            msg = self._build_status()
            await query.message.reply_text(msg, reply_markup=MAIN_KEYBOARD)
        elif action == "stats":
            msg = await self._build_stats()
            await query.message.reply_text(msg, reply_markup=MAIN_KEYBOARD)
        elif action == "pause":
            self.bot_ref.paused = True
            await query.message.reply_text("⏸ Trading PAUSED", reply_markup=MAIN_KEYBOARD)
        elif action == "resume":
            self.bot_ref.paused = False
            await query.message.reply_text("▶️ Trading RESUMED", reply_markup=MAIN_KEYBOARD)
        elif action == "close":
            if self.bot_ref.open_position is None:
                await query.message.reply_text("No open position.", reply_markup=MAIN_KEYBOARD)
            else:
                record = await self.bot_ref.close_all_positions()
                if record:
                    await self.notify_exit(record)
                else:
                    await query.message.reply_text("Close attempted.", reply_markup=MAIN_KEYBOARD)

    # ══════════════════════════════════════════════════════════════════════
    # COMMANDS (slash commands — same logic as buttons)
    # ══════════════════════════════════════════════════════════════════════
    async def cmd_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("🥇 Retrodash", reply_markup=MAIN_KEYBOARD)

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = self._build_status()
        await update.message.reply_text(msg, reply_markup=MAIN_KEYBOARD)

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = await self._build_stats()
        await update.message.reply_text(msg, reply_markup=MAIN_KEYBOARD)

    async def cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.bot_ref.paused = True
        await update.message.reply_text("⏸ Trading PAUSED", reply_markup=MAIN_KEYBOARD)

    async def cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.bot_ref.paused = False
        await update.message.reply_text("▶️ Trading RESUMED", reply_markup=MAIN_KEYBOARD)

    async def cmd_close(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if self.bot_ref.open_position is None:
            await update.message.reply_text("No open position.", reply_markup=MAIN_KEYBOARD)
            return
        record = await self.bot_ref.close_all_positions()
        if record:
            await self.notify_exit(record)
        else:
            await update.message.reply_text("Close attempted.", reply_markup=MAIN_KEYBOARD)

    # ══════════════════════════════════════════════════════════════════════
    # MESSAGE BUILDERS
    # ══════════════════════════════════════════════════════════════════════
    def _build_status(self):
        bot = self.bot_ref
        balance = bot.demo_balance

        pos_str = "FLAT"
        if bot.open_position:
            p = bot.open_position
            hold = bot.position_hold_time()
            hold_str = f"{hold // 60}m {hold % 60}s" if hold >= 60 else f"{hold}s"
            emoji = "🟢" if p['direction'] == 'long' else "🔴"
            pos_str = f"{emoji} {p['direction'].upper()} @ ${p['entry_price']:,.2f} ({hold_str})"

        total = bot.daily_wins + bot.daily_losses
        wr = f"{bot.daily_wins / total * 100:.0f}%" if total > 0 else "N/A"
        paused = " | ⏸ PAUSED" if bot.paused else ""

        return (
            f"📈 RETRODASH STATUS{paused}\n"
            f"Balance: ${balance:,.2f}\n"
            f"Position: {pos_str}\n"
            f"Today: {total} trades | {wr} win rate\n"
            f"P&L: ${bot.daily_pnl:+.2f}"
        )

    async def _build_stats(self):
        bot = self.bot_ref

        # Today
        total = bot.daily_wins + bot.daily_losses
        wr = f"{bot.daily_wins / total * 100:.0f}%" if total > 0 else "N/A"

        # All-time
        at_total = bot.all_time_wins + bot.all_time_losses
        at_wr = f"{bot.all_time_wins / at_total * 100:.0f}%" if at_total > 0 else "N/A"

        # Streak (all-time)
        streak = 0
        streak_type = ""
        for t in reversed(bot.all_time_trades):
            if streak == 0:
                streak_type = "W" if t["pnl"] >= 0 else "L"
                streak = 1
            elif (t["pnl"] >= 0 and streak_type == "W") or (t["pnl"] < 0 and streak_type == "L"):
                streak += 1
            else:
                break
        streak_str = f"{streak}{streak_type}" if streak > 0 else "-"

        # Profit factor (all-time)
        gross_win = sum(t["pnl"] for t in bot.all_time_trades if t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in bot.all_time_trades if t["pnl"] < 0))
        pf = f"{gross_win / gross_loss:.2f}" if gross_loss > 0 else "∞" if gross_win > 0 else "-"

        balance = bot.demo_balance

        return (
            f"📊 RETRODASH STATS\n"
            f"\n"
            f"Today: {bot.daily_wins}W {bot.daily_losses}L | {wr}\n"
            f"P&L: ${bot.daily_pnl:+.2f}\n"
            f"\n"
            f"All-time: {bot.all_time_wins}W {bot.all_time_losses}L | {at_wr}\n"
            f"P&L: ${bot.all_time_pnl:+.2f} | PF: {pf}\n"
            f"Streak: {streak_str}\n"
            f"Balance: ${balance:,.2f}"
        )
