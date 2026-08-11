"""News engine — заглушка с интерфейсом.

Подключается к новостному источнику (RSS/API) и возвращает
NEWS RISK SCORE 0..100. Если данных нет, возвращает безопасный 0
(не блокирует торговлю), но не инициирует сделок сам.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class NewsReport:
    score: int = 0
    critical: bool = False
    headline: str = ""
    blocked: bool = False

    def to_dict(self) -> dict:
        return self.__dict__


class NewsEngine:
    """Интерфейс новостного движка.

    Продакшн-реализация должна наследоваться и переопределять
    ``fetch``, подключая источник новостей и календарь событий.
    """

    def assess(
        self,
        symbol: str,
        *,
        upcoming_events: list[dict] | None = None,
        headlines: list[str] | None = None,
    ) -> NewsReport:
        score = 0
        upcoming_events = upcoming_events or []
        headlines = headlines or []
        # Простая эвристика до подключения реальных источников.
        for ev in upcoming_events:
            impact = str(ev.get("impact", "")).lower()
            if impact == "high":
                score += 40
            elif impact == "medium":
                score += 15
        if any("hack" in h.lower() or "sec" in h.lower() for h in headlines):
            score += 30
        score = min(100, score)
        return NewsReport(
            score=score,
            critical=score >= 75,
            blocked=score >= 75,
        )
