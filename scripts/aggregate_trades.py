"""
P1-1: Aggregate partial exits into single position records.

TZ P1-1: сделка = позиция от открытия до полного закрытия;
tp1/tp2/tp3 — события partial_exits внутри записи с weighted_exit_price.
Win rate и «≥200 сделок» считаются по агрегированным сделкам.

Usage:
    python scripts/aggregate_trades.py [--input models/paper_trades.jsonl]
                                        [--output models/paper_positions_agg.jsonl]
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AggregatedTrade:
    """Агрегированная сделка: одна позиция от открытия до полного закрытия."""
    id: str  # position id
    symbol: str
    direction: str
    entry_price: float
    # Взвешенная средняя цена выхода (по объёму частичных тейков).
    weighted_exit_price: float = 0.0
    # Суммарный объём (initial quantity).
    total_quantity: float = 0.0
    # Суммарный PnL (все частичные выходы + финальное закрытие).
    total_pnl: float = 0.0
    # Суммарные комиссии.
    total_fees: float = 0.0
    # Процент доходности (от entry notional).
    total_pnl_pct: float = 0.0
    # Список частичных выходов.
    partial_exits: list[dict[str, Any]] = field(default_factory=list)
    # Финальная причина выхода (последняя).
    final_exit_reason: str = ""
    # Все причины выхода.
    exit_reasons: list[str] = field(default_factory=list)
    strategy: str = ""
    opened_at: int = 0
    closed_at: int = 0  # время последней закрытия
    # R-метрики (если доступны).
    r_multiple: float = 0.0
    mfe_r: float = 0.0
    mae_r: float = 0.0
    # Контекст (если доступен).
    regime: str = ""
    timeframe: str = ""
    regime_axes: str = ""

    @property
    def num_exits(self) -> int:
        return len(self.partial_exits)

    @property
    def is_win(self) -> bool:
        return self.total_pnl > 0


def aggregate_trades(input_path: Path, output_path: Path | None = None) -> list[AggregatedTrade]:
    """Прочитать paper_trades.jsonl и агрегировать по position id.

    Каждая уникальная позиция (по id) становится одной AggregatedTrade.
    Частичные тейки (tp1/tp2/tp3) объединяются с weighted_exit_price.
    """
    # Читаем все сделки
    records: list[dict] = []
    with input_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))

    # Группируем по id позиции
    groups: dict[str, list[dict]] = defaultdict(list)
    for rec in records:
        groups[rec["id"]].append(rec)

    # Агрегируем
    aggregated: list[AggregatedTrade] = []
    for pid, trades in groups.items():
        # Сортируем по времени закрытия
        trades.sort(key=lambda t: t.get("closed_at", 0))

        first = trades[0]
        total_qty = sum(t.get("quantity", 0) for t in trades)
        total_pnl = sum(t.get("pnl", 0) for t in trades)
        total_fees = sum(t.get("fees", 0) for t in trades)

        # Weighted exit price
        if total_qty > 0:
            weighted_exit = sum(
                t.get("exit_price", 0) * t.get("quantity", 0) for t in trades
            ) / total_qty
        else:
            weighted_exit = first.get("exit_price", 0)

        # Entry notional for pnl_pct
        entry_notional = first.get("entry_price", 0) * total_qty
        pnl_pct = (total_pnl / entry_notional * 100) if entry_notional > 0 else 0

        # Partial exits
        partial_exits = []
        exit_reasons = []
        for t in trades:
            reason = t.get("exit_reason", "")
            exit_reasons.append(reason)
            partial_exits.append({
                "exit_price": t.get("exit_price", 0),
                "quantity": t.get("quantity", 0),
                "pnl": t.get("pnl", 0),
                "reason": reason,
                "closed_at": t.get("closed_at", 0),
            })

        # R-метрики: берём из последней записи (или средневзвешенные)
        r_multiple = trades[-1].get("r_multiple", 0.0)
        mfe_r = max((t.get("mfe_r", 0.0) for t in trades), default=0.0)
        mae_r = min((t.get("mae_r", 0.0) for t in trades), default=0.0)

        agg = AggregatedTrade(
            id=pid,
            symbol=first.get("symbol", ""),
            direction=first.get("direction", ""),
            entry_price=first.get("entry_price", 0),
            weighted_exit_price=weighted_exit,
            total_quantity=total_qty,
            total_pnl=total_pnl,
            total_fees=total_fees,
            total_pnl_pct=pnl_pct,
            partial_exits=partial_exits,
            final_exit_reason=trades[-1].get("exit_reason", ""),
            exit_reasons=exit_reasons,
            strategy=first.get("strategy", ""),
            opened_at=first.get("opened_at", 0),
            closed_at=trades[-1].get("closed_at", 0),
            r_multiple=r_multiple,
            mfe_r=mfe_r,
            mae_r=mae_r,
            regime=first.get("regime", ""),
            timeframe=first.get("timeframe", ""),
            regime_axes=first.get("regime_axes", ""),
        )
        aggregated.append(agg)

    # Сортируем по времени открытия
    aggregated.sort(key=lambda a: a.opened_at)

    # Записываем результат
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as f:
            for agg in aggregated:
                f.write(json.dumps(asdict(agg), ensure_ascii=False) + "\n")

    return aggregated


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Aggregate paper trades (P1-1)")
    parser.add_argument("--input", default="models/paper_trades.jsonl")
    parser.add_argument("--output", default="models/paper_positions_agg.jsonl")
    args = parser.parse_args()

    result = aggregate_trades(Path(args.input), Path(args.output))
    print(f"Aggregated {sum(a.num_exits for a in result)} trade records → {len(result)} positions")
    print(f"Written to {args.output}")

    # Summary stats
    wins = sum(1 for a in result if a.is_win)
    losses = len(result) - wins
    total_pnl = sum(a.total_pnl for a in result)
    print(f"Wins: {wins}, Losses: {losses}, WR: {wins/len(result)*100:.1f}%")
    print(f"Total PnL: {total_pnl:.2f} USDT")


if __name__ == "__main__":
    main()
