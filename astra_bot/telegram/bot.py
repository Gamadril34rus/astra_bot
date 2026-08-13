"""
ASTRA BOT — Telegram-бот (русскоязычное меню).

Команды меню (русские + английские алиасы), регистрируемые через
``setMyCommands``:

* /обучение  — запустить self-play + переобучение (берёт «живой» капитал
  из ``models/training_state.json``, поэтому счёт обучения растёт/падает
  от прогресса, а не всегда 2000 ₽);
* /стоп      — прекратить обучение (кооперативная остановка цикла);
* /баланс    — общий капитал, плюсы и минусы (paper-движок и OKX demo);
* /настройки — время ежедневного отчёта, тихие часы, вкл/выкл алертов;
* а также /статус /отчёт /позиции /риск /здоровье /счёт /пауза /возобновить
  /помощь.

Все русские команды дублируются английскими алиасами для совместимости.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Awaitable, Callable

from telegram import (
    Bot,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from ..core.state import get_system_state
from ..core.training_state import get_training_state
from ..engines.risk_engine import get_risk_engine
from ..paperengine.paper_engine import get_paper_engine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- меню/команды

# Telegram разрешает в командах только латиницу/цифры/подчёркивание,
# поэтому служебные команды — английские, но описания и кнопки меню —
# на русском. Сообщения вида «/обучение» пользователь не набирает: он
# жмёт русскую кнопку меню, которая дёргает соответствующий обработчик.
# Пары (команда, описание для меню Telegram). Порядок = порядок в меню.
BOT_COMMANDS: list[tuple[str, str]] = [
    ("train", "🎓 Запустить обучение (self-play + переобучение)"),
    ("stop", "⏹ Прекратить обучение"),
    ("balance", "💰 Баланс: общий, плюсы, минусы"),
    ("settings", "⏰ Настройки времени оповещений"),
    ("status", "📊 Текущее состояние бота"),
    ("report", "📈 Итоги дня и метрики"),
    ("positions", "📍 Открытые сделки"),
    ("risk", "🛡️ Лимиты и риск-режим"),
    ("health", "🏥 Здоровье системы"),
    ("schedule", "⏰ Бюджет торговых часов в месяц"),
    ("ready", "🎯 Готовность к реальному счёту"),
    ("account", "⚙️ Выбор демо/реального счёта"),
    ("pause", "⏸ Приостановить торговлю (админ)"),
    ("resume", "▶️ Возобновить торговлю (админ)"),
    ("help", "❓ Справка по командам"),
]

# Имя команды -> метод-обработчик.
COMMAND_HANDLERS: dict[str, str] = {
    "train": "_cmd_train",
    "stop": "_cmd_stop_training",
    "balance": "_cmd_balance",
    "settings": "_cmd_settings",
    "status": "_cmd_status",
    "report": "_cmd_report",
    "positions": "_cmd_positions",
    "risk": "_cmd_risk",
    "health": "_cmd_health",
    "schedule": "_cmd_schedule",
    "ready": "_cmd_ready",
    "account": "_cmd_account",
    "pause": "_cmd_pause",
    "resume": "_cmd_resume",
    "help": "_cmd_help",
    "start": "_cmd_help",
}

# Русские «алиасы», которые вводит пользователь текстом (через слеш), —
# Telegram их не показывает в меню, но мы их распознаём и маршрутизируем
# так же, как латинские команды.
RUSSIAN_ALIASES: dict[str, str] = {
    "обучение": "train",
    "стоп": "stop",
    "баланс": "balance",
    "настройки": "settings",
    "статус": "status",
    "отчет": "report",
    "отчёт": "report",
    "позиции": "positions",
    "риск": "risk",
    "здоровье": "health",
    "расписание": "schedule",
    "бюджет": "schedule",
    "готовность": "ready",
    "счет": "account",
    "счёт": "account",
    "пауза": "pause",
    "возобновить": "resume",
    "помощь": "help",
}

# Обратная совместимость: английский -> русский текст кнопки/документации.
COMMAND_ALIASES: dict[str, str] = {
    "train": "обучение",
    "stop": "стоп",
    "balance": "баланс",
    "settings": "настройки",
    "status": "статус",
    "report": "отчёт",
    "positions": "позиции",
    "risk": "риск",
    "health": "здоровье",
    "account": "счёт",
    "pause": "пауза",
    "resume": "возобновить",
    "help": "помощь",
    "start": "помощь",
}

MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton("🎓 Обучение"), KeyboardButton("⏹ Стоп")],
        [KeyboardButton("💰 Баланс"), KeyboardButton("⏰ Настройки")],
        [KeyboardButton("📊 Статус"), KeyboardButton("📈 Отчёт")],
        [KeyboardButton("📍 Позиции"), KeyboardButton("🛡️ Риск")],
        [KeyboardButton("🏥 Здоровье"), KeyboardButton("⏰ Расписание")],
        [KeyboardButton("🎯 Готовность"), KeyboardButton("⚙️ Счёт")],
        [KeyboardButton("❓ Помощь")],
    ],
    resize_keyboard=True,
    input_field_placeholder="Выберите действие или введите /команду",
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


def settings_keyboard(state) -> InlineKeyboardMarkup:
    """Инлайн-клавиатура настроек оповещений."""
    alerts_label = "🔔 Алерты: ВКЛ" if state.alerts_enabled else "🔕 Алерты: ВЫКЛ"
    q = "нет" if not state.quiet_hours_start else f"{state.quiet_hours_start}–{state.quiet_hours_end}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(alerts_label, callback_data="settings:toggle_alerts"),
            ],
            [
                InlineKeyboardButton(f"⏰ Отчёт: {state.daily_report_time} МСК", callback_data="settings:report_help"),
            ],
            [
                InlineKeyboardButton(f"🌙 Тихие часы: {q}", callback_data="settings:quiet_help"),
            ],
            [
                InlineKeyboardButton("ℹ️ Как менять", callback_data="settings:help"),
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
        self._webhook_mode = False
        self.account_mode: str = "paper"
        self.real_trading_confirmed: bool = False

        # Состояние обучения (флаг стопа, капитал, настройки).
        self._ts = get_training_state()

        # Ссылка на приложение AstraBot (main.py), если подключена —
        # используется для запуска multi-timeframe обучения как в CI.
        self.bot_app: Any = None

        # Текущая фоновая задача обучения (для /стоп).
        self._train_task: asyncio.Task | None = None
        self._train_session: str | None = None
        self._train_started_at: datetime | None = None

    # ----------------------------------------------------------- lifecycle
    async def initialize(self):
        self._bot = Bot(token=self.bot_token)
        self._application = Application.builder().token(self.bot_token).build()
        self._setup_handlers()
        logger.info("Telegram bot initialized")

    def _setup_handlers(self):
        app = self._application

        # Латинские команды (то, что реально показывается в меню Telegram).
        for cmd, method_name in COMMAND_HANDLERS.items():
            app.add_handler(CommandHandler(cmd, getattr(self, method_name)))

        # Inline-кнопки
        app.add_handler(CallbackQueryHandler(self._cb_account, pattern=r"^account:"))
        app.add_handler(CallbackQueryHandler(self._cb_settings, pattern=r"^settings:"))

        # Текстовое меню + русские «команды» (/обучение и т.п.). Русский
        # слеш Telegram считает обычным текстом, поэтому ловим его тут.
        app.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text)
        )

    async def set_bot_commands(self) -> None:
        """Зарегистрировать русские команды в меню Telegram (setMyCommands)."""
        if not self._bot:
            return
        commands = [BotCommand(cmd, desc) for cmd, desc in BOT_COMMANDS]
        # Русская локаль — чтобы описание было на русском в русских клиентах,
        # плюс дефолтный скоуп.
        await self._bot.set_my_commands(commands)
        try:
            from telegram import BotCommandScopeAllPrivateChats
            await self._bot.set_my_commands(
                commands, scope=BotCommandScopeAllPrivateChats()
            )
        except Exception as exc:  # pragma: no cover
            logger.debug("Не задал команды для приватных чатов: %s", exc)
        logger.info("Зарегистрировано %d команд меню Telegram", len(commands))

    # ------------------------------------------------------------------ utils
    def _is_allowed(self, user_id: int) -> bool:
        return user_id in self.allowed_user_ids or user_id in self.admin_user_ids

    def _is_admin(self, user_id: int) -> bool:
        return user_id in self.admin_user_ids

    @staticmethod
    def _fmt_money(value: Decimal | float | int | str | None, currency: str = "₽") -> str:
        try:
            return f"{Decimal(str(value if value is not None else 0)):,.2f} {currency}"
        except Exception:
            return f"0.00 {currency}"

    @staticmethod
    def _fmt_pct(value: Decimal | float | int | str | None) -> str:
        try:
            return f"{float(value if value is not None else 0):.2f}%"
        except Exception:
            return "0.00%"

    @staticmethod
    def _pnl_icon(value) -> str:
        return "🟢" if Decimal(str(value)) >= 0 else "🔴"

    async def _reply(self, update: Update, text: str, reply_markup: Any | None = None):
        try:
            await update.message.reply_text(
                text, parse_mode="Markdown", reply_markup=reply_markup
            )
        except Exception:
            # Падает, например, на незакрытых символах Markdown в числах —
            # повторяем без разметки, чтобы пользователь всё равно получил ответ.
            await update.message.reply_text(
                text.replace("*", "").replace("_", " ").replace("`", ""),
                reply_markup=reply_markup,
            )

    async def _deny(self, update: Update, admin_only: bool = False) -> bool:
        uid = update.effective_user.id
        if not self._is_allowed(uid):
            await update.message.reply_text("❌ Доступ запрещён")
            return True
        if admin_only and not self._is_admin(uid):
            await update.message.reply_text("❌ Только для администратора")
            return True
        return False

    # -------------------------------------------------------------- /обучение
    async def _cmd_train(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self._deny(update, admin_only=True):
            return

        if self._train_task is not None and not self._train_task.done():
            await self._reply(
                update,
                "⚠️ Обучение уже запущено.\n"
                f"Сессия: `{self._train_session}`\n"
                "Чтобы остановить — /стоп.",
            )
            return

        args = context.args or []
        offline = any(a.startswith("--offline") for a in args)
        bars = 3000
        for a in args:
            if a.startswith("--bars="):
                try:
                    bars = int(a.split("=", 1)[1])
                except ValueError:
                    pass

        # «Живой» капитал обучения: берём из персистентного состояния,
        # а не зашиваем 2000 ₽. После прошлого запуска он может быть
        # больше (плюс) или меньше (минус), с защитой от слива.
        ts = get_training_state()
        start_capital = ts.get_initial_capital()

        await self._reply(
            update,
            f"🎓 *Запускаю обучение*\n\n"
            f"Стартовый капитал сессии: {self._fmt_money(start_capital)}\n"
            f"Режим: {'офлайн ' + str(bars) + ' баров' if offline else 'self-play на истории OKX'}\n"
            f"Защита: позиция 5%, дневной лимит 3%, макс. просадка 15%.\n\n"
            "После прохода переобучу LightGBM и пришлю отчёт. "
            "Остановить — /стоп.",
        )

        session = uuid.uuid4().hex[:12]
        self._train_session = session
        self._train_started_at = datetime.utcnow()
        ts.start_session(session)

        loop = asyncio.get_event_loop()
        self._train_task = loop.create_task(
            self._run_training(session=session, offline=offline, bars=bars,
                               start_capital=start_capital, chat_id=update.effective_chat.id)
        )

        def _on_done(t: asyncio.Task):
            if t is self._train_task:
                self._train_task = None
                self._train_session = None
                self._train_started_at = None

        self._train_task.add_done_callback(_on_done)

    async def _run_training(
        self,
        session: str,
        offline: bool,
        bars: int,
        start_capital: Decimal,
        chat_id: int,
    ) -> None:
        """Фоновый прогон self-play + переобучение. Не бросает исключений
        наружу — все ошибки уходят в чат как сообщение."""
        from ..ml.self_play import SelfPlayConfig, SelfPlayEngine, format_daily_report
        from ..ml.weekly_learner import train_weekly

        ts = get_training_state()

        def should_stop() -> bool:
            return get_training_state().should_stop()

        try:
            engine = SelfPlayEngine(
                SelfPlayConfig(initial_capital=Decimal(str(start_capital)))
            )
            # По умолчанию тянем реальную историю OKX; если ключей нет
            # или сеть недоступна — мягкий фолбэк на синтетику, чтобы
            # команда не падала с ошибкой.
            client = None
            use_offline = offline
            if not offline:
                try:
                    import os as _os
                    from ..adapters.okx import OKXClient as _OKX

                    if _os.environ.get("OKX_API_KEY"):
                        client = _OKX({
                            "api_key": _os.environ["OKX_API_KEY"],
                            "api_secret": _os.environ["OKX_API_SECRET"],
                            "passphrase": _os.environ.get(
                                "OKX_API_PASSPHRASE",
                                _os.environ.get("OKX_PASSPHRASE", ""),
                            ),
                            "sandbox": _os.environ.get("OKX_DEMO", "1").lower()
                                       not in {"0", "false", "no"},
                        })
                        await client.initialize()
                except Exception as exc:  # noqa: BLE001
                    logger.info("OKX недоступен для /train, иду в офлайн: %s", exc)
                    client = None
                    use_offline = True
            report = await engine.run(
                client=client,
                offline_bars=bars if use_offline else 0,
                should_stop=should_stop,
            )
            if client is not None:
                try:
                    await client.close()
                except Exception:
                    pass
            # Сохраняем «живой» капитал для следующего запуска.
            next_capital = ts.record_run(
                final_equity=Decimal(str(report.final_equity)),
                trades=report.total_trades,
                wins=report.wins,
                losses=report.losses,
                pnl=Decimal(str(report.total_pnl)),
            )
            training = train_weekly(min_samples=100)
            text = format_daily_report(report)
            text += (
                "\n\n💼 *Движение капитала обучения*\n"
                f"  Старт сессии: {self._fmt_money(start_capital)}\n"
                f"  Финиш: {self._fmt_money(report.final_equity)}\n"
                f"  Следующий старт: {self._fmt_money(next_capital)}\n"
            )
            if training.trained:
                text += (
                    f"\n🧠 *Модель:* {training.version}\n"
                    f"   AUC={training.roc_auc:.3f}, "
                    f"accuracy={training.accuracy:.3f}, "
                    f"win-rate={training.positive_rate*100:.1f}%"
                )
            else:
                text += f"\n🧠 {training.message}"
            await self._send(chat_id, text)
        except asyncio.CancelledError:
            logger.info("Обучение %s отменено", session)
            raise
        except Exception as exc:
            logger.exception("Training command failed")
            await self._send(chat_id, f"❌ Ошибка обучения: {exc}")

    async def _send(self, chat_id: int, text: str) -> None:
        if not self._bot:
            return
        try:
            await self._bot.send_message(
                chat_id=chat_id, text=text, parse_mode="Markdown"
            )
        except Exception as exc:
            logger.error("Не отправил сообщение в %s: %s", chat_id, exc)

    # -------------------------------------------------------------- /стоп
    async def _cmd_stop_training(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self._deny(update, admin_only=True):
            return
        ts = get_training_state()

        running = self._train_task is not None and not self._train_task.done()
        if not running and not ts.stop_requested:
            await self._reply(
                update,
                "ℹ️ Сейчас обучение не запущено. Команда /стоп остановит "
                "активную сессию, когда она будет идти.",
            )
            return

        ts.request_stop()
        if self._train_task is not None and not self._train_task.done():
            self._train_task.cancel()
        await self._reply(
            update,
            "⏹ Отправлен запрос на прекращение обучения.\n"
            "Текущий цикл допишет уже собранные уроки и сохранит модель.",
        )
        logger.info("Training stop requested by user %s", update.effective_user.id)

    # ------------------------------------------------------------ /баланс
    async def _cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self._deny(update):
            return

        ts = get_training_state()
        paper = get_paper_engine()
        risk = get_risk_engine()

        lines = ["💰 *БАЛАНС*", ""]

        # --- Движок обучения (self-play) ---
        start_cap = ts.get_initial_capital()
        last_final = Decimal(ts.last_final_equity)
        delta = last_final - start_cap
        lines += [
            "*🎓 Капитал обучения (демо self-play)*",
            f"  Текущий (следующий старт): {self._fmt_money(start_cap)}",
            f"  Прошлая сессия: {self._fmt_money(last_final)} "
            f"({self._pnl_icon(delta)} {self._fmt_money(delta).strip()})",
            f"  Лучший исторический: {self._fmt_money(ts.stats.best_equity)}",
            f"  Худший исторический: {self._fmt_money(ts.stats.worst_equity)}",
            "",
        ]

        # --- Плюсы и минусы по всем учебным сессиям ---
        st = ts.stats
        total_pnl = Decimal(str(st.total_pnl))
        lines += [
            "*📈 Плюсы / минусы (всего учебных сессий: " + str(st.runs) + ")*",
            f"  Сделок: {st.total_trades}  (✅ {st.wins} / ❌ {st.losses})",
            f"  Накопленный PnL: {self._pnl_icon(total_pnl)} {self._fmt_money(total_pnl)}",
            f"  За последнюю сессию: {self._fmt_money(ts.last_run_pnl)} "
            f"({ts.last_run_trades} сделок, {ts.last_run_at or '—'})",
            "",
        ]

        # --- Бумажный счёт бота ---
        if paper is not None:
            acc = paper.get_account_info()
            eq = Decimal(acc["equity"])
            init = Decimal(acc["initial_capital"])
            pnl = Decimal(acc["total_pnl"])
            lines += [
                "*🤖 Бумажный счёт бота*",
                f"  Капитал: {self._fmt_money(eq)} (старт {self._fmt_money(init)})",
                f"  {self._pnl_icon(pnl)} PnL: {self._fmt_money(pnl)} "
                f"({acc['total_pnl_pct']})",
                f"  Просадка: {acc['current_drawdown']}",
                f"  Открытых позиций: {acc['open_positions']}",
                f"  Сделок: {acc['total_trades']} "
                f"(W {acc['wins']} / L {acc['losses']}, WR {acc['win_rate']})",
                "",
            ]

        # --- OKX demo ---
        okx_lines = await self._okx_balance_lines()
        if okx_lines:
            lines += okx_lines

        # --- Риск-лимиты (защита от слива) ---
        lines += [
            "*🛡️ Защита от слива*",
            f"  Риск-режим: {risk.risk_state.value}",
            f"  Дневной PnL: {self._fmt_money(risk.daily_pnl)}",
            "  В обучении: 5% на сделку, стоп-день −3%, стоп-сессии −15%, "
            "минимальный счёт 500 ₽.",
        ]

        await self._reply(update, "\n".join(lines), reply_markup=MAIN_MENU)

    async def _okx_prices(self, assets: list[str]) -> dict[str, float]:
        """Текущие цены монет в USDT (для оценки портфеля)."""
        prices: dict[str, float] = {}
        try:
            import os
            from ..adapters.okx import OKXClient
            client = OKXClient({
                "api_key": os.environ.get("OKX_API_KEY", ""),
                "api_secret": os.environ.get("OKX_API_SECRET", ""),
                "passphrase": os.environ.get("OKX_API_PASSPHRASE", ""),
                "sandbox": os.environ.get("OKX_DEMO", "1").lower()
                           not in {"0", "false", "no"},
            })
            await client.initialize()
            try:
                for asset in set(assets):
                    if asset in ("USDT", "", None):
                        continue
                    try:
                        t = await client.get_ticker(f"{asset}-USDT")
                        if t and t.get("last"):
                            prices[asset] = float(t["last"])
                    except Exception:
                        continue
            finally:
                await client.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("prices fetch failed: %s", exc)
        return prices

    async def _okx_balance_lines(self) -> list[str]:
        """Получить баланс OKX demo (приватный API). В логи секреты не пишем."""
        try:
            import os
            from ..adapters.okx import OKXClient

            key = os.environ.get("OKX_API_KEY", "")
            secret = os.environ.get("OKX_API_SECRET", "")
            passphrase = os.environ.get("OKX_API_PASSPHRASE") or os.environ.get(
                "OKX_PASSPHRASE", ""
            )
            if not (key and secret and passphrase):
                return ["*🏦 OKX demo:* ключи не заданы", ""]

            client = OKXClient(
                {
                    "api_key": key,
                    "api_secret": secret,
                    "passphrase": passphrase,
                    "sandbox": os.environ.get("OKX_DEMO", "1").lower()
                               not in {"0", "false", "no"},
                }
            )
            await client.initialize()
            try:
                bals = await client.get_account_balance()
                funding = await client.get_funding_balance()
            finally:
                await client.close()

            out: list[str] = []
            total_usdt = Decimal("0")
            prices = await self._okx_prices(list(bals.keys()) + list(funding.keys()))

            def _emit(title: str, balances) -> None:
                nonlocal total_usdt
                if not balances:
                    return
                out.append(title)
                for asset, b in balances.items():
                    out.append(f"  {asset}: {b.free:f} / всего {b.total:f}")
                    if asset == "USDT":
                        total_usdt += b.total
                    else:
                        px = prices.get(asset)
                        if px and b.total:
                            total_usdt += b.total * Decimal(str(px))

            _emit("*🏦 OKX demo — торговый счёт*", bals)
            _emit("*💼 OKX demo — funding-счёт*", funding)

            if not out:
                return ["*🏦 OKX demo:* баланс пуст или API недоступен", ""]
            out.append(f"  💵 Оценка портфеля: ~{total_usdt:,.0f} USDT")
            out.append("")
            return out
        except Exception as exc:
            logger.warning("OKX balance fetch failed: %s", exc)
            return [f"*🏦 OKX demo:* ошибка получения ({type(exc).__name__})", ""]

    # --------------------------------------------------------- /настройки
    async def _cmd_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self._deny(update, admin_only=True):
            return

        ts = get_training_state()
        args = context.args or []

        # /настройки отчёт 08:30
        if args and args[0].lower() in {"отчёт", "отчет", "report"}:
            if len(args) < 2:
                await self._reply(update, "Укажите время, например: /настройки отчёт 08:30")
                return
            try:
                value = ts.set_daily_report_time(args[1])
                await self._reply(update, f"⏰ Время ежедневного отчёта: *{value} МСК*")
            except ValueError as exc:
                await self._reply(update, f"❌ {exc}")
            return

        # /настройки алерты вкл|выкл
        if args and args[0].lower() in {"алерты", "alerts"}:
            if len(args) < 2:
                await self._reply(update, "Укажите вкл/выкл, например: /настройки алерты вкл")
                return
            val = args[1].lower() in {"вкл", "on", "1", "да", "true"}
            ts.set_alerts(val)
            await self._reply(update, f"🔔 Алерты: {'ВКЛ' if val else 'ВЫКЛ'}")
            return

        # /настройки тишина 23:00 08:00  (или off)
        if args and args[0].lower() in {"тишина", "тихие", "quiet"}:
            if len(args) >= 2 and args[1].lower() in {"off", "выкл", "нет"}:
                ts.set_quiet_hours(None, None)
                await self._reply(update, "🌙 Тихие часы отключены")
                return
            if len(args) < 3:
                await self._reply(
                    update,
                    "Укажите интервал, например: /настройки тишина 23:00 08:00 "
                    "(или «/настройки тишина выкл»)",
                )
                return
            try:
                s, e = ts.set_quiet_hours(args[1], args[2])
                await self._reply(update, f"🌙 Тихие часы: *{s}–{e} МСК*")
            except ValueError as exc:
                await self._reply(update, f"❌ {exc}")
            return

        # Без аргументов — показать текущие настройки и кнопки.
        await update.message.reply_text(
            self._settings_text(ts),
            parse_mode="Markdown",
            reply_markup=settings_keyboard(ts),
        )

    def _settings_text(self, ts) -> str:
        q = "отключены" if not ts.quiet_hours_start else f"{ts.quiet_hours_start}–{ts.quiet_hours_end} МСК"
        in_quiet = " (сейчас тихие часы)" if ts.in_quiet_hours() else ""
        return (
            "⏰ *НАСТРОЙКИ ОПОВЕЩЕНИЙ*\n\n"
            f"🔔 Алерты о сделках: {'ВКЛ' if ts.alerts_enabled else 'ВЫКЛ'}\n"
            f"📨 Ежедневный отчёт: *{ts.daily_report_time} МСК*\n"
            f"🌙 Тихие часы: {q}{in_quiet}\n\n"
            "*Команды:*\n"
            "• `/настройки отчёт 08:30` — время отчёта\n"
            "• `/настройки алерты вкл|выкл`\n"
            "• `/настройки тишина 23:00 08:00` — тихие часы\n"
            "• `/настройки тишина выкл` — отключить тихие часы\n"
        )

    async def _cb_settings(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        uid = query.from_user.id
        if not self._is_admin(uid):
            await query.edit_message_text("❌ Только для администратора")
            return
        ts = get_training_state()
        action = query.data.split(":", 1)[1]
        if action == "toggle_alerts":
            ts.set_alerts(not ts.alerts_enabled)
            await query.edit_message_text(
                self._settings_text(ts), parse_mode="Markdown",
                reply_markup=settings_keyboard(ts),
            )
        elif action == "help" or action.endswith("_help"):
            await query.message.reply_text(
                "Менять время/алерты удобнее всего командами:\n"
                "`/настройки отчёт 08:30`\n"
                "`/настройки алерты вкл`\n"
                "`/настройки тишина 23:00 08:00`"
            )

    # --------------------------------------------------------------- /статус
    async def _cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self._deny(update):
            return

        state = get_system_state()
        risk = get_risk_engine()
        paper = get_paper_engine()

        mode = "📄 Демо" if self.account_mode == "paper" else "💵 Реальный"
        running = paper and paper.is_running
        training = self._train_task is not None and not self._train_task.done()

        text = (
            "📊 *СТАТУС*\n\n"
            f"*Счёт:* {mode}\n"
            f"*Торговля:* {'🟢 Работает' if running else '⏸️ Остановлена'}\n"
            f"*Обучение:* {'🎓 Идёт' if training else '💤 Остановлено'}\n"
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
        if await self._deny(update):
            return

        state = get_system_state()
        risk = get_risk_engine()

        total_pnl = state.total_net_pnl
        total_pct = state.total_pnl_pct

        text = (
            f"📈 *ОТЧЁТ НА {datetime.utcnow().strftime('%d.%m.%Y')}*\n\n"
            "*💰 Капитал*\n"
            f"  Текущий: {self._fmt_money(state.current_equity)}\n"
            f"  Начальный: {self._fmt_money(state.initial_capital)}\n"
            f"  {self._pnl_icon(total_pnl)} Прибыль: {self._fmt_money(total_pnl)} "
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
        if await self._deny(update):
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
        if await self._deny(update):
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
        if await self._deny(update):
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

    # -------------------------------------------------------- /расписание
    async def _cmd_schedule(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self._deny(update):
            return
        from ..core import trading_schedule
        args = context.args or []
        if args and args[0].isdigit() and self._is_admin(update.effective_user.id):
            # /расписание 700 — сменить месячный бюджет часов.
            trading_schedule.set_budget(float(args[0]))
        st = trading_schedule.get_status()
        icon = "🟢" if st["can_trade_now"] else "🌙"
        text = (
            "⏰ *БЮДЖЕТ ТОРГОВЫХ ЧАСОВ*\n\n"
            f"{icon} Сейчас торговля: "
            f"{'разрешена' if st['can_trade_now'] else 'на паузе'}\n"
            f"Месяц: {st['month']} ({st['days_in_month']} дн.)\n"
            f"Бюджет: {st['budget_hours']:.0f} ч/мес\n"
            f"Использовано: {st['used_hours']:.1f} ч\n"
            f"Осталось: {st['remaining_hours']:.1f} ч\n"
            f"В сутки: {st['hours_per_day']:.1f} ч\n"
            f"Осталось сегодня: {st['daily_remaining_minutes']:.0f} мин\n"
            f"Активные часы: {st['active_hours_msk']} МСК\n\n"
            "Сменить бюджет (админ): `/расписание 700`"
        )
        await self._reply(update, text, reply_markup=MAIN_MENU)

    # -------------------------------------------------------- /готовность
    async def _cmd_ready(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self._deny(update):
            return
        try:
            from ..core import readiness
            text = readiness.format_report()
        except Exception as exc:
            text = f"❌ Не смог оценить готовность: {exc}"
        await self._reply(update, text, reply_markup=MAIN_MENU)

    # ---------------------------------------------------------- /счёт
    async def _cmd_account(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self._deny(update):
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
                    "⚠️ *Внимение!*\n\n"
                    "Вы пытаетесь включить *реальную торговлю*. "
                    "Это операции с живыми деньгами. Убедитесь, что:\n"
                    "• API-ключи без прав на вывод;\n"
                    "• риск-параметры проверены;\n"
                    "• стратегии прошли 30 дней демо-теста.\n\n"
                    "Для подтверждения нажмите кнопку ниже.",
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

    # ----------------------------------------------------------- /пауза
    async def _cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self._deny(update, admin_only=True):
            return
        from ..core.state import TradingState
        state = get_system_state()
        state.trading_state = TradingState.PAUSED
        await self._reply(update, "⏸️ Торговля приостановлена")
        logger.info("Trading paused by user %s", update.effective_user.id)

    async def _cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self._deny(update, admin_only=True):
            return
        from ..core.state import TradingState
        state = get_system_state()
        state.trading_state = TradingState.RUNNING
        await self._reply(update, "▶️ Торговля возобновлена")
        logger.info("Trading resumed by user %s", update.effective_user.id)

    # ----------------------------------------------------------- /помощь
    async def _cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if await self._deny(update):
            return
        text = (
            "❓ *Помощь — ASTRA BOT*\n\n"
            "*Обучение:*\n"
            "/обучение — запустить self-play + переобучение (капитал "
            "растёт/падает от результата)\n"
            "/обучение --offline [--bars=3000] — офлайн на синтетике\n"
            "/стоп — прекратить обучение\n\n"
            "*Деньги:*\n"
            "/баланс — общий капитал, плюсы, минусы (обучение + бумажный "
            "счёт + OKX demo)\n"
            "/позиции — открытые сделки\n\n"
            "*Оповещения:*\n"
            "/настройки — текущие настройки и кнопки\n"
            "/настройки отчёт 08:30 — время ежедневного отчёта (МСК)\n"
            "/настройки алерты вкл|выкл\n"
            "/настройки тишина 23:00 08:00 — тихие часы\n\n"
            "*Система:*\n"
            "/статус /отчёт /риск /здоровье /счёт\n"
            "/пауза /возобновить (админ)\n"
        )
        await self._reply(update, text, reply_markup=MAIN_MENU)

    # --------------------------------------------------------------- /text
    async def _handle_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if not self._is_allowed(user_id):
            return
        raw = (update.message.text or "").strip()
        text = raw

        # Русские «команды» вида /обучение Telegram отдаёт как обычный
        # текст (кириллица не валидна для BotCommand). Маршрутизируем их
        # вручную на латинские команды.
        if raw.startswith("/"):
            token = raw[1:].split("@", 1)[0].split()[0].lower()
            token = RUSSIAN_ALIASES.get(token, token)
            method_name = COMMAND_HANDLERS.get(token)
            if method_name is not None:
                # Прокидываем аргументы после команды.
                args = raw.split()[1:]
                context.args = args
                await getattr(self, method_name)(update, context)
                return

        dispatch = {
            "🎓 Обучение": self._cmd_train,
            "⏹ Стоп": self._cmd_stop_training,
            "💰 Баланс": self._cmd_balance,
            "⏰ Настройки": self._cmd_settings,
            "📊 Статус": self._cmd_status,
            "📈 Отчёт": self._cmd_report,
            "📍 Позиции": self._cmd_positions,
            "🛡️ Риск": self._cmd_risk,
            "🏥 Здоровье": self._cmd_health,
            "⏰ Расписание": self._cmd_schedule,
            "🎯 Готовность": self._cmd_ready,
            "⚙️ Счёт": self._cmd_account,
            "❓ Помощь": self._cmd_help,
        }
        handler = dispatch.get(text)
        if handler:
            await handler(update, context)
        elif text.lower() in {"test", "тест"}:
            await update.message.reply_text("✅ Бот на связи!")

    # --------------------------------------------------------------- alerts
    async def send_alert(self, message: str, severity: str = "info"):
        if not self._application:
            return
        ts = get_training_state()
        # Тихие часы не блокируют критичные алерты и утренний отчёт.
        is_critical = severity in {"error", "critical"}
        if not ts.alerts_enabled and not is_critical:
            return
        if ts.in_quiet_hours() and not is_critical:
            logger.info("Алерты подавлены тихими часами: %s", severity)
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

    async def _send_to_admins(
        self, message: str, severity: str = "info", force: bool = False
    ):
        """Отправить сообщение всем админам (с учётом тихих часов)."""
        if not self._bot:
            return
        ts = get_training_state()
        if not force and not ts.alerts_enabled:
            return
        if not force and ts.in_quiet_hours() and severity not in {"error", "critical"}:
            return
        emoji = {
            "info": "ℹ️", "warning": "⚠️", "error": "❌", "critical": "🚨",
        }.get(severity, "📢")
        for admin_id in self.admin_user_ids:
            try:
                await self._bot.send_message(
                    chat_id=admin_id, text=f"{emoji} {message}",
                    parse_mode="Markdown",
                )
            except Exception as exc:
                logger.error("Не отправил сообщение %s: %s", admin_id, exc)

    async def send_daily_report(self, report_text: str):
        # Утренний отчёт отправляется всегда, независимо от тихих часов.
        if not self._application or not self._bot:
            return
        for admin_id in self.admin_user_ids:
            try:
                await self._bot.send_message(
                    chat_id=admin_id, text=report_text, parse_mode="Markdown"
                )
            except Exception as exc:
                logger.error("Не отправил отчёт %s: %s", admin_id, exc)

    # --------------------------------------------------------------- lifecycle
    async def start(self, webhook_url: str | None = None):
        """Запустить бота.

        Если задан ``webhook_url`` — поднимаемся в режиме webhook (Telegram
        сам присылает обновления на наш HTTP-эндпоинт). Это надёжно на
        спящем free-хостинге вроде Render, где long-polling рвётся при
        засыпании сервиса. Без webhook — обычный long-polling.
        """
        if not self._application:
            await self.initialize()
        self._running = True
        await self._application.initialize()
        await self._application.start()

        if webhook_url:
            self._webhook_mode = True
            await self._bot.set_webhook(webhook_url, allowed_updates=["message", "callback_query"])
            logger.info("Telegram webhook set: %s", webhook_url)
        else:
            self._webhook_mode = False
            await self._application.updater.start_polling()

        # Регистрируем русские команды в меню Telegram.
        try:
            await self.set_bot_commands()
        except Exception as exc:
            logger.warning("Не смог зарегистрировать команды меню: %s", exc)

        # Стартовое сообщение — не чаще одного раза в сутки, иначе
        # частые перезапуски (каждые 5 мин на GitHub Actions) спамили бы.
        asyncio.ensure_future(self._maybe_startup_message())
        logger.info("Telegram bot started (webhook=%s)", bool(webhook_url))

    async def _maybe_startup_message(self) -> None:
        """Прислать «бот на связи» только раз в сутки (храним дату в состоянии)."""
        try:
            from datetime import datetime as _dt, timezone as _tz
            ts = get_training_state()
            today = _dt.now(_tz.utc).strftime("%Y-%m-%d")
            # Поле last_startup_message держим прямо в training_state.
            if getattr(ts, "last_startup_message", None) == today:
                return
            await self._send_to_admins(
                "🤖 *ASTRA BOT на связи*\n\n"
                "Режим работы: 08:00–24:00 МСК. Я присылаю только:\n"
                "• утренний отчёт в 09:00 МСК;\n"
                "• уведомление, когда буду готов к реальному счёту.\n"
                "По сделкам не пишу — смотрите /баланс или /отчёт.",
                force=True,
            )
            ts.last_startup_message = today
            ts.save()
        except Exception as exc:  # noqa: BLE001
            logger.debug("startup message failed: %s", exc)

    async def process_update(self, update_json: dict) -> None:
        """Обработать входящее обновление Telegram (для webhook-режима)."""
        if not self._application:
            return
        await self._application.process_update(Update.de_json(update_json, self._bot))

    async def _startup_message(self) -> None:
        try:
            await asyncio.sleep(1.5)
            await self.send_alert(
                "🤖 *ASTRA BOT на связи*\n\n"
                "Меню команд доступно слева от поля ввода. Я буду присылать:\n"
                "• открытие/закрытие сделок на демо OKX;\n"
                "• утренний отчёт в 09:00 МСК;\n"
                "• уведомление, когда буду готов к реальному счёту.\n\n"
                "Нажмите 💰 Баланс или 📊 Статус для проверки.",
                severity="info",
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("startup message failed: %s", exc)

    async def stop(self):
        self._running = False
        if self._train_task is not None and not self._train_task.done():
            self._train_task.cancel()
        if self._application:
            try:
                if self._webhook_mode:
                    await self._bot.delete_webhook(drop_pending_updates=False)
                else:
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
