"""Expected value engine."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EVReport:
    p_win: float
    avg_win_pct: float
    avg_loss_pct: float
    fees_pct: float
    slippage_pct: float
    edge_pct: float
    is_positive: bool

    def to_dict(self) -> dict:
        return self.__dict__


class EVEngine:
    def __init__(self, min_edge_pct: float = 0.4):
        self.min_edge_pct = min_edge_pct

    def calculate(
        self,
        *,
        p_win: float,
        entry: float,
        stop: float,
        take: float,
        fees_pct: float = 0.05,
        slippage_pct: float = 0.05,
    ) -> EVReport:
        if entry == 0:
            return EVReport(p_win, 0, 0, fees_pct, slippage_pct, -1, False)
        avg_win = abs(take - entry) / entry * 100
        avg_loss = abs(entry - stop) / entry * 100
        p_loss = 1 - p_win
        edge = (
            p_win * avg_win
            - p_loss * avg_loss
            - fees_pct
            - slippage_pct
        )
        return EVReport(
            p_win=p_win,
            avg_win_pct=avg_win,
            avg_loss_pct=avg_loss,
            fees_pct=fees_pct,
            slippage_pct=slippage_pct,
            edge_pct=edge,
            is_positive=edge >= self.min_edge_pct,
        )
