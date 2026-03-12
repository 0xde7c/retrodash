"""
Telegram handler — commands, inline buttons, notifications.
5m RSI(14) mean-reversion scalper.
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
        InlineKeyboardButton("Status", callback_data="status"),
        InlineKeyboardButton("Stats", callback_data="stats"),
    ],
    [
        InlineKeyboardButton("RSI", callback_data="rsi"),
        InlineKeyboardButton("Close", callback_data="close"),
    ],
    [
        InlineKeyboardButton("Pause", callback_data="pause"),
        InlineKeyboardButton("Resume", callback_data="resume"),
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
        self.app.add_handler(CommandHandler("rsi", self.cmd_rsi))
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
        bot = self.bot_ref

        now = datetime.now(timezone.utc)
        in_session = SESSION_START <= now.hour < SESSION_END
        session_str = "ACTIVE" if in_session else "INACTIVE"

        rsi_str = f"{bot.last_rsi:.1f}" if bot.last_rsi else "—"
        atr_str = f"${bot.last_atr:.2f}" if bot.last_atr else "—"

        info = await bot.get_account_info()
        balance = info.get("balance", 0)
        equity = info.get("equity", 0)

        msg = (
            f"RETRODASH v3 STARTED\n"
            f"Mode: {mode} | {SYMBOL}\n"
            f"Strategy: 5m RSI(9) Scalper\n"
            f"Session: {session_str} ({SESSION_START:02d}:00-{SESSION_END:02d}:00 UTC)\n"
            f"Phase: {bot.phase}\n"
            f"RSI: {rsi_str} | ATR: {atr_str}\n"
            f"Balance: ${balance:,.2f} | Equity: ${equity:,.2f}"
        )
        await self.send(msg, keyboard=MAIN_KEYBOARD)

    async def notify_entry(self, signal, entry_price, sl, tp):
        side_label = "LONG" if signal["side"] == "buy" else "SHORT"
        sl_dist = signal["sl_distance"]
        tp_dist = signal["tp_distance"]

        msg = (
            f"{side_label} ENTRY @ ${entry_price:,.2f}\n"
            f"RSI: {signal['prev_rsi']:.1f} -> {signal['rsi']:.1f} (turn {signal['turn_delta']:.1f})\n"
            f"SL: ${sl:,.2f} (${sl_dist:.2f})\n"
            f"TP: ${tp:,.2f} (${tp_dist:.2f})\n"
            f"ATR: ${signal['atr']:.2f}"
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

        tag = "TP" if reason in ("take_profit", "rsi_tp") else ("SL" if reason == "stop_loss" else ("W" if pnl >= 0 else "L"))
        hold_str = f"{hold // 60}m {hold % 60}s" if hold >= 60 else f"{hold}s"

        msg = (
            f"{tag} CLOSED ${entry:,.2f} -> ${exit_p:,.2f}\n"
            f"P&L: ${pnl:+.2f} | {reason}\n"
            f"Hold: {hold_str}"
        )
        if trade_record.get("entry_rsi"):
            msg += f"\nEntry RSI: {trade_record['entry_rsi']:.1f}"
        await self.send(msg)

    async def notify_daily_summary(self, summary):
        if not summary:
            return
        msg = (
            f"DAILY SUMMARY\n"
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
            msg = await self._build_status()
            await query.message.reply_text(msg, reply_markup=MAIN_KEYBOARD)
        elif action == "stats":
            msg = self._build_stats()
            await query.message.reply_text(msg, reply_markup=MAIN_KEYBOARD)
        elif action == "rsi":
            msg = self._build_rsi()
            await query.message.reply_text(msg, reply_markup=MAIN_KEYBOARD)
        elif action == "pause":
            self.bot_ref.paused = True
            await query.message.reply_text("Trading PAUSED", reply_markup=MAIN_KEYBOARD)
        elif action == "resume":
            self.bot_ref.paused = False
            await query.message.reply_text("Trading RESUMED", reply_markup=MAIN_KEYBOARD)
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
    # COMMANDS
    # ══════════════════════════════════════════════════════════════════════
    async def cmd_menu(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("Retrodash v3 — RSI Scalper", reply_markup=MAIN_KEYBOARD)

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = await self._build_status()
        await update.message.reply_text(msg, reply_markup=MAIN_KEYBOARD)

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = self._build_stats()
        await update.message.reply_text(msg, reply_markup=MAIN_KEYBOARD)

    async def cmd_rsi(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        msg = self._build_rsi()
        await update.message.reply_text(msg, reply_markup=MAIN_KEYBOARD)

    async def cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.bot_ref.paused = True
        await update.message.reply_text("Trading PAUSED", reply_markup=MAIN_KEYBOARD)

    async def cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self.bot_ref.paused = False
        await update.message.reply_text("Trading RESUMED", reply_markup=MAIN_KEYBOARD)

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
    async def _build_status(self):
        bot = self.bot_ref
        info = await bot.get_account_info()
        balance = info.get("balance", 0)

        paused = " | PAUSED" if bot.paused else ""

        now = datetime.now(timezone.utc)
        in_session = SESSION_START <= now.hour < SESSION_END
        session_str = "ACTIVE" if in_session else "INACTIVE"

        rsi_str = f"{bot.last_rsi:.1f}" if bot.last_rsi else "—"
        atr_str = f"${bot.last_atr:.2f}" if bot.last_atr else "—"
        adx_str = f"{bot.last_adx:.1f}" if getattr(bot, 'last_adx', None) else "—"

        pos_str = "FLAT"
        if bot.open_position:
            p = bot.open_position
            hold = bot.position_hold_time()
            hold_str = f"{hold // 60}m {hold % 60}s" if hold >= 60 else f"{hold}s"
            trail_str = " [TRAILING]" if p.get('trail_active') else ""
            pos_str = f"{p['direction'].upper()} @ ${p['entry_price']:,.2f} ({hold_str}){trail_str}"

        total = bot.daily_wins + bot.daily_losses
        wr = f"{bot.daily_wins / total * 100:.0f}%" if total > 0 else "N/A"

        ok, block_reason = bot.can_trade()
        trade_str = "Ready" if ok else f"Blocked: {block_reason}"

        htf_rsi_str = f"{bot.last_htf_rsi:.1f}" if getattr(bot, 'last_htf_rsi', None) else "—"

        return (
            f"RETRODASH v3 STATUS{paused}\n"
            f"Phase: {bot.phase} | Session: {session_str}\n"
            f"RSI(9): {rsi_str} | 1H RSI: {htf_rsi_str}\n"
            f"ATR: {atr_str} | ADX: {adx_str}\n"
            f"Position: {pos_str}\n"
            f"Trades: {bot.daily_trades}/{MAX_TRADES_PER_DAY} | {wr}\n"
            f"P&L: ${bot.daily_pnl:+.2f}\n"
            f"Balance: ${balance:,.2f}\n"
            f"Trading: {trade_str}"
        )

    def _build_rsi(self):
        bot = self.bot_ref

        rsi_str = f"{bot.last_rsi:.1f}" if bot.last_rsi else "—"
        atr_str = f"${bot.last_atr:.2f}" if bot.last_atr else "—"

        now = datetime.now(timezone.utc)
        in_session = SESSION_START <= now.hour < SESSION_END
        session_str = "ACTIVE" if in_session else "INACTIVE"

        # Entry zones
        zone = "NEUTRAL"
        if bot.last_rsi is not None:
            if bot.last_rsi <= RSI_OS:
                zone = f"OVERSOLD (entry zone <= {RSI_OS})"
            elif bot.last_rsi >= RSI_OB:
                zone = f"OVERBOUGHT (entry zone >= {RSI_OB})"

        pos_str = ""
        if bot.open_position:
            p = bot.open_position
            pos_str = f"\nPosition: {p['direction'].upper()} @ ${p['entry_price']:,.2f} (entry RSI: {p.get('entry_rsi', '?')})"

        atr_ok = "OK" if (bot.last_atr is not None and ATR_MIN <= bot.last_atr <= ATR_MAX) else ("QUIET" if bot.last_atr is not None and bot.last_atr < ATR_MIN else "HIGH")
        adx_str = f"{bot.last_adx:.1f}" if getattr(bot, 'last_adx', None) else "—"
        adx_ok = "RANGE" if (getattr(bot, 'last_adx', None) is not None and bot.last_adx <= ADX_MAX) else "TREND"

        return (
            f"RSI INFO\n"
            f"RSI(9): {rsi_str}\n"
            f"ATR(14): {atr_str} (${ATR_MIN:.0f}-${ATR_MAX:.0f}, {atr_ok})\n"
            f"ADX(10): {adx_str} (max {ADX_MAX}, {adx_ok})\n"
            f"Zone: {zone}\n"
            f"Session: {session_str}\n"
            f"Turn delta: {RSI_TURN_DELTA}\n"
            f"Entry: OS<={RSI_OS} / OB>={RSI_OB}\n"
            f"R:R 1:{TP_RR}\n"
            f"TP: RSI crosses {RSI_TP_LEVEL} (if in profit)"
            f"{pos_str}"
        )

    def _build_stats(self):
        bot = self.bot_ref

        # Today
        total = bot.daily_wins + bot.daily_losses
        wr = f"{bot.daily_wins / total * 100:.0f}%" if total > 0 else "N/A"

        # All-time
        at_total = bot.all_time_wins + bot.all_time_losses
        at_wr = f"{bot.all_time_wins / at_total * 100:.0f}%" if at_total > 0 else "N/A"

        # Streak
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

        # Profit factor
        gross_win = sum(t["pnl"] for t in bot.all_time_trades if t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in bot.all_time_trades if t["pnl"] < 0))
        pf = f"{gross_win / gross_loss:.2f}" if gross_loss > 0 else ("inf" if gross_win > 0 else "-")

        balance = bot.demo_balance

        return (
            f"RETRODASH v3 STATS\n"
            f"\n"
            f"Today: {bot.daily_wins}W {bot.daily_losses}L | {wr}\n"
            f"P&L: ${bot.daily_pnl:+.2f}\n"
            f"\n"
            f"All-time: {bot.all_time_wins}W {bot.all_time_losses}L | {at_wr}\n"
            f"P&L: ${bot.all_time_pnl:+.2f} | PF: {pf}\n"
            f"Streak: {streak_str}\n"
            f"Balance: ${balance:,.2f}"
        )
