"""
可视化模块
收益曲线、日收益分布、月度热力图、交易分析
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
from typing import Optional


class Visualizer:
    """回测结果可视化"""

    def __init__(self, config):
        self.config = config
        self.report_dir = Path(config.output.report_dir)
        self.report_dir.mkdir(parents=True, exist_ok=True)

        # 配置中文字体
        try:
            import matplotlib
            matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'Microsoft YaHei', 'DejaVu Sans']
            matplotlib.rcParams['axes.unicode_minus'] = False
        except Exception:
            pass

    def plot_equity_curve(
        self, equity_curve: list, benchmark: Optional[pd.Series] = None,
        save: bool = True
    ):
        """绘制权益曲线"""
        import matplotlib.pyplot as plt

        df = pd.DataFrame(equity_curve)
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date')

        fig, ax1 = plt.subplots(figsize=(14, 7))

        # 权益曲线
        ax1.plot(df.index, df['equity'], 'b-', linewidth=1.5, label='Strategy Equity')
        ax1.set_ylabel('Equity (RMB)', color='b')
        ax1.tick_params(axis='y', labelcolor='b')
        ax1.grid(True, alpha=0.3)

        # 基准（如果有）
        if benchmark is not None:
            ax1.plot(benchmark.index, benchmark.values * df['equity'].iloc[0],
                     'gray', linewidth=1, alpha=0.7, label='Benchmark')

        # 回撤面积
        cummax = df['equity'].cummax()
        drawdown = (df['equity'] - cummax) / cummax * 100
        ax2 = ax1.twinx()
        ax2.fill_between(df.index, 0, drawdown.values, alpha=0.3, color='red', label='Drawdown %')
        ax2.set_ylabel('Drawdown (%)', color='r')
        ax2.tick_params(axis='y', labelcolor='r')
        ax2.set_ylim(drawdown.min() * 1.5, 0)

        ax1.set_title('一夜持股法 — 权益曲线', fontsize=14)
        ax1.legend(loc='upper left')
        ax2.legend(loc='upper right')

        plt.tight_layout()

        if save:
            save_path = self.report_dir / f"equity_curve_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            plt.savefig(save_path, dpi=150, bbox_inches='tight')
            print(f"权益曲线已保存: {save_path}")

        plt.show()

    def plot_daily_returns(self, equity_curve: list, save: bool = True):
        """绘制日收益分布"""
        import matplotlib.pyplot as plt

        df = pd.DataFrame(equity_curve)
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date')
        df['daily_return'] = df['equity'].pct_change() * 100

        valid_returns = df['daily_return'].dropna()

        fig, axes = plt.subplots(1, 2, figsize=(14, 5))

        # 日收益时序
        axes[0].bar(df.index, df['daily_return'], width=1,
                    color=['g' if r >= 0 else 'r' for r in df['daily_return']],
                    alpha=0.6)
        axes[0].axhline(y=0, color='black', linewidth=0.5)
        axes[0].set_title('Daily Returns (%)')
        axes[0].set_ylabel('Return %')
        axes[0].grid(True, alpha=0.3)

        # 收益分布直方图
        axes[1].hist(valid_returns, bins=50, color='steelblue', alpha=0.7, edgecolor='white')
        axes[1].axvline(x=0, color='red', linestyle='--', linewidth=1)
        axes[1].axvline(x=valid_returns.mean(), color='green', linestyle='--', linewidth=1,
                        label=f'Mean: {valid_returns.mean():.3f}%')
        axes[1].set_title('Return Distribution')
        axes[1].set_xlabel('Daily Return %')
        axes[1].set_ylabel('Frequency')
        axes[1].legend()

        plt.tight_layout()

        if save:
            save_path = self.report_dir / f"daily_returns_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            plt.savefig(save_path, dpi=150, bbox_inches='tight')
            print(f"收益分布图已保存: {save_path}")

        plt.show()

    def plot_monthly_heatmap(self, equity_curve: list, save: bool = True):
        """绘制月度收益热力图"""
        import matplotlib.pyplot as plt

        df = pd.DataFrame(equity_curve)
        df['date'] = pd.to_datetime(df['date'])
        df = df.set_index('date')
        df['daily_return'] = df['equity'].pct_change()

        # 按月聚合收益率
        monthly = df['daily_return'].resample('M').apply(
            lambda x: (1 + x).prod() - 1
        ) * 100

        # 构建热力图矩阵
        monthly_df = pd.DataFrame({
            'year': monthly.index.year,
            'month': monthly.index.month,
            'return': monthly.values
        })

        pivot = monthly_df.pivot_table(
            values='return', index='year', columns='month', aggfunc='sum'
        )

        # 确保所有月份都存在
        for m in range(1, 13):
            if m not in pivot.columns:
                pivot[m] = np.nan

        pivot = pivot.reindex(sorted(pivot.columns), axis=1)

        fig, ax = plt.subplots(figsize=(12, len(pivot) * 0.8 + 2))
        im = ax.imshow(pivot.values, cmap='RdYlGn', aspect='auto', vmin=-10, vmax=10)

        # 标签
        month_labels = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                        'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        ax.set_xticks(range(12))
        ax.set_xticklabels(month_labels)
        ax.set_yticks(range(len(pivot)))
        ax.set_yticklabels(pivot.index.astype(int))

        # 在格子里显示数值
        for i in range(len(pivot)):
            for j in range(12):
                val = pivot.iloc[i, j]
                if not np.isnan(val):
                    color = 'white' if abs(val) > 5 else 'black'
                    ax.text(j, i, f'{val:.1f}%', ha='center', va='center',
                            color=color, fontsize=8)

        ax.set_title('Monthly Returns Heatmap (%)', fontsize=14)
        plt.colorbar(im, ax=ax, shrink=0.8)

        plt.tight_layout()

        if save:
            save_path = self.report_dir / f"monthly_heatmap_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            plt.savefig(save_path, dpi=150, bbox_inches='tight')
            print(f"月度热力图已保存: {save_path}")

        plt.show()

    def plot_trade_analysis(self, trades: list, save: bool = True):
        """交易分析图表"""
        import matplotlib.pyplot as plt

        sell_trades = [t for t in trades if t.direction == 'sell' and t.pnl is not None]

        if not sell_trades:
            print("没有交易数据")
            return

        pnls = [t.pnl for t in sell_trades]
        pnl_pcts = [t.pnl_pct for t in sell_trades]

        fig, axes = plt.subplots(2, 2, figsize=(14, 10))

        # 累计盈亏
        cumsum = np.cumsum(pnls)
        axes[0, 0].plot(range(len(cumsum)), cumsum, 'b-', linewidth=1)
        axes[0, 0].axhline(y=0, color='black', linewidth=0.5)
        axes[0, 0].fill_between(range(len(cumsum)), 0, cumsum,
                                color=['g' if c > 0 else 'r' for c in cumsum], alpha=0.3)
        axes[0, 0].set_title('Cumulative PnL')
        axes[0, 0].set_xlabel('Trade #')
        axes[0, 0].set_ylabel('PnL (RMB)')
        axes[0, 0].grid(True, alpha=0.3)

        # 单笔盈亏分布
        colors = ['g' if p > 0 else 'r' for p in pnls]
        axes[0, 1].bar(range(len(pnls)), pnls, color=colors, alpha=0.7)
        axes[0, 1].axhline(y=0, color='black', linewidth=0.5)
        axes[0, 1].set_title('Per-Trade PnL')
        axes[0, 1].set_xlabel('Trade #')
        axes[0, 1].set_ylabel('PnL (RMB)')
        axes[0, 1].grid(True, alpha=0.3)

        # 收益率分布
        axes[1, 0].hist(pnl_pcts, bins=30, color='steelblue', alpha=0.7, edgecolor='white')
        axes[1, 0].axvline(x=0, color='red', linestyle='--')
        axes[1, 0].axvline(x=np.mean(pnl_pcts), color='green', linestyle='--',
                           label=f'Mean: {np.mean(pnl_pcts):.2f}%')
        axes[1, 0].set_title('Trade Return Distribution')
        axes[1, 0].set_xlabel('Return %')
        axes[1, 0].set_ylabel('Count')
        axes[1, 0].legend()

        # 胜率饼图
        wins = sum(1 for p in pnls if p > 0)
        losses = len(pnls) - wins
        axes[1, 1].pie([wins, losses], labels=['Wins', 'Losses'],
                       colors=['#2ecc71', '#e74c3c'], autopct='%1.1f%%',
                       startangle=90, explode=(0.05, 0))
        axes[1, 1].set_title(f'Win Rate: {wins/len(pnls)*100:.1f}%')

        plt.tight_layout()

        if save:
            save_path = self.report_dir / f"trade_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
            plt.savefig(save_path, dpi=150, bbox_inches='tight')
            print(f"交易分析图已保存: {save_path}")

        plt.show()
