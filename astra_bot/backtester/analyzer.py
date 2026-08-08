"""
ASTRA BOT — Backtest Analyzer
Анализ результатов бэктеста
"""

import logging
from typing import Dict, List, Optional
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .engine import BacktestResult, Trade, DailyStats

logger = logging.getLogger(__name__)


@dataclass
class PerformanceMetrics:
    """Метрики производительности"""
    # Общие
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    total_pnl: float
    net_profit: float
    profit_factor: float
    
    # Средние
    avg_win: float
    avg_loss: float
    avg_win_pct: float
    avg_loss_pct: float
    
    # Экстремумы
    largest_win: float
    largest_loss: float
    max_drawdown: float
    max_drawdown_pct: float
    
    # Риск-адаптированные
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    expectancy: float
    
    # Equity
    initial_equity: float
    final_equity: float
    total_return: float
    total_return_pct: float
    
    # Trade duration
    avg_hold_time: float = 0.0
    max_hold_time: float = 0.0
    min_hold_time: float = 0.0
    
    @property
    def is_profitable(self) -> bool:
        return self.net_profit > 0
    
    @property
    def risk_adjusted_return(self) -> float:
        """Return per unit of risk (max drawdown)"""
        if self.max_drawdown > 0:
            return self.total_return_pct / self.max_drawdown
        return 0
    
    def to_dict(self) -> dict:
        return {
            "total_trades": self.total_trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": f"{self.win_rate:.2f}%",
            "total_pnl": f"{self.total_pnl:.2f}",
            "net_profit": f"{self.net_profit:.2f}",
            "profit_factor": f"{self.profit_factor:.2f}",
            "avg_win": f"{self.avg_win:.2f}",
            "avg_loss": f"{self.avg_loss:.2f}",
            "avg_win_pct": f"{self.avg_win_pct:.2f}%",
            "avg_loss_pct": f"{self.avg_loss_pct:.2f}%",
            "largest_win": f"{self.largest_win:.2f}",
            "largest_loss": f"{self.largest_loss:.2f}",
            "max_drawdown": f"{self.max_drawdown:.2f}",
            "max_drawdown_pct": f"{self.max_drawdown_pct:.2f}%",
            "sharpe_ratio": f"{self.sharpe_ratio:.2f}",
            "sortino_ratio": f"{self.sortino_ratio:.2f}",
            "calmar_ratio": f"{self.calmar_ratio:.2f}",
            "expectancy": f"{self.expectancy:.4f}",
            "initial_equity": f"{self.initial_equity:.2f}",
            "final_equity": f"{self.final_equity:.2f}",
            "total_return": f"{self.total_return:.2f}",
            "total_return_pct": f"{self.total_return_pct:.2f}%",
            "risk_adjusted_return": f"{self.risk_adjusted_return:.2f}",
        }
    
    def to_dataframe(self) -> pd.DataFrame:
        """Конвертировать в DataFrame"""
        return pd.DataFrame([self.to_dict()])
    
    @classmethod
    def from_backtest_result(cls, result: BacktestResult) -> "PerformanceMetrics":
        """Создать из результатов бэктеста"""
        trades = result.trades
        
        # Фильтрация закрытых сделок
        closed_trades = [t for t in trades if t.result in ["won", "lost"]]
        
        total_trades = len(closed_trades)
        wins = sum(1 for t in closed_trades if t.result == "won")
        losses = sum(1 for t in closed_trades if t.result == "lost")
        
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        
        total_pnl = sum(t.pnl for t in closed_trades)
        total_fees = result.total_fees + result.total_slippage
        net_profit = float(total_pnl - total_fees)
        
        # Profit Factor
        gross_profit = sum(t.pnl for t in closed_trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in closed_trades if t.pnl < 0))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0
        
        # Average win/loss
        avg_win = (sum(t.pnl for t in closed_trades if t.pnl > 0) / wins) if wins > 0 else 0
        avg_loss = (sum(t.pnl for t in closed_trades if t.pnl < 0) / losses) if losses > 0 else 0
        
        # Percentage
        avg_win_pct = (sum(t.pnl_pct for t in closed_trades if t.pnl_pct > 0) / wins) if wins > 0 else 0
        avg_loss_pct = (sum(t.pnl_pct for t in closed_trades if t.pnl_pct < 0) / losses) if losses > 0 else 0
        
        # Largest
        largest_win = max((t.pnl for t in closed_trades if t.pnl > 0), default=0)
        largest_loss = min((t.pnl for t in closed_trades if t.pnl < 0), default=0)
        
        # Return
        total_return = float(result.final_equity - result.config.initial_capital) if result.config.initial_capital else 0
        total_return_pct = (total_return / float(result.config.initial_capital) * 100) if result.config.initial_capital > 0 else 0
        
        # Hold times
        hold_times = []
        for t in closed_trades:
            if hasattr(t, 'exit_time') and hasattr(t, 'entry_time'):
                if t.exit_time and t.entry_time:
                    hold_time = (t.exit_time - t.entry_time).total_seconds() / 3600  # hours
                    hold_times.append(hold_time)
        
        avg_hold = float(np.mean(hold_times)) if hold_times else 0.0
        max_hold = float(max(hold_times)) if hold_times else 0.0
        min_hold = float(min(hold_times)) if hold_times else 0.0
        
        # Sharpe Ratio (из equity curve)
        equity_values = [float(e["equity"]) for e in result.equity_curve]
        if len(equity_values) > 1:
            returns = np.diff(equity_values) / equity_values[:-1]
            daily_returns = returns
            if np.std(daily_returns) > 0:
                sharpe = np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252)
                sortino = np.mean(daily_returns) / np.std(daily_returns[daily_returns < 0]) * np.sqrt(252) if np.std(daily_returns[daily_returns < 0]) > 0 else 0
            else:
                sharpe = 0
                sortino = 0
        else:
            sharpe = 0
            sortino = 0
        
        # Calmar Ratio
        max_dd_pct = float(result.max_drawdown_pct) if result.max_drawdown_pct else 0
        calmar = (total_return_pct / max_dd_pct) if max_dd_pct > 0 else 0
        
        # Expectancy
        expectancy = (net_profit / total_trades) if total_trades > 0 else 0
        
        return cls(
            total_trades=total_trades,
            wins=wins,
            losses=losses,
            win_rate=win_rate,
            total_pnl=float(total_pnl),
            net_profit=net_profit,
            profit_factor=profit_factor,
            avg_win=float(avg_win),
            avg_loss=float(avg_loss),
            avg_win_pct=float(avg_win_pct),
            avg_loss_pct=float(avg_loss_pct),
            largest_win=float(largest_win),
            largest_loss=float(largest_loss),
            max_drawdown=float(result.max_drawdown),
            max_drawdown_pct=float(result.max_drawdown_pct),
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            expectancy=expectancy,
            initial_equity=float(result.config.initial_capital),
            final_equity=float(result.final_equity),
            total_return=float(total_return),
            total_return_pct=float(total_return_pct),
            avg_hold_time=avg_hold,
            max_hold_time=max_hold,
            min_hold_time=min_hold,
        )


class BacktestAnalyzer:
    """
    Анализатор результатов бэктеста.
    
    Предоставляет:
    - Подробную статистику
    - Анализ по времени
    - Анализ по стратегиям
    - Визуализацию (текстовую)
    """
    
    def __init__(self, result: BacktestResult):
        self.result = result
        self.metrics = PerformanceMetrics.from_backtest_result(result)
    
    def analyze(self) -> Dict:
        """Полный анализ"""
        return {
            "summary": self.metrics.to_dict(),
            "trades": [
                {
                    "id": t.id,
                    "side": t.side,
                    "entry_time": t.entry_time.isoformat() if t.entry_time else None,
                    "entry_price": str(t.entry_price),
                    "exit_time": t.exit_time.isoformat() if t.exit_time else None,
                    "exit_price": str(t.exit_price) if t.exit_price else None,
                    "quantity": str(t.quantity),
                    "pnl": str(t.pnl),
                    "pnl_pct": str(t.pnl_pct),
                    "fees": str(t.fees),
                    "result": t.result,
                    "strategy": t.strategy_name,
                    "exit_reason": t.exit_reason,
                }
                for t in self.result.trades
            ],
            "equity_curve": self.result.equity_curve,
            "daily_stats": [
                {
                    "date": s.date.isoformat() if s.date else None,
                    "pnl": str(s.pnl),
                    "trades": s.trades,
                    "wins": s.wins,
                    "losses": s.losses,
                }
                for s in self.result.daily_stats
            ],
        }
    
    def get_trade_distribution(self) -> Dict:
        """Распределение сделок по PnL"""
        trades = [t for t in self.result.trades if t.result in ["won", "lost"]]
        
        if not trades:
            return {}
        
        pnl_values = [float(t.pnl) for t in trades]
        
        return {
            "count": len(trades),
            "mean": float(np.mean(pnl_values)),
            "std": float(np.std(pnl_values)),
            "min": float(min(pnl_values)),
            "max": float(max(pnl_values)),
            "median": float(np.median(pnl_values)),
            "percentiles": {
                "10": float(np.percentile(pnl_values, 10)),
                "25": float(np.percentile(pnl_values, 25)),
                "50": float(np.percentile(pnl_values, 50)),
                "75": float(np.percentile(pnl_values, 75)),
                "90": float(np.percentile(pnl_values, 90)),
            },
        }
    
    def get_drawdown_analysis(self) -> Dict:
        """Анализ просадок"""
        equity = [float(e["equity"]) for e in self.result.equity_curve]
        
        if len(equity) < 2:
            return {}
        
        # Расчёт просадок
        peak = equity[0]
        current_drawdown = 0
        max_drawdown = 0
        drawdown_starts = []
        drawdown_ends = []
        
        for i, eq in enumerate(equity):
            if eq > peak:
                peak = eq
            
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            
            if dd > current_drawdown and current_drawdown > 0:
                drawdown_ends.append({
                    "start_idx": drawdown_starts[-1]["start_idx"] if drawdown_starts else 0,
                    "end_idx": i,
                    "max_dd": current_drawdown,
                })
            
            if dd > max_drawdown:
                max_drawdown = dd
            
            if dd > current_drawdown:
                current_drawdown = dd
                if not drawdown_starts:
                    drawdown_starts.append({"start_idx": i})
                else:
                    drawdown_starts[-1]["max_dd"] = dd
        
        return {
            "max_drawdown_pct": float(max_drawdown),
            "current_drawdown_pct": float(current_drawdown),
            "num_drawdowns": len([d for d in drawdown_ends if d["max_dd"] > 1]),
            "avg_drawdown_depth": float(np.mean([d["max_dd"] for d in drawdown_ends])) if drawdown_ends else 0,
        }
    
    def get_monthly_performance(self) -> List[Dict]:
        """Производительность по месяцам"""
        if not self.result.equity_curve:
            return []
        
        df = pd.DataFrame(self.result.equity_curve)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["month"] = df["timestamp"].dt.to_period("M")
        
        monthly = df.groupby("month").agg({
            "equity": ["first", "last"],
        })
        monthly.columns = ["start_equity", "end_equity"]
        monthly["return"] = (monthly["end_equity"] - monthly["start_equity"]) / monthly["start_equity"] * 100
        
        return [
            {
                "month": str(idx),
                "start_equity": float(row["start_equity"]),
                "end_equity": float(row["end_equity"]),
                "return_pct": float(row["return"]),
            }
            for idx, row in monthly.iterrows()
        ]
    
    def print_report(self):
        """Вывести текстовый отчёт"""
        print("\n" + "=" * 60)
        print("ASTRA BOT — BACKTEST REPORT")
        print("=" * 60)
        
        m = self.metrics
        
        print(f"\n📊 SUMMARY")
        print(f"  Total Trades:    {m.total_trades}")
        print(f"  Win Rate:        {m.win_rate:.2f}%")
        print(f"  Profit Factor:   {m.profit_factor:.2f}")
        print(f"  Net Profit:      {m.net_profit:.2f}")
        print(f"  Total Return:    {m.total_return_pct:.2f}%")
        
        print(f"\n💰 EQUITY")
        print(f"  Initial:         {m.initial_equity:.2f}")
        print(f"  Final:           {m.final_equity:.2f}")
        print(f"  Max Drawdown:    {m.max_drawdown_pct:.2f}%")
        
        print(f"\n📈 RISK METRICS")
        print(f"  Sharpe Ratio:    {m.sharpe_ratio:.2f}")
        print(f"  Sortino Ratio:   {m.sortino_ratio:.2f}")
        print(f"  Calmar Ratio:    {m.calmar_ratio:.2f}")
        print(f"  Expectancy:      {m.expectancy:.4f} per trade")
        
        print(f"\n🎯 TRADE STATISTICS")
        print(f"  Avg Win:         {m.avg_win:.2f} ({m.avg_win_pct:.2f}%)")
        print(f"  Avg Loss:        {m.avg_loss:.2f} ({m.avg_loss_pct:.2f}%)")
        print(f"  Largest Win:     {m.largest_win:.2f}")
        print(f"  Largest Loss:    {m.largest_loss:.2f}")
        print(f"  Avg Hold Time:   {m.avg_hold_time:.1f}h")
        
        if self.result.trades:
            print(f"\n📝 RECENT TRADES")
            for t in self.result.trades[-5:]:
                symbol = "↑" if t.side == "long" else "↓"
                print(f"  {symbol} #{t.id}: {t.pnl:.2f} ({t.pnl_pct:.2f}%) - {t.result} ({t.exit_reason})")
        
        print("\n" + "=" * 60)


# Утилита для быстрого анализа
def analyze_backtest(result: BacktestResult) -> BacktestAnalyzer:
    """Создать анализатор и запустить анализ"""
    analyzer = BacktestAnalyzer(result)
    analyzer.print_report()
    return analyzer
