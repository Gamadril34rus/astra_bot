#!/usr/bin/env python3
"""Короткие непрерывные paper-сессии ASTRA на GitHub Actions."""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass
from astra_bot.adapters.bingx import BingXClient
from astra_bot.core import trading_schedule
from astra_bot.core.instruments import TRADING_UNIVERSE, to_bingx
from astra_bot.core.logger import setup_logging
from astra_bot.decision.trading_engine import TradingEngine, TradingEngineConfig
from astra_bot.telegram.bot import create_telegram_bot
from astra_bot.utils.retry import retry_async

# Block 1.5: Error logging to logs/errors.log
import traceback
def _log_error_to_file(exc: Exception, context: str = ""):
    try:
        log_dir = Path("logs")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "errors.log"
        import time
        ts = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\n[{ts}] {context}: {exc}\n")
            f.write(traceback.format_exc())
            f.write(f"\n{'='*60}\n")
    except Exception:
        pass

setup_logging(level=os.environ.get("LOG_LEVEL", "INFO"))
logger = logging.getLogger("bot_runner")
LISTEN_SECONDS = int(os.environ.get("BOT_LISTEN_SECONDS", "200"))

def _rotate_state() -> None:
    """Прокачка торговых state-файлов в начале сессии (TZ §29):
    Git не должен хранить бесконечную торговую БД."""
    try:
        from astra_bot.core.state_rotation import rotate_all
        rotate_all(PROJECT_ROOT / "models")
    except Exception as exc:
        logger.warning("State rotation error (не блокирует сессию): %s", exc)


async def amain() -> int:
    _rotate_state()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    admin = os.environ.get("TELEGRAM_ADMIN_ID", "")
    if not token or not admin:
        logger.error("TELEGRAM_BOT_TOKEN / TELEGRAM_ADMIN_ID не заданы")
        return 2
    admin_ids = [int(x) for x in admin.split(",") if x.strip()]
    allowed = [int(x) for x in os.environ.get("TELEGRAM_USER_ID", str(admin)).split(",") if x.strip()] or admin_ids
    status = trading_schedule.get_status()
    can_trade = status["can_trade_now"]
    logger.info("Старт: торговля %s | осталось %s ч/мес | %s", "разрешена" if can_trade else "на паузе", status["remaining_hours"], status["now_msk"])
    bingx = BingXClient({
        "api_key": os.environ.get("BINGX_API_KEY", ""),
        "api_secret": os.environ.get("BINGX_API_SECRET", ""),
        "enabled": True,
        "rate_limit_qps": 5,
    })
    try:
        await bingx.initialize()
    except Exception as exc:
        logger.warning("BingX init failed (продолжаю в офлайн-режиме): %s", exc)

    candidates = tuple(to_bingx(s) for s in TRADING_UNIVERSE)
    symbols = candidates  # fallback по умолчанию
    try:
        available = await bingx.get_instruments()
        if available:
            available_ids = {i.symbol for i in available if getattr(i, "trading_status", "") == "trading"}
            filtered = tuple(s for s in candidates if s in available_ids)
            skipped = tuple(s for s in candidates if s not in available_ids)
            logger.info("BingX spot universe: %d/%d instruments available", len(filtered), len(candidates))
            if skipped:
                logger.warning("Skipped unavailable instruments: %s", ", ".join(skipped))
            if filtered:
                symbols = filtered
            else:
                logger.warning("BingX вернул 0 торгуемых из нашего списка — fallback на статический универсум %d", len(candidates))
                symbols = candidates
        else:
            logger.warning("BingX get_instruments вернул пусто — fallback на статический универсум %d", len(candidates))
            symbols = candidates
    except Exception as exc:
        logger.warning("BingX get_instruments failed, fallback на статический универсум: %s", exc)
        symbols = candidates

    if not symbols:
        logger.error("No configured instruments — сессия без торговли, но без ошибки CI")
        symbols = candidates
        if not symbols:
            try:
                await bingx.close()
            except Exception:
                pass
            return 0

    engine = TradingEngine(exchange=bingx, config=TradingEngineConfig(symbols=symbols, poll_interval_seconds=300))
    bot = await create_telegram_bot(bot_token=token, allowed_user_ids=allowed, admin_user_ids=admin_ids)
    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass
    try:
        await bot.start()
    except Exception as exc:
        logger.warning("Telegram bot start failed (продолжаю без Telegram): %s", exc)

    async def trade_loop():
        while not stop.is_set():
            try:
                if trading_schedule.can_trade_now():
                    trading_schedule.tick()
                    await engine.step()
                else:
                    logger.info("Вне торгового расписания — шаг пропущен")
            except Exception as exc:
                logger.exception("Ошибка торгового шага: %s", exc)
                _log_error_to_file(exc, "trade_loop")
            try:
                await asyncio.wait_for(stop.wait(), timeout=45)
            except TimeoutError:
                pass

    trade_task = asyncio.create_task(trade_loop())
    try:
        await asyncio.wait_for(stop.wait(), timeout=LISTEN_SECONDS)
    except TimeoutError:
        pass
    finally:
        stop.set()
        trade_task.cancel()
        try:
            await trade_task
        except (asyncio.CancelledError, Exception):
            pass
        try:
            await bot.stop()
        except Exception as exc:
            logger.debug("Telegram stop error: %s", exc)
        try:
            await bingx.close()
        except Exception:
            pass
    logger.info("Сессия завершена.")
    return 0

def main() -> None:
    import json as _json
    import urllib.error as _ue
    import urllib.request as _ur
    _repo = os.environ.get("GITHUB_REPOSITORY")
    _api_url = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    _token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    _trigger_path = "TRIGGER_AUDIT.txt"
    if _repo and _token and not os.environ.get("ASTRABOT_SKIP_DISPATCH"):
        try:
            _headers = {"Authorization": f"Bearer {_token}",
                        "Accept": "application/vnd.github+json",
                        "User-Agent": "astra-bot",
                        "X-GitHub-Api-Version": "2022-11-28"}
            _url = f"{_api_url}/repos/{_repo}/contents/{_trigger_path}?ref=master"
            try:
                with _ur.urlopen(_ur.Request(_url, headers=_headers), timeout=10) as _r:
                    _meta = _json.loads(_r.read().decode())
                _sha = _meta.get("sha")
                import logging as _log
                _log.getLogger("bot_runner").info(
                    "Флаг %s найден (sha=%s) — триггерю full-research-audit и удаляю флаг",
                    _trigger_path, _sha)
                _wf_url = f"{_api_url}/repos/{_repo}/actions/workflows/full-research-audit.yml/dispatches"
                _req = _ur.Request(_wf_url, data=_json.dumps({"ref": "master"}).encode(),
                                   headers={**_headers, "Content-Type": "application/json"},
                                   method="POST")
                try:
                    with _ur.urlopen(_req, timeout=10) as _r:
                        _r.read()
                    _log.getLogger("bot_runner").info("full-research-audit workflow_dispatch отправлен")
                except _ue.HTTPError as _e:
                    _log.getLogger("bot_runner").warning(
                        "Не удалось триггернуть аудит (HTTP %s): %s",
                        _e.code, _e.read().decode()[:300])
                if _sha:
                    _del_url = f"{_api_url}/repos/{_repo}/contents/{_trigger_path}"
                    _del_body = _json.dumps({
                        "message": "chore: consume one-shot audit trigger",
                        "sha": _sha, "branch": "master",
                    }).encode()
                    _del_req = _ur.Request(_del_url, data=_del_body,
                                          headers={**_headers, "Content-Type": "application/json"},
                                          method="DELETE")
                    try:
                        with _ur.urlopen(_del_req, timeout=10) as _r:
                            _r.read()
                        _log.getLogger("bot_runner").info("Флаг %s удалён", _trigger_path)
                    except _ue.HTTPError as _e:
                        _log.getLogger("bot_runner").warning(
                            "Не удалось удалить флаг (HTTP %s): %s",
                            _e.code, _e.read().decode()[:300])
            except _ue.HTTPError as _e:
                if _e.code != 404:
                    _log.getLogger("bot_runner").debug("Проверка флага %s: HTTP %s", _trigger_path, _e.code)
            except Exception as _e:
                _log.getLogger("bot_runner").debug("Ошибка проверки триггера: %r", _e)
        except Exception as _exc:
            logging.getLogger("bot_runner").debug("dispatch-логика пропущена: %r", _exc)

    try:
        code = asyncio.run(amain())
    except KeyboardInterrupt:
        code = 0
    except Exception as exc:
        logger.exception("Критическая ошибка сессии: %s", exc)
        try:
            _log_error_to_file(exc, "main")
        except Exception:
            pass
        code = 0
    sys.exit(code or 0)

if __name__ == "__main__":
    main()
