"""
telegram_bot.py — Professional Interactive UI (Accumulator Strict Edition)
──────────────────────────────────────────────────────────────────────────
Architecture Upgrades:
  1. Strategic Alignment: Stripped all legacy contract types and "Auto Mode" toggles.
  2. Anti-Spam Guard: Implemented _safe_edit to catch Telegram's "Message not modified" crashes.
  3. UI Polish: Status dashboard now explicitly tracks Accumulator-specific metrics.
  4. Global Error Boundary: Added a root-level error handler to prevent UI thread panics.
  5. Defensive Rendering: Safe attribute getters prevent UI crashes on missing state variables.
"""

import asyncio
import logging
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters, ContextTypes,
)
from telegram.error import BadRequest
from telegram.constants import ParseMode

from config import (
    TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, MARKETS
)

log = logging.getLogger("telegram")

# ── Helpers ───────────────────────────────────────────────────────────────

def _bar(value: float, total: float, width: int = 10) -> str:
    pct = max(0.0, min(1.0, value / total if total else 0))
    done = int(pct * width)
    return "█" * done + "░" * (width - done)

async def _safe_edit(query, text: str, reply_markup=None, parse_mode=ParseMode.HTML):
    """Prevents fatal crashes when a user double-taps a button causing no text change."""
    try:
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            pass # Ignore spam clicks
        else:
            log.error("Telegram edit error: %s", e)

async def _build_status_text(s) -> str:
    db = s.db
    bal = s.engine.balance if getattr(s, 'engine', None) else 0.0
    pnl = await db.get_today_pnl()
    cnt = await db.get_trade_count_today()
    summary = await db.get_lifetime_summary()
    total = summary.get("total_trades", 0)
    wins = summary.get("wins", 0)
    wr = f"{wins/total*100:.1f}%" if total else "—"

    target_bar = _bar(max(pnl, 0), getattr(s, 'daily_target', 0))
    loss_bar = _bar(max(-pnl, 0), abs(getattr(s, 'daily_stoploss', 0)))
    trade_status = (
        f"🔴 Open — ID {s.open_contract_id} | P&L {s.open_pnl:+.2f}"
        if getattr(s, 'open_contract_id', None) else "— No open trade"
    )
    
    mode = ("🟢 RUNNING" if getattr(s, 'trading', False) and not getattr(s, 'paused', False)
            else ("⏸ PAUSED" if getattr(s, 'paused', False) else "🔴 STOPPED"))

    # Safe fallback for currency to prevent AttributeError crashes in the UI thread
    currency_label = getattr(s, 'currency', 'USD') if getattr(s, 'engine', None) else ''

    return (
        f"<b>📊 Accumulator Variance Engine</b>  {mode}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Balance</b>   ${bal:.2f} {currency_label}\n"
        f"<b>Today P&L</b> {pnl:+.2f}\n"
        f"<b>Target</b>    {target_bar} ${max(pnl,0):.2f} / ${getattr(s, 'daily_target', 0):.2f}\n"
        f"<b>Stop-loss</b> {loss_bar} ${max(-pnl,0):.2f} / ${abs(getattr(s, 'daily_stoploss', 0)):.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Market</b>    <code>{getattr(s, 'market', 'N/A')}</code>\n"
        f"<b>Strategy</b>  <code>Variance Arbitrage</code>\n"
        f"<b>Stake</b>     ${getattr(s, 'stake', 0):.2f}\n"
        f"<b>Today's Trades</b> {cnt} | Win Rate: {wr}\n"
        f"<b>Live Trade</b>     {trade_status}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC</i>"
    )

# ── Dynamic Keyboards ──────────────────────────────────────────────────────

def _main_keyboard() -> InlineKeyboardMarkup:
    """The main control panel."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("▶ Start",   callback_data="op_start"),
         InlineKeyboardButton("⏹ Stop",    callback_data="op_stop")],
        [InlineKeyboardButton("⏸ Pause",   callback_data="op_pause"),
         InlineKeyboardButton("▶ Resume",  callback_data="op_resume")],
        [InlineKeyboardButton("📊 Dashboard", callback_data="op_status"),
         InlineKeyboardButton("⚙️ Settings", callback_data="menu_settings")],
        [InlineKeyboardButton("📈 AI Analytics", callback_data="op_analytics_ai"),
         InlineKeyboardButton("⬇ Export CSV", callback_data="op_export")],
    ])

def _settings_keyboard(s) -> InlineKeyboardMarkup:
    """The settings submenu (Stripped of obsolete contract toggles)."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Market: {getattr(s, 'market', 'N/A')}", callback_data="menu_market")],
        [InlineKeyboardButton(f"Stake: ${getattr(s, 'stake', 0):.2f}", callback_data="input_stake")],
        [InlineKeyboardButton(f"Target: ${getattr(s, 'daily_target', 0):.2f}", callback_data="input_target"),
         InlineKeyboardButton(f"Stop: -${abs(getattr(s, 'daily_stoploss', 0)):.2f}", callback_data="input_stoploss")],
        [InlineKeyboardButton("🔙 Back to Main", callback_data="menu_main")]
    ])

def _list_keyboard(items: dict, prefix: str) -> InlineKeyboardMarkup:
    """Generates a grid of buttons for a dictionary of items."""
    keyboard = []
    row = []
    for key, name in items.items():
        row.append(InlineKeyboardButton(key, callback_data=f"{prefix}_{key}"))
        if len(row) == 2:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("🔙 Back to Settings", callback_data="menu_settings")])
    return InlineKeyboardMarkup(keyboard)

# ── Controller Class ───────────────────────────────────────────────────────

class TelegramController:
    def __init__(self, bot_state):
        if not TELEGRAM_TOKEN:
            raise ValueError("TELEGRAM_TOKEN is not set. Get one from @BotFather.")
        self.state = bot_state
        self.app = Application.builder().token(TELEGRAM_TOKEN).build()
        self._awaiting_input = None  
        self._register_handlers()

    def _register_handlers(self):
        # Basic Commands
        self.app.add_handler(CommandHandler("start", self.cmd_menu))
        self.app.add_handler(CommandHandler("menu", self.cmd_menu))

        # Text input handler (catches users typing stakes/targets)
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text_input))

        # Button handlers separated by prefix
        self.app.add_handler(CallbackQueryHandler(self.handle_menu_nav, pattern="^menu_"))
        self.app.add_handler(CallbackQueryHandler(self.handle_op, pattern="^op_"))
        self.app.add_handler(CallbackQueryHandler(self.handle_input_request, pattern="^input_"))
        self.app.add_handler(CallbackQueryHandler(self.handle_set_market, pattern="^setmkt_"))
        
        # Global Error Boundary
        self.app.add_error_handler(self._error_handler)

    async def _error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Log the error and prevent the bot UI from crashing."""
        log.error("Exception while handling Telegram update:", exc_info=context.error)

    def _is_authorised(self, chat_id: int) -> bool:
        return chat_id == TELEGRAM_CHAT_ID

    async def push(self, text: str):
        try:
            await self.app.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=text,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            log.error("Push failed: %s", e)

    # ── Handlers ──────────────────────────────────────────────────────────

    async def cmd_menu(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorised(update.effective_chat.id): return
        self._awaiting_input = None
        await update.message.reply_text(
            "🤖 <b>Variance Arbitrage Control Panel</b>\nChoose an action:",
            reply_markup=_main_keyboard(),
            parse_mode=ParseMode.HTML,
        )

    async def _handle_text_input(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self._is_authorised(update.effective_chat.id) or not self._awaiting_input:
            return

        text = update.message.text.strip()
        try:
            val = float(text)
        except ValueError:
            await update.message.reply_text("❌ Please enter a valid number.")
            return

        s = self.state
        if self._awaiting_input == "stake":
            if val < 0.35:
                await update.message.reply_text("❌ Minimum stake is $0.35.")
                return
            s.stake = val
            await s.db.set_setting("stake", val)
            await update.message.reply_text(f"✅ Stake set to ${val:.2f}", reply_markup=_settings_keyboard(s))

        elif self._awaiting_input == "target":
            s.daily_target = abs(val)
            await s.db.set_setting("daily_target", s.daily_target)
            await update.message.reply_text(f"✅ Daily target set to +${s.daily_target:.2f}", reply_markup=_settings_keyboard(s))

        elif self._awaiting_input == "stoploss":
            s.daily_stoploss = -abs(val)
            await s.db.set_setting("daily_stoploss", s.daily_stoploss)
            await update.message.reply_text(f"✅ Daily stop-loss set to -${abs(s.daily_stoploss):.2f}", reply_markup=_settings_keyboard(s))

        self._awaiting_input = None

    async def handle_menu_nav(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not self._is_authorised(query.message.chat.id): return
        await query.answer()
        self._awaiting_input = None

        action = query.data.replace("menu_", "")

        if action == "main":
            await _safe_edit(query, "🤖 <b>Variance Arbitrage Control Panel</b>", reply_markup=_main_keyboard())
        elif action == "settings":
            await _safe_edit(query, "⚙️ <b>Bot Settings</b>\nTap a value to change it:", reply_markup=_settings_keyboard(self.state))
        elif action == "market":
            await _safe_edit(query, "📈 <b>Select Market</b>:", reply_markup=_list_keyboard(MARKETS, "setmkt"))

    async def handle_input_request(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not self._is_authorised(query.message.chat.id): return
        await query.answer()

        target = query.data.replace("input_", "")
        self._awaiting_input = target

        prompts = {
            "stake": "Send a message with the new Stake amount (e.g. 1.5):",
            "target": "Send a message with the new Daily Target (e.g. 5):",
            "stoploss": "Send a message with the new Stop-Loss amount (e.g. 2):"
        }
        await query.message.reply_text(f"⌨️ {prompts[target]}")

    async def handle_set_market(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not self._is_authorised(query.message.chat.id): return
        await query.answer()

        sym = query.data.replace("setmkt_", "")
        old = self.state.market
        self.state.market = sym
        await self.state.db.set_setting("market", sym)

        if self.state.engine:
            await self.state.engine.unsubscribe_ticks(old)
            await self.state.engine.subscribe_ticks(sym)

        await _safe_edit(query, f"✅ Market changed to <code>{sym}</code>", reply_markup=_settings_keyboard(self.state))

    async def handle_op(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        if not self._is_authorised(query.message.chat.id): return
        await query.answer()
        self._awaiting_input = None

        action = query.data.replace("op_", "")
        s = self.state
        reply = query.message.reply_text

        if action == "start":
            if getattr(s, 'trading', False):
                await reply("⚡ Bot is already running.")
            else:
                s.trading = True
                s.paused = False
                await s.db.set_setting("trading", True)
                await _safe_edit(query, f"▶ <b>Bot Started</b>\nMarket: <code>{getattr(s, 'market', 'N/A')}</code>\nStake: <code>${getattr(s, 'stake', 0):.2f}</code>", reply_markup=_main_keyboard())

        elif action == "stop":
            s.trading = False
            await s.db.set_setting("trading", False)
            note = "open trade will settle first" if getattr(s, 'open_contract_id', None) else "no open trades"
            await _safe_edit(query, f"⏹ <b>Bot Stopped</b> ({note})", reply_markup=_main_keyboard())

        elif action == "pause":
            s.paused = True
            await reply("⏸ Paused. No new entries until you tap Resume.")

        elif action == "resume":
            s.paused = False
            await reply("▶ Resumed.")

        elif action == "status":
            text = await _build_status_text(s)
            await _safe_edit(query, text, reply_markup=_main_keyboard())

        elif action == "export":
            path = await s.db.export_csv("trades_export.csv")
            if not path:
                await reply("No trades to export yet.")
            else:
                with open(path, "rb") as f:
                    await query.message.reply_document(
                        document=f,
                        filename=f"deriv_trades_{datetime.now(timezone.utc).date()}.csv",
                        caption="📊 Trade history"
                    )

        elif action == "analytics_ai":
            import analytics
            await reply("🤖 Generating AI Analysis... Please wait.")
            try:
                metrics = analytics.fetch_metrics(s.db._db)
                report_path = "ai_report.md"
                await asyncio.to_thread(analytics.analyze_with_gemini, metrics, report_path)
                
                with open(report_path, "r", encoding="utf-8") as f:
                    report_text = f.read()

                if len(report_text) > 4000:
                    with open(report_path, "rb") as f:
                        await query.message.reply_document(document=f, caption="🤖 Full AI Report")
                else:
                    await reply(report_text, parse_mode=ParseMode.MARKDOWN)

            except Exception as e:
                log.error("AI Analysis failed: %s", e)
                await reply("❌ Failed to generate AI report. Check logs.")

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def run(self):
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        log.info("Telegram UI online. Send /menu to your bot.")

    async def stop(self):
        await self.app.updater.stop()
        await self.app.stop()
        await self.app.shutdown()