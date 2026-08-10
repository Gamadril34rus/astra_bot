"""
ASTRA BOT — Telegram Bot
Мониторинг и управление через Telegram
"""

import logging
from datetime import datetime

from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from telegram import Bot, Update

from ..core.state import get_system_state
from ..engines.risk_engine import get_risk_engine
from ..paperengine.paper_engine import get_paper_engine

logger = logging.getLogger(__name__)


class AstraTelegramBot:
    """
    Telegram бот для мониторинга и управления ASTRA BOT.

    Команды:
    - /status — Текущий статус
    - /report — Детальный отчёт
    - /positions — Открытые позиции
    - /risk — Риск-статус
    - /health — Здоровье системы
    - /pause — Пауза торговли
    - /resume — Продолжить торговлю
    """

    def __init__(
        self,
        bot_token: str,
        allowed_user_ids: list[int] = None,
        admin_user_ids: list[int] = None,
    ):
        self.bot_token = bot_token
        self.allowed_user_ids = set(allowed_user_ids or [])
        self.admin_user_ids = set(admin_user_ids or [])

        self._bot: Bot | None = None
        self._application: Application | None = None
        self._running = False

    async def initialize(self):
        """Инициализация бота"""
        self._bot = Bot(token=self.bot_token)
        self._application = Application.builder().token(self.bot_token).build()

        # Регистрация обработчиков
        self._setup_handlers()

        logger.info("Telegram bot initialized")

    def _setup_handlers(self):
        """Настройка обработчиков команд"""
        # Команды для всех пользователей
        self._application.add_handler(CommandHandler("start", self._cmd_start))
        self._application.add_handler(CommandHandler("status", self._cmd_status))
        self._application.add_handler(CommandHandler("report", self._cmd_report))
        self._application.add_handler(CommandHandler("positions", self._cmd_positions))
        self._application.add_handler(CommandHandler("risk", self._cmd_risk))
        self._application.add_handler(CommandHandler("health", self._cmd_health))

        # Команды только для админов
        self._application.add_handler(CommandHandler("pause", self._cmd_pause))
        self._application.add_handler(CommandHandler("resume", self._cmd_resume))

        # Обработчик текстовых сообщений (для обратной связи)
        self._application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message)
        )

    async def _cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /start"""
        user_id = update.effective_user.id

        if not self._is_allowed(user_id):
            await update.message.reply_text("❌ Доступ запрещён")
            return

        await update.message.reply_text(
            "🤖 *ASTRA BOT*\n\n"
            "Добро пожаловать в систему автономной торговли.\n\n"
            "Доступные команды:\n"
            "/status — Текущий статус\n"
            "/report — Подробный отчёт\n"
            "/positions — Открытые позиции\n"
            "/risk — Риск-статус\n"
            "/health — Здоровье системы\n\n"
            "_Для администратора: /pause, /resume_",
            parse_mode="Markdown"
        )

    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /status"""
        user_id = update.effective_user.id

        if not self._is_allowed(user_id):
            await update.message.reply_text("❌ Доступ запрещён")
            return

        state = get_system_state()
        risk = get_risk_engine()
        paper = get_paper_engine()

        status_text = (
            f"📊 *ASTRA BOT STATUS*\n\n"
            f"*Режим:* {'📄 Paper Trading' if paper and paper.is_running else '⏸️ Stopped'}\n"
            f"*Капитал:* {state.current_equity:.2f} ₽\n"
            f"*Просадка:* {state.current_drawdown:.2f}%\n"
            f"*Риск-режим:* {state.risk_state.value}\n"
            f"*Торговля:* {'✅ Вкл' if state.trading_state == 'RUNNING' else '❌ Выкл'}\n"
            f"*Позиции:* {len(paper.get_positions()) if paper else 0}\n"
            f"*Сделки:* {state.total_trades} "
            f"({state.total_wins}W/{state.total_losses}L)\n"
            f"*Дневной PnL:* {risk.daily_pnl:.2f} ₽\n"
            f"*Множитель риска:* {state.get_risk_multiplier():.2f}\n\n"
            f"*Последнее обновление:* {datetime.utcnow().strftime('%H:%M:%S')}"
        )

        await update.message.reply_text(status_text, parse_mode="Markdown")

    async def _cmd_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /report"""
        user_id = update.effective_user.id

        if not self._is_allowed(user_id):
            await update.message.reply_text("❌ Доступ запрещён")
            return

        state = get_system_state()
        risk = get_risk_engine()

        report = (
            f"📈 *ASTRA BOT DAILY REPORT*\n\n"
            f"*Дата:* {datetime.utcnow().strftime('%d.%m.%Y')}\n\n"
            f"*Капитал:*\n"
            f"  Текущий: {state.current_equity:.2f} ₽\n"
            f"  Начальный: {state.initial_capital:.2f} ₽\n"
            f"  Прибыль: {state.total_net_pnl:.2f} ₽ "
            f"({state.total_pnl_pct:.2f}%)\n\n"
            f"*Просадка:*\n"
            f"  Текущая: {state.current_drawdown:.2f}%\n"
            f"  Макс: {state.max_drawdown_ever:.2f}%\n\n"
            f"*Торговля:*\n"
            f"  Всего сделок: {state.total_trades}\n"
            f"  Побед: {state.total_wins}\n"
            f"  Поражений: {state.total_losses}\n"
            f"  Win Rate: {(state.total_wins/state.total_trades*100) if state.total_trades > 0 else 0:.1f}%\n\n"
            f"*Стратегии:*\n"
        )

        for name, strat in state.strategies.items():
            report += (
                f"  {name}: PF={strat.profit_factor:.2f}, "
                f"TR={strat.total_trades}, "
                f"{'✅' if strat.is_running else '❌'}\n"
            )

        report += (
            f"\n*Риск:*\n"
            f"  Статус: {risk.risk_state.value}\n"
            f"  Дневной PnL: {risk.daily_pnl:.2f} ₽\n"
            f"  Множитель: {state.get_risk_multiplier():.2f}\n"
        )
        report += f"\n*Режим рынка:* {state.market_regimes.get('BTC/USDT', {}).get('regime', 'UNKNOWN')}\n"
        report += f"\n*Здоровье системы:* {state.system_health.value}"

        await update.message.reply_text(report, parse_mode="Markdown")

    async def _cmd_positions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /positions"""
        user_id = update.effective_user.id

        if not self._is_allowed(user_id):
            await update.message.reply_text("❌ Доступ запрещён")
            return

        paper = get_paper_engine()

        if not paper:
            await update.message.reply_text("📭 Позиций нет (paper engine не инициализирован)")
            return

        positions = paper.get_positions()

        if not positions:
            await update.message.reply_text("📭 Открытых позиций нет")
            return

        text = "📍 *OPEN POSITIONS*\n\n"

        for pos in positions:
            text += (
                f"*{pos.symbol}*\n"
                f"  Side: {pos.side}\n"
                f"  Entry: {pos.entry_price:.2f}\n"
                f"  Current: {pos.current_price:.2f}\n"
                f"  Size: {pos.quantity:.6f}\n"
                f"  PnL: {pos.pnl:.2f} ({pos.pnl_pct:.2f}%)\n"
                f"  Strategy: {pos.strategy_name}\n\n"
            )

        await update.message.reply_text(text, parse_mode="Markdown")

    async def _cmd_risk(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /risk"""
        user_id = update.effective_user.id

        if not self._is_allowed(user_id):
            await update.message.reply_text("❌ Доступ запрещён")
            return

        risk = get_risk_engine()

        if not risk:
            await update.message.reply_text("❌ Риск-движок не инициализирован")
            return

        risk_info = risk.to_dict()

        text = (
            f"🛡️ *RISK STATUS*\n\n"
            f"*Режим:* {risk_info['risk_state']}\n"
            f"*Торговля:* {'✅ Вкл' if risk_info['trading_enabled'] else '❌ Выкл'}\n\n"
            f"*Капитал:* {risk_info['current_equity']} ₽\n"
            f"*Просадка:* {risk_info['current_drawdown']}%\n"
            f"*Множитель риска:* {risk_info['risk_multiplier']}\n\n"
            f"*Дневные потери:* {risk_info['daily_pnl']} ₽\n"
            f"*Недельные потери:* {risk_info['weekly_pnl']} ₽\n\n"
            f"*Позиции:* {risk_info['open_positions']} открытых\n"
            f"*Сделки:* {risk_info['total_trades']} "
            f"({risk_info['total_wins']}W/{risk_info['total_losses']}L)\n"
            f"*Win Rate:* {risk_info['win_rate']:.1f}%"
        )

        await update.message.reply_text(text, parse_mode="Markdown")

    async def _cmd_health(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /health"""
        user_id = update.effective_user.id

        if not self._is_allowed(user_id):
            await update.message.reply_text("❌ Доступ запрещён")
            return

        state = get_system_state()

        health_text = (
            f"🏥 *SYSTEM HEALTH*\n\n"
            f"*Общее:* {state.system_health.value}\n"
            f"*Режим:* {state.trading_state.value}\n"
            f"*Риск:* {state.risk_state.value}\n\n"
            f"*Стратегии:*\n"
        )

        for name, strat in state.strategies.items():
            health_text += (
                f"  {name}: {'✅ Healthy' if strat.is_healthy else '⚠️ Issues'}\n"
            )

        health_text += f"\n*Последнее обновление:* {datetime.utcnow().strftime('%H:%M:%S')}"

        await update.message.reply_text(health_text, parse_mode="Markdown")

    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /pause (только админ)"""
        user_id = update.effective_user.id

        if not self._is_admin(user_id):
            await update.message.reply_text("❌ Только админ может использовать эту команду")
            return

        state = get_system_state()
        state.trading_state = "PAUSED"

        await update.message.reply_text("⏸️ Торговля приостановлена")
        logger.info(f"Trading paused by user {user_id}")

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /resume (только админ)"""
        user_id = update.effective_user.id

        if not self._is_admin(user_id):
            await update.message.reply_text("❌ Только админ может использовать эту команду")
            return

        state = get_system_state()
        state.trading_state = "RUNNING"

        await update.message.reply_text("▶️ Торговля возобновлена")
        logger.info(f"Trading resumed by user {user_id}")

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка текстовых сообщений"""
        user_id = update.effective_user.id

        if not self._is_allowed(user_id):
            return

        text = update.message.text

        # Эхо для отладки
        if text == "test":
            await update.message.reply_text("✅ Bot is alive!")

        logger.debug(f"Message from {user_id}: {text}")

    def _is_allowed(self, user_id: int) -> bool:
        """Проверить имеет ли пользователь доступ"""
        return user_id in self.allowed_user_ids or user_id in self.admin_user_ids

    def _is_admin(self, user_id: int) -> bool:
        """Проверить является ли пользователь админом"""
        return user_id in self.admin_user_ids

    async def send_alert(self, message: str, severity: str = "info"):
        """Отправить оповещение всем админам"""
        if not self._application:
            return

        emoji = {"info": "ℹ️", "warning": "⚠️", "error": "❌", "critical": "🚨"}.get(severity, "📢")

        for admin_id in self.admin_user_ids:
            try:
                await self._bot.send_message(
                    chat_id=admin_id,
                    text=f"{emoji} *ASTRA ALERT*\n\n{message}",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Failed to send alert to {admin_id}: {e}")

    async def send_daily_report(self, report_text: str):
        """Отправить ежедневный отчёт"""
        await self.send_alert(report_text, "info")

    async def start(self):
        """Запустить бота"""
        if not self._application:
            await self.initialize()

        self._running = True
        logger.info("Telegram bot started")

        await self._application.initialize()
        await self._application.start()
        await self._application.updater.start_polling()

    async def stop(self):
        """Остановить бота"""
        self._running = False

        if self._application:
            await self._application.updater.stop()
            await self._application.stop()
            await self._application.shutdown()

        logger.info("Telegram bot stopped")


# Глобальный бот
_telegram_bot: AstraTelegramBot | None = None


def get_telegram_bot() -> AstraTelegramBot:
    """Получить глобальный Telegram бот"""
    global _telegram_bot
    return _telegram_bot


def set_telegram_bot(bot: AstraTelegramBot):
    """Установить глобальный Telegram бот"""
    global _telegram_bot
    _telegram_bot = bot


async def create_telegram_bot(
    bot_token: str,
    allowed_user_ids: list[int] = None,
    admin_user_ids: list[int] = None,
) -> AstraTelegramBot:
    """Создать и инициализировать Telegram бота"""
    bot = AstraTelegramBot(
        bot_token=bot_token,
        allowed_user_ids=allowed_user_ids,
        admin_user_ids=admin_user_ids,
    )
    await bot.initialize()
    set_telegram_bot(bot)
    return bot
