"""
绩效报告模块
生成回测报告、交易统计、持仓分析
"""

import json
import pandas as pd
from datetime import date, datetime
from pathlib import Path
from typing import Optional


class PerformanceReporter:
    """绩效报告生成器"""

    def __init__(self, config):
        self.config = config
        self.report_dir = Path(config.output.report_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)

    def generate_backtest_report(
        self, trades: list, equity_curve: list, metrics: dict
    ) -> dict:
        """
        生成完整回测报告

        Returns:
            dict 包含完整报告数据
        """
        report = {
            'generated_at': datetime.now().isoformat(),
            'metrics': metrics,
            'trade_summary': self._trade_summary(trades),
            'monthly_returns': self._monthly_returns(equity_curve),
            'yearly_returns': self._yearly_returns(equity_curve),
            'worst_trades': self._worst_trades(trades),
            'best_trades': self._best_trades(trades),
        }

        # 保存报告
        self._save_report(report)

        return report

    def _trade_summary(self, trades: list) -> dict:
        """交易统计摘要"""
        sell_trades = [t for t in trades if t.direction == 'sell']
        buy_trades = [t for t in trades if t.direction == 'buy']

        if not sell_trades:
            return {'num_buys': len(buy_trades), 'num_sells': 0}

        pnls = [t.pnl for t in sell_trades if t.pnl is not None]
        pnl_pcts = [t.pnl_pct for t in sell_trades if t.pnl_pct is not None]

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        return {
            'num_buys': len(buy_trades),
            'num_sells': len(sell_trades),
            'total_pnl': sum(pnls),
            'max_single_profit': max(pnls) if pnls else 0,
            'max_single_loss': min(pnls) if pnls else 0,
            'avg_profit_per_trade': sum(pnls) / len(pnls) if pnls else 0,
            'avg_pnl_pct': sum(pnl_pcts) / len(pnl_pcts) if pnl_pcts else 0,
            'win_count': len(wins),
            'loss_count': len(losses),
            'avg_win': sum(wins) / len(wins) if wins else 0,
            'avg_loss': sum(losses) / len(losses) if losses else 0,
        }

    def _monthly_returns(self, equity_curve: list) -> dict:
        """月度收益"""
        if not equity_curve:
            return {}

        df = pd.DataFrame(equity_curve)
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date')

        # 按月聚合
        if 'equity' in df.columns:
            monthly = df['equity'].resample('M').last()
        else:
            return {}

        returns = {}
        for idx, val in monthly.items():
            month_key = idx.strftime('%Y-%m')
            prev = monthly.shift(1).get(idx, monthly.iloc[0]) if idx > monthly.index[0] else monthly.iloc[0]
            if len(monthly) > 1:
                prev_val = monthly.shift(1).loc[idx] if idx in monthly.shift(1).index else monthly.iloc[0]
                returns[month_key] = round((val - prev_val) / prev_val * 100, 2)

        return returns

    def _yearly_returns(self, equity_curve: list) -> dict:
        """年度收益"""
        if not equity_curve:
            return {}

        df = pd.DataFrame(equity_curve)
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date')

        if 'equity' not in df.columns:
            return {}

        yearly = df['equity'].resample('Y').last()
        returns = {}
        for idx, val in yearly.items():
            year_key = idx.strftime('%Y')
            if idx > yearly.index[0]:
                prev_val = yearly.shift(1).loc[idx]
                returns[year_key] = round((val - prev_val) / prev_val * 100, 2)

        return returns

    def _worst_trades(self, trades: list, n: int = 5) -> list:
        """最差N笔交易"""
        sell_trades = [t for t in trades if t.direction == 'sell' and t.pnl is not None]
        sorted_trades = sorted(sell_trades, key=lambda x: x.pnl)[:n]
        return [self._trade_to_dict(t) for t in sorted_trades]

    def _best_trades(self, trades: list, n: int = 5) -> list:
        """最佳N笔交易"""
        sell_trades = [t for t in trades if t.direction == 'sell' and t.pnl is not None]
        sorted_trades = sorted(sell_trades, key=lambda x: x.pnl, reverse=True)[:n]
        return [self._trade_to_dict(t) for t in sorted_trades]

    def _trade_to_dict(self, trade) -> dict:
        return {
            'symbol': trade.symbol,
            'name': trade.name,
            'direction': trade.direction,
            'date': trade.date.isoformat() if isinstance(trade.date, date) else str(trade.date),
            'price': trade.price,
            'shares': trade.shares,
            'amount': trade.amount,
            'commission': trade.commission,
            'stamp_duty': trade.stamp_duty,
            'pnl': trade.pnl,
            'pnl_pct': trade.pnl_pct,
        }

    def _save_report(self, report: dict):
        """保存报告到文件"""
        # JSON 格式
        json_path = self.report_dir / f"backtest_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n报告已保存: {json_path}")

    def export_trades_csv(self, trades: list, filename: Optional[str] = None):
        """导出交易记录为CSV"""
        if filename is None:
            filename = f"trades_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

        path = self.report_dir / filename
        trade_dicts = [self._trade_to_dict(t) for t in trades]
        df = pd.DataFrame(trade_dicts)
        df.to_csv(path, index=False, encoding='utf-8-sig')
        print(f"交易记录已导出: {path}")
        return path

    def export_equity_curve(self, equity_curve: list, filename: Optional[str] = None):
        """导出权益曲线为CSV"""
        if filename is None:
            filename = f"equity_curve_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

        path = self.report_dir / filename
        df = pd.DataFrame(equity_curve)
        df.to_csv(path, index=False, encoding='utf-8-sig')
        print(f"权益曲线已导出: {path}")
        return path

    def print_summary(self, metrics: dict):
        """打印格式化的绩效摘要到控制台"""
        veto_days = metrics.get('veto_days', 0)
        adj_return = metrics.get('adjusted_return', None)

        report = f"""
╔══════════════════════════════════════════════════╗
║           一夜持股法 — 回测绩效报告              ║
╠══════════════════════════════════════════════════╣
║  回测区间:  {metrics.get('start_date', '?')} → {metrics.get('end_date', '?')}
║
║  📈 收益指标:
║     总收益率:       {metrics['total_return']*100:>8.2f}%"""
        if adj_return is not None:
            report += f"""
║     V2调整后收益:   {adj_return*100:>8.2f}% (置信度{metrics.get('confidence_penalty', 'N/A')})
║     指数否决天数:   {veto_days:>8}"""
        report += f"""
║     年化收益率:     {metrics['annual_return']*100:>8.2f}%
║     最终权益:       ¥{metrics['final_equity']:>10,.2f}
║
║  📉 风险指标:
║     最大回撤:       {metrics['max_drawdown']*100:>8.2f}%
║     夏普比率:       {metrics['sharpe_ratio']:>8.2f}
║     卡玛比率:       {metrics['calmar_ratio']:>8.2f}
║
║  📊 交易统计:
║     总交易次数:     {metrics['total_trades']:>8}
║     胜率:           {metrics['win_rate']*100:>8.1f}%
║     平均每笔盈亏:   ¥{metrics['avg_trade_pnl']:>10.2f}
║     盈亏比:         {metrics['profit_factor']:>8.2f}
║
║  💰 费用:
║     总佣金:         ¥{metrics['total_commission']:>10.2f}
║     总印花税:       ¥{metrics['total_stamp_duty']:>10.2f}
╚══════════════════════════════════════════════════╝
"""
        print(report)
