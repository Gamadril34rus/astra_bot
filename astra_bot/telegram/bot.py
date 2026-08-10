"""
ASTRA BOT — Telegram-бот.

Полностью русскоязычный интерфейс с главным меню и выбором режима
торговли (демо / реальный счёт). Реальный счёт по умолчанию заблокирован
и требует явного подтверждения администратором.
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Any

from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)

from ..core.state import get_system_state
from ..engines.risk_engine import get_risk_engine
from ..paperengine.paper_engine import get_paper_engine

logger = logging.getLogger(__name__)


MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("📊 Статус"), KeyboardButton("📈 Отчёт")],
        [KeyboardButton("📍 Позиции"), KeyboardButton("🛡️ Риск")],
        [KeyboardButton("🏥 Здоровье"), KeyboardButton("⚙️ Счёт")],
        [KeyboardButton("❓ Помощь")],
    ],
    resize_keyboard=True,
    input_field_placeholder="Выберите действие",
)


def account_mode_keyboard(current_mode: str) -> InlineKeyboardMarkup:
    """Клавиатура выбора торгового режима."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    f"{'✅ ' if current_mode == 'paper' else ''}Демо-счёт",
                    callback_data="account:paper",
                ),
                InlineKeyboardButton(
                    f"{'✅ ' if current_mode == 'real' else ''}Реальный счёт",
                    callback_data="account:real",
                ),
            ],
            [
                InlineKeyboardButton(
                    "🔒 Подтвердить реальную торговлю",
                    callback_data="account:confirm_real",
                )
            ],
        ]
    )


class AstraTelegramBot:
    """Русскоязычный Telegram-бот с меню и выбором счёта."""

    def __init__(
        self,
        bot_token: str,
        allowed_user_ids: list[int] | None = None,
        admin_user_ids: list[int] | None = None,
    ):
        self.bot_token = bot_token
        self.allowed_user_ids = set(allowed_user_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])

        self._bot: Bot | None = None
        self._application: Application | None = None
        self._running = False

        # Режим счёта и флаг подтверждения реальной торговли.
        self.account_mode: str = "paper"
        self.real_trading_confirmed: bool = False

    async def initialize(self):
        self._bot = Bot(token=self.bot_token)
        self._application = Application.builder().token(self.bot_token).build()
        self._setup_handlers()
        logger.info("Telegram bot initialized")

    def _setup_handlers(self):
        app = self._application
        app.add_handler(CommandHandler("start", self._cmd_start))
        app.add_handler(CommandHandler("help", self._cmd_help))
        app.add_handler(CommandHandler("status", self._cmd_status))
        app.add_handler(CommandHandler("report", self._cmd_report))
        app.add_handler(CommandHandler("positions", self._cmd_positions))
        app.add_handler(CommandHandler("risk", self._cmd_risk))
        app.add_handler(CommandHandler("health", self._cmd_health))
        app.add_handler(CommandHandler("account", self._cmd_account))
        app.add_handler(CommandHandler("pause", self._cmd_pause))
        app.add_handler(CommandHandler("resume", self._cmd_resume))

        # Inline-кнопки
        app.add_handler(
            CallbackQueryHandler(self._cb_account, pattern=r"^account:")
        )

        # Текстовое меню
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text)
        )

    # ------------------------------------------------------------------ utils
    def _is_allowed(self, user_id: int) -> bool:
        return user_id in self.allowed_user_ids or user_id in self.admin_user_ids

    def _is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_user_ids

    @staticmethod
    def _fmt_money(value: Decimal | float | int | str | None) -> str:
        try:
            return f"{Decimal(str(value if value is not None else 0)):,.2f} ₽"
        except Exception:
            return "0.00 ₽"

    @staticmethod
    def _fmt_pct(value: Decimal | float | int | str | None) -> str:
        try:
            return f"{float(value if value is not None else 0):.2f}%"
        except Exception:
            return "0.00%"

    # ------------------------------------------------------------------ /start
    async def _reply(
        self,
        update: Update,
        text: str,
        reply_markup: Any | None = None,
    ) -> None:
        await update.message.reply_text(
            text, parse_mode="Markdown", reply_markup=reply_markup
        )

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not self._is_allowed(user_id):
            await update.message.reply_text("❌ Доступ запрещён")
            return

        is_admin = self._is_admin(user_id)
        await self._reply(
            update,
            "🤖 *ASTRA BOT*\n\n"
            "Добро пожаловать! Я ваш помощник для автономной торговли.\n"
            "Все операции по умолчанию идут на *демо-счёте* — пополнять "
            "реальный депозит не нужно.\n\n"
            "Команды:\n"
            "📊 Статус — текущее состояние бота\n"
            "📈 Отчёт — итоги дня и метрики\n"
            "📍 Позиции — открытые сделки\n"
            "🛡️ Риск — лимиты и риск-режим\n"
            "⚙️ Счёт — выбор демо/реального счёта\n"
            "❓ Помощь — полный список команд",
            reply_markup=MAIN_MENU,
        )
        if is_admin:
            await update.message.reply_text(
                "Вы администратор. Доступны команды /pause и /resume."
            )

    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update.effective_user.id):
            await update.message.reply_text("❌ Доступ запрещён")
            return
        await self._reply(
            update,
            "❓ *Помощь*\n\n"
            "/status — текущий статус\n"
            "/report — отчёт\n"
            "/positions — открытые позиции\n"
            "/risk — состояние риск-менеджмента\n"
            "/health — здоровье системы\n"
            "/account — выбор счёта (демо/реальный)\n\n"
            "Администратору:\n"
            "/pause — приостановить торговлю\n"
            "/resume — возобновить торговлю\n",
            reply_markup=MAIN_MENU,
        )

    # --------------------------------------------------------------- /status
    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update.effective_user.id):
            await update.message.reply_text("❌ Доступ запрещён")
            return

        state = get_system_state()
        risk = get_risk_engine()
        paper = get_paper_engine()

        mode = "📄 Демо" if self.account_mode == "paper" else "💵 Реальный"
        running = paper and paper.is_running

        text = (
            "📊 *СТАТУС*\n\n"
            f"*Счёт:* {mode}\n"
            f"*Торговля:* {'🟢 Работает' if running else '⏸️ Остановлена'}\n"
            f"*Капитал:* {self._fmt_money(state.current_equity)}\n"
            f"*Просадка:* {self._fmt_pct(state.current_drawdown)}\n"
            f"*Риск-режим:* {state.risk_state.value}\n"
            f"*Позиций:* {len(paper.get_positions()) if paper else 0}\n"
            f"*Сделок:* {state.total_trades} "
            f"(✅ {state.total_wins} / ❌ {state.total_losses})\n"
            f"*Дневной PnL:* {self._fmt_money(risk.daily_pnl)}\n"
            f"*Обновлено:* {datetime.utcnow().strftime('%H:%M:%S')} UTC"
        )
        await self._reply(update, text, reply_markup=MAIN_MENU)

    async def _cmd_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update.effective_user.id):
            await update.message.reply_text("❌ Доступ запрещён")
            return

        state = get_system_state()
        risk = get_risk_engine()

        total_pnl = state.total_net_pnl
        total_pct = state.total_pnl_pct
        pnl_icon = "🟢" if total_pnl >= 0 else "🔴"

        text = (
            f"📈 *ОТЧЁТ НА {datetime.utcnow().strftime('%d.%m.%Y')}*\n\n"
            "*💰 Капитал*\n"
            f"  Текущий: {self._fmt_money(state.current_equity)}\n"
            f"  Начальный: {self._fmt_money(state.initial_capital)}\n"
            f"  {pnl_icon} Прибыль: {self._fmt_money(total_pnl)} "
            f"({self._fmt_pct(total_pct)})\n\n"
            "*📉 Просадка*\n"
            f"  Текущая: {self._fmt_pct(state.current_drawdown)}\n"
            f"  Максимум: {self._fmt_pct(state.max_drawdown_ever)}\n\n"
            "*📊 Сделки*\n"
            f"  Всего: {state.total_trades}\n"
            f"  Побед/Поражений: {state.total_wins}/{state.total_losses}\n"
            f"  Win Rate: {self._fmt_pct((state.total_wins / state.total_trades * 100) if state.total_trades else 0)}\n\n"
            "*🛡️ Риск*\n"
            f"  Статус: {risk.risk_state.value}\n"
            f"  Дневной PnL: {self._fmt_money(risk.daily_pnl)}\n"
            f"  Множитель: {state.get_risk_multiplier():.2f}\n"
        )
        await self._reply(update, text, reply_markup=MAIN_MENU)

    async def _cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update.effective_user.id):
            await update.message.reply_text("❌ Доступ запрещён")
            return
        paper = get_paper_engine()
        positions = paper.get_positions() if paper else []

        if not positions:
            await self._reply(update, "📍 Открытых позиций нет.", reply_markup=MAIN_MENU)
            return

        lines = ["📍 *ОТКРЫТЫЕ ПОЗИЦИИ*\n"]
        for pos in positions:
            pnl = pos.pnl if hasattr(pos, "pnl") else Decimal("0")
            icon = "🟢" if pnl >= 0 else "🔴"
            lines.append(
                f"{icon} *{pos.symbol}* ({pos.side})\n"
                f"  Вход: {self._fmt_money(pos.entry_price)}\n"
                f"  Текущая: {self._fmt_money(pos.current_price)}\n"
                f"  Кол-во: {pos.quantity}\n"
                f"  PnL: {self._fmt_money(pnl)}\n"
            )
        await self._reply(update, "\n".join(lines), reply_markup=MAIN_MENU)

    async def _cmd_risk(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update.effective_user.id):
            await update.message.reply_text("❌ Доступ запрещён")
            return
        risk = get_risk_engine()
        info = risk.to_dict()
        text = (
            "🛡️ *РИСК-МЕНЕДЖМЕНТ*\n\n"
            f"*Статус:* {info['risk_state']}\n"
            f"*Торговля:* {'✅ Разрешена' if info['trading_enabled'] else '❌ Запрещена'}\n"
            f"*Капитал:* {self._fmt_money(info['current_equity'])}\n"
            f"*Просадка:* {self._fmt_pct(info['current_drawdown'])}\n"
            f"*Дневной PnL:* {self._fmt_money(info['daily_pnl'])}\n"
            f"*Недельный PnL:* {self._fmt_money(info['weekly_pnl'])}\n"
            f"*Множитель риска:* {info['risk_multiplier']}\n"
            f"*Открытых позиций:* {info['open_positions']}\n"
            f"*Сделок:* {info['total_trades']} "
            f"(W {info['total_wins']} / L {info['total_losses']})\n"
            f"*Win Rate:* {self._fmt_pct(info['win_rate'])}"
        )
        await self._reply(update, text, reply_markup=MAIN_MENU)

    async def _cmd_health(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update.effective_user.id):
            await update.message.reply_text("❌ Доступ запрещён")
            return
        state = get_system_state()
        text = (
            "🏥 *ЗДОРОВЬЕ СИСТЕМЫ*\n\n"
            f"*Общее:* {state.system_health.value}\n"
            f"*Режим:* {state.trading_state.value}\n"
            f"*Риск:* {state.risk_state.value}\n"
            f"*Ошибок сегодня:* {state.errors_today}\n"
            f"*Обновлено:* {datetime.utcnow().strftime('%H:%M:%S')} UTC"
        )
        await self._reply(update, text, reply_markup=MAIN_MENU)

    # ---------------------------------------------------------- /account
    async def _cmd_account(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_allowed(update.effective_user.id):
            await update.message.reply_text("❌ Доступ запрещён")
            return
        await update.message.reply_text(
            self._account_text(),
            parse_mode="Markdown",
            reply_markup=account_mode_keyboard(self.account_mode),
        )

    def _account_text(self) -> str:
        mode = "📄 Демо-счёт" if self.account_mode == "paper" else "💵 Реальный счёт"
        confirmed = (
            "✅ Подтверждена" if self.real_trading_confirmed else "🔒 Не подтверждена"
        )
        return (
            "⚙️ *ВЫБОР СЧЁТА*\n\n"
            f"*Текущий режим:* {mode}\n"
            f"*Реальная торговля:* {confirmed}\n\n"
            "ℹ️ Бот по умолчанию работает на *демо-счёте* — пополнять "
            "депозит не нужно. Для перехода на реальный счёт нажмите "
            "«Реальный счёт», затем подтвердите действие. Это может "
            "сделать только администратор."
        )

    async def _cb_account(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        user_id = query.from_user.id
        if not self._is_allowed(user_id):
            await query.edit_message_text("❌ Действие запрещено")
            return

        action = query.data.split(":", 1)[1]
        if action == "paper":
            self.account_mode = "paper"
            await query.edit_message_text(
                self._account_text(),
                parse_mode="Markdown",
                reply_markup=account_mode_keyboard(self.account_mode),
            )
            return

        if action == "real":
            if not self._is_admin(user_id):
                await query.edit_message_text(
                    "❌ Переключение на реальный счёт доступно только администратору."
                )
                return
            if not self.real_trading_confirmed:
                await query.edit_message_text(
                    "⚠️ *Внимание!*\n\n"
                    "Вы пытаетесь включить *реальную торговлю*. "
                    "Это операции с живыми деньгами. Убедитесь, что:\n"
                    "• API-ключи без прав на вывод;\n"
                    "• риск-параметры проверены;\n"
                    "• стратегии прошли 30 дней демо-теста.\n\n"
                    "Для подтверждения нажмите кнопу ниже.",
                    parse_mode="Markdown",
                    reply_markup=account_mode_keyboard(self.account_mode),
                )
                return
            self.account_mode = "real"
            await query.edit_message_text(
                self._account_text(),
                parse_mode="Markdown",
                reply_markup=account_mode_keyboard(self.account_mode),
            )
            return

        if action == "confirm_real":
            if not self._is_admin(user_id):
                await query.edit_message_text(
                    "❌ Подтверждать реальную торговлю может только администратор."
                )
                return
            self.real_trading_confirmed = True
            self.account_mode = "real"
            logger.warning("Реальная торговля подтверждена пользователем %s", user_id)
            await query.edit_message_text(
                "✅ *Реальная торговля подтверждена администратором.*\n\n"
                "Помните: риск-менеджмент по-прежнему ограничивает позиции "
                "согласно лимитам из конфигурации.",
                parse_mode="Markdown",
            )

    # ----------------------------------------------------------- /pause /resume
    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text(
                "❌ Только администратор может использовать эту команду"
            )
            return
        state = get_system_state()
        state.trading_state = "PAUSED"
        await update.message.reply_text("⏸️ Торговля приостановлена")
        logger.info("Trading paused by user %s", update.effective_user.id)

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._is_admin(update.effective_user.id):
            await update.message.reply_text(
                "❌ Только администратор может использовать эту команду"
            )
            return
        state = get_system_state()
        state.trading_state = "RUNNING"
        await update.message.reply_text("▶️ Торговля возобновлена")
        logger.info("Trading resumed by user %s", update.effective_user.id)

    # --------------------------------------------------------------- /text
    async def _handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not self._is_allowed(user_id):
            return
        text = (update.message.text or "").strip()

        dispatch = {
            "📊 Статус": self._cmd_status,
            "📈 Отчёт": self._cmd_report,
            "📍 Позиции": self._cmd_positions,
            "🛡️ Риск": self._cmd_risk,
            "🏥 Здоровье": self._cmd_health,
            "⚙️ Счёт": self._cmd_account,
            "❓ Помощь": self._cmd_help,
        }
        handler = dispatch.get(text)
        if handler:
            await handler(update, context)
        elif text.lower() in {"test", "тест"}:
            await update.message.reply_text("✅ Бот на связи!")

    # --------------------------------------------------------------- lifecycle
    async def send_alert(self, message: str, severity: str = "info"):
        if not self._application:
            return
        emoji = {
            "info": "ℹ️",
            "warning": "⚠️",
            "error": "❌",
            "critical": "🚨",
        }.get(severity, "📢")
        for admin_id in self.admin_user_ids:
            try:
                await self._bot.send_message(
                    chat_id=admin_id,
                    text=f"{emoji} *ASTRA ALERT*\n\n{message}",
                    parse_mode="Markdown",
                )
            except Exception as exc:
                logger.error("Не отправил алерт %s: %s", admin_id, exc)

    async def send_daily_report(self, report_text: str):
        await self.send_alert(report_text, "info")

    async def start(self):
        if not self._application:
            await self.initialize()
        self._running = True
        logger.info("Telegram bot started")
        await self._application.initialize()
        await self._application.start()
        await self._application.updater.start_polling()

    async def stop(self):
        self._running = False
        if self._application:
            try:
                await self._application.updater.stop()
                await self._application.stop()
                await self._application.shutdown()
            except Exception as exc:
                logger.warning("Telegram shutdown error: %s", exc)
        logger.info("Telegram bot stopped")


# Глобальный бот
_telegram_bot: AstraTelegramBot | None = None


def get_telegram_bot() -> AstraTelegramBot | None:
    return _telegram_bot


def set_telegram_bot(bot: AstraTelegramBot):
    global _telegram_bot
    _telegram_bot = bot


async def create_telegram_bot(
    bot_token: str,
    allowed_user_ids: list[int] | None = None,
    admin_user_ids: list[int] | None = None,
) -> AstraTelegramBot:
    bot = AstraTelegramBot(
        bot_token=bot_token,
        allowed_user_ids=allowed_user_ids,
        admin_user_ids=admin_user_ids,
    )
    await bot.initialize()
    set_telegram_bot(bot)
    return bot
