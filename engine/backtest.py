"""
回测引擎 V2 — 一夜持股法
按交易日逐日模拟，支持 V2 全量策略:
- 每日指数否决检查 (Rule 1)
- 7步筛选流水线 (Rules 2-5)
- 进场信号检测 (Part 2, 回测降级)
- 分级离场执行 (Part 3, 回测近似)
"""

import pandas as pd
import numpy as np
from datetime import date, datetime, timedelta
from typing import Optional
import time

from data.fetcher import DataFetcher
from data.cache import LocalCache
from data.calendar import TradingCalendar
from strategy.market_filter import MarketFilter
from strategy.filters import StockFilter
from strategy.scanner import StockScanner
from strategy.ranking import StockRanker
from strategy.entry_signals import EntrySignalDetector
from strategy.exit_strategy import ExitStrategy, ExitAction
from engine.broker import SimulatedBroker, Trade, Position


class BacktestEngine:
    """一夜持股法回测引擎 (V1 + V2)"""

    def __init__(
        self,
        config,
        fetcher: DataFetcher,
        cache: LocalCache,
        calendar: TradingCalendar,
    ):
        self.config = config
        self.fetcher = fetcher
        self.cache = cache
        self.calendar = calendar
        self.broker = SimulatedBroker(config, calendar)

        self.version = int(config.strategy.get('version', 1))
        self.stock_filter = StockFilter(config)
        self.market_filter = MarketFilter(config)
        self.ranker = StockRanker(config)

        if self.version == 2:
            self.entry_detector = EntrySignalDetector(config, backtest_mode=True)
            self.exit_strategy = ExitStrategy(config, backtest_mode=True)
        else:
            self.entry_detector = None
            self.exit_strategy = None

        bt = config.execution.backtest
        self.initial_capital = float(bt.initial_capital)
        self.confidence_penalty = float(bt.get('backtest_confidence_penalty', 1.0))

        if self.version == 2:
            self.max_positions = int(config.strategy.v2.ranking.max_positions)
            self.single_pct = float(config.strategy.v2.ranking.single_position_pct)
        else:
            self.max_positions = int(config.strategy.ranking.max_positions)
            self.single_pct = float(config.strategy.ranking.single_position_pct)

    def run(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        initial_capital: Optional[float] = None,
        symbols: Optional[list] = None,
    ) -> dict:
        if start_date is None:
            start_date = self.config.execution.backtest.start_date
        if end_date is None:
            end_date = self.config.execution.backtest.end_date
        if initial_capital is None:
            initial_capital = self.initial_capital

        print(f"\n{'='*60}")
        print(f"  一夜持股法 回测引擎 {'V2' if self.version == 2 else 'V1'}")
        print(f"  回测区间: {start_date} → {end_date}")
        print(f"  初始资金: ¥{initial_capital:,.2f}")
        if self.version == 2:
            print(f"  回测置信度折扣: {self.confidence_penalty}")
        print(f"{'='*60}\n")

        self.broker.reset(initial_capital)
        self.calendar.load_calendar()

        start_dt = date.fromisoformat(start_date)
        end_dt = date.fromisoformat(end_date)
        trading_days = self.calendar.trading_days_between(start_dt, end_dt)

        print(f"交易日数量: {len(trading_days)}")
        if len(trading_days) == 0:
            return {'trades': [], 'equity_curve': [], 'metrics': {}}

        if symbols is None:
            symbols = self._get_default_pool()

        print(f"股票池: {len(symbols)} 只")
        print(f"每日最大持仓: {self.max_positions} 只")

        # 预加载数据
        daily_data = self._preload_data(symbols, start_date, end_date)
        index_data = self._preload_index_data(start_date, end_date) if self.version == 2 else {}

        # 回测循环
        equity_curve = []
        all_trades = []
        prev_positions = []
        veto_count = 0

        from tqdm import tqdm
        for i, td in enumerate(tqdm(trading_days, desc="回测进度")):
            td_str = td.isoformat()

            # === V2 指数否决检查 ===
            if self.version == 2 and index_data:
                veto_passed, veto_reason = self._check_index_veto(td_str, index_data)
                if not veto_passed:
                    veto_count += 1
                    # 有持仓仍然卖出
                    if prev_positions:
                        morning_prices = self._get_morning_prices(prev_positions, td_str, daily_data)
                        sell_trades = self._exit_positions_v2(prev_positions, morning_prices, td, daily_data)
                        all_trades.extend(sell_trades)
                        prev_positions = []
                    equity_curve.append({
                        'date': td_str,
                        'equity': self.broker.account.equity,
                        'cash': self.broker.account.cash,
                        'positions': 0,
                        'veto': True,
                    })
                    continue

            # === 早盘: 卖出昨日持仓 ===
            if prev_positions:
                morning_prices = self._get_morning_prices(prev_positions, td_str, daily_data)
                if self.version == 2 and self.exit_strategy:
                    sell_trades = self._exit_positions_v2(prev_positions, morning_prices, td, daily_data)
                else:
                    sell_trades = self.broker.execute_sell_all(prev_positions, morning_prices, td)
                all_trades.extend(sell_trades)
                prev_positions = []

            # === 尾盘: 扫描并买入 ===
            if self.version == 2:
                candidates = self._scan_backtest_v2(symbols, td_str, daily_data)
            else:
                candidates = self._scan_backtest(symbols, td_str, daily_data)

            if candidates:
                available = self.broker.account.cash * 0.99
                selected = self.ranker.rank_and_select(candidates)
                selected = self.ranker.calculate_position_sizes(selected, available)

                buy_trades = self.broker.execute_buy_batch(selected, td, self.single_pct)
                all_trades.extend(buy_trades)
                prev_positions = list(self.broker.account.positions)

            equity_curve.append({
                'date': td_str,
                'equity': self.broker.account.equity,
                'cash': self.broker.account.cash,
                'positions': len(prev_positions),
                'veto': False,
            })

        # 最后清仓
        if prev_positions:
            last_day = trading_days[-1]
            morning_prices = self._get_morning_prices(prev_positions, last_day.isoformat(), daily_data)
            sell_trades = self.broker.execute_sell_all(prev_positions, morning_prices, last_day)
            all_trades.extend(sell_trades)

        # 绩效
        metrics = self._calculate_metrics(all_trades, equity_curve, start_date, end_date)
        if self.version == 2:
            metrics['veto_days'] = veto_count
            metrics['confidence_penalty'] = self.confidence_penalty
            metrics['adjusted_return'] = metrics['total_return'] * self.confidence_penalty

        print(f"\n{'='*60}")
        print(f"  回测完成")
        print(f"{'='*60}")
        print(f"  总收益率:    {metrics['total_return']*100:.2f}%")
        if self.version == 2:
            print(f"  调整后收益率:{metrics['adjusted_return']*100:.2f}% (置信度{self.confidence_penalty})")
            print(f"  指数否决天数:{metrics['veto_days']}")
        print(f"  年化收益率:  {metrics['annual_return']*100:.2f}%")
        print(f"  最大回撤:    {metrics['max_drawdown']*100:.2f}%")
        print(f"  胜率:        {metrics['win_rate']*100:.1f}%")
        print(f"  夏普比率:    {metrics['sharpe_ratio']:.2f}")
        print(f"  总交易次数:  {metrics['total_trades']}")
        print(f"  最终权益:    ¥{self.broker.account.equity:,.2f}")

        return {'trades': all_trades, 'equity_curve': equity_curve, 'metrics': metrics}

    # ===== V2: 指数否决 =====

    def _preload_index_data(self, start_date: str, end_date: str) -> dict:
        """预加载指数数据"""
        print("预加载指数数据...")
        index_data = {}
        codes = self.market_filter.index_codes
        for code in codes:
            try:
                df = self.cache.get_index_data(code, start_date, end_date)
                if df is None or df.empty:
                    df = self.fetcher.fetch_index_daily(code, start_date, end_date)
                    if df is not None and not df.empty:
                        self.cache.upsert_index_data(code, df)
                if df is not None and not df.empty:
                    index_data[code] = df
            except Exception as e:
                print(f"  指数{code}数据加载失败: {e}")
        print(f"  加载了 {len(index_data)} 个指数数据")
        return index_data

    def _check_index_veto(self, date_str: str, index_data: dict) -> tuple:
        """检查当日指数是否一票否决"""
        td = date.fromisoformat(date_str)
        env_data = {}
        for code, df in index_data.items():
            if df is None or df.empty:
                continue
            df['date'] = pd.to_datetime(df['date'])
            day = df[df['date'].dt.strftime('%Y-%m-%d') == date_str]
            if day.empty:
                continue
            # 需要数据到当日，且至少有5天才能算MA
            idx = df[df['date'] < td.isoformat()]
            if len(idx) < 5:
                continue
            close = float(day.iloc[0]['close'])
            ma5 = idx['close'].tail(5).mean()
            vol = float(day.iloc[0].get('volume', 0))
            vol_ma5 = idx['volume'].tail(5).mean() if 'volume' in idx.columns else vol
            env_data[code] = {
                'latest_close': close, 'ma5_close': ma5,
                'above_ma5': close >= ma5,
                'volume_ma5': vol_ma5,
                'volume_above_ma5': vol >= vol_ma5,
            }
        return self.market_filter.check_index_environment(env_data)

    # ===== V2: 尾盘扫描 =====

    def _scan_backtest_v2(self, symbols: list, date_str: str, daily_data: dict) -> list[dict]:
        """V2 回测扫描 (含记忆周期+日内强度+进场信号)"""
        candidates = []
        all_gains = []

        # 先收集所有日涨幅
        for sym in symbols:
            df = daily_data.get(sym)
            if df is None or df.empty:
                continue
            df['date'] = pd.to_datetime(df['date'])
            day_data = df[df['date'].dt.strftime('%Y-%m-%d') == date_str]
            if day_data.empty:
                continue
            try:
                all_gains.append(float(day_data.iloc[-1].get('pct_chg', 0)))
            except Exception:
                all_gains.append(0)

        for sym in symbols:
            df = daily_data.get(sym)
            if df is None or df.empty:
                continue

            df['date'] = pd.to_datetime(df['date'])
            day_data = df[df['date'].dt.strftime('%Y-%m-%d') == date_str]
            if day_data.empty:
                continue

            row = day_data.iloc[-1]
            current_price = float(row['close'])
            open_price = float(row['open'])
            high = float(row['high'])
            low = float(row['low'])
            prev_close = float(df.iloc[-2]['close']) if len(df) >= 2 else open_price
            volume = float(row.get('volume', 0))
            pct_chg = float(row.get('pct_chg', 0))
            turnover = float(row.get('turnover', 0))
            market_cap = float(row.get('总市值', 0)) if '总市值' in row.index else 0
            name = sym

            recent_5 = df.tail(6).head(5)
            avg_vol_5d = recent_5['volume'].mean() if len(recent_5) >= 5 else volume

            # VWAP 近似
            vwap = (high + low + current_price) / 3

            # V2 筛选
            passed, details = self.stock_filter.apply_all_v2(
                symbol=sym, name=name,
                current_price=current_price, prev_close=prev_close,
                current_volume=volume, avg_volume_5d=avg_vol_5d,
                turnover_rate=turnover, market_cap=market_cap,
                change_pct=pct_chg, daily_kline=df.tail(60), all_gains=all_gains,
            )

            if not passed:
                continue

            # 进场信号检测 (回测模式：仅 Signal B 生效)
            signal_count, signal_details = self.entry_detector.detect_signals(
                symbol=sym, daily_kline=df.tail(20),
            )
            if not signal_details.get('_summary', {}).get('requirements_met', False):
                continue

            candidates.append({
                'symbol': sym, 'name': name,
                'price': current_price, 'change_pct': pct_chg,
                'volume_ratio': details.get('volume_ratio', {}).get('value', 0),
                'turnover': turnover, 'market_cap': market_cap,
                'sector_rank': 0, 'filter_details': details,
                'signal_count': signal_count, 'signal_details': signal_details,
                'memory_hits': details.get('memory_cycle', {}).get('hit_count', 0),
                'gain_percentile': details.get('intraday_strength', {}).get('gain_percentile', 0),
                'above_vwap': details.get('intraday_strength', {}).get('above_vwap', True),
            })

        return candidates

    def _exit_positions_v2(self, positions: list, morning_prices: dict,
                           trade_date: date, daily_data: dict) -> list[Trade]:
        """V2 分级离场"""
        trades = []
        for pos in positions:
            sym = pos.symbol

            # 获取次日开盘价、最高价等
            open_price = morning_prices.get(sym, pos.buy_price)
            df = daily_data.get(sym)
            current_price = open_price
            new_high = False

            if df is not None and not df.empty:
                df['date'] = pd.to_datetime(df['date'])
                next_date_str = trade_date.isoformat()
                next_day = df[df['date'].dt.strftime('%Y-%m-%d') == next_date_str]
                if not next_day.empty:
                    row = next_day.iloc[-1]
                    current_price = float(row.get('close', open_price))
                    new_high = float(row.get('high', open_price)) > open_price * 1.01

            # T+1检查
            can, reason = self.broker.can_sell(sym, pos.buy_date, trade_date)
            if not can:
                continue

            # 使用 ExitStrategy 做决策
            decision = self.exit_strategy.evaluate_graded_exit(
                symbol=sym, buy_price=pos.buy_price,
                prev_close=pos.buy_price,  # 回测中用买入价近似昨收
                open_price=open_price, current_price=current_price,
                new_high_made=new_high,
            )

            trade = self.broker.execute_graded_sell(
                pos, open_price, current_price, trade_date, decision.action
            )
            if trade:
                trades.append(trade)

        return trades

    # ===== V1 兼容方法 =====

    def _get_default_pool(self) -> list:
        try:
            hs300 = self.fetcher._retry(
                lambda: __import__('akshare').ak.index_stock_cons('000300')
            )
            if hs300 is not None and not hs300.empty:
                return hs300['品种代码'].tolist() if '品种代码' in hs300.columns else hs300['stock_code'].tolist()
        except Exception:
            pass
        return [
            '600519', '000858', '601318', '600036', '000333',
            '600276', '600900', '601166', '600030', '000651',
            '002415', '300750', '603259', '601012', '000725',
        ]

    def _preload_data(self, symbols: list, start_date: str, end_date: str) -> dict:
        data = {}
        from tqdm import tqdm
        for sym in tqdm(symbols[:200], desc="加载日线"):
            try:
                df = self.cache.get_daily_data(sym, start_date, end_date)
                if df is None or len(df) < 10:
                    df = self.fetcher.fetch_daily_kline(sym, start_date, end_date)
                    if df is not None and not df.empty:
                        self.cache.upsert_daily_data(sym, df)
                if df is not None and not df.empty:
                    data[sym] = df
            except Exception:
                pass
        print(f"  加载了 {len(data)} 只股票数据")
        return data

    def _get_morning_prices(self, positions: list, date_str: str, daily_data: dict) -> dict:
        """获取当日开盘价作为卖出价 (date_str = 卖出日)"""
        prices = {}
        for pos in positions:
            sym = pos.symbol
            df = daily_data.get(sym)
            if df is not None and not df.empty:
                df['date'] = pd.to_datetime(df['date'])
                day_data = df[df['date'].dt.strftime('%Y-%m-%d') == date_str]
                if not day_data.empty:
                    prices[sym] = float(day_data.iloc[0]['open'])
                    continue
            prices[sym] = pos.buy_price
        return prices

    def _scan_backtest(self, symbols: list, date_str: str, daily_data: dict) -> list[dict]:
        """V1 回测扫描 (向后兼容)"""
        candidates = []
        for sym in symbols:
            df = daily_data.get(sym)
            if df is None or df.empty:
                continue
            df['date'] = pd.to_datetime(df['date'])
            day_data = df[df['date'].dt.strftime('%Y-%m-%d') == date_str]
            if day_data.empty:
                continue
            row = day_data.iloc[-1]
            current_price = float(row['close'])
            open_price = float(row['open'])
            prev_close = float(df.iloc[-2]['close']) if len(df) >= 2 else open_price
            volume = float(row.get('volume', 0))
            pct_chg = float(row.get('pct_chg', 0))
            turnover = float(row.get('turnover', 0))
            recent_5 = df.tail(6).head(5)
            avg_vol_5d = recent_5['volume'].mean() if len(recent_5) >= 5 else volume
            tail_lift = (current_price - open_price) / open_price * 100 * 0.6 if open_price > 0 else 0
            ma5_data = df.tail(5)['close']
            ma5 = ma5_data.mean() if len(ma5_data) >= 5 else current_price

            passed, details = self.stock_filter.apply_all(
                symbol=sym, name=sym,
                current_price=current_price, prev_close=prev_close,
                current_volume=volume, avg_volume_5d=avg_vol_5d,
                price_30min_ago=current_price / (1 + tail_lift / 100) if tail_lift != 0 else open_price,
                turnover_rate=turnover, market_cap=0, change_pct=pct_chg,
            )
            if passed and current_price >= ma5:
                candidates.append({
                    'symbol': sym, 'name': sym, 'price': current_price,
                    'change_pct': pct_chg,
                    'volume_ratio': details.get('volume_ratio', {}).get('value', 0),
                    'tail_lift': tail_lift, 'turnover': turnover,
                    'market_cap': 0, 'sector_rank': 0, 'filter_details': details,
                })
        return candidates

    def _calculate_metrics(self, trades: list, equity_curve: list,
                           start_date: str, end_date: str) -> dict:
        if not equity_curve:
            return self._empty_metrics()
        eq_df = pd.DataFrame(equity_curve)
        eq_df['date'] = pd.to_datetime(eq_df['date'])
        eq_df = eq_df.set_index('date')
        eq_df['daily_return'] = eq_df['equity'].pct_change()
        sell_trades = [t for t in trades if t.direction == 'sell']
        total_return = (eq_df['equity'].iloc[-1] - self.initial_capital) / self.initial_capital
        days = (eq_df.index[-1] - eq_df.index[0]).days
        annual_return = (1 + total_return) ** (365 / days) - 1 if days > 0 else 0.0
        cummax = eq_df['equity'].cummax()
        drawdown = (eq_df['equity'] - cummax) / cummax
        max_drawdown = drawdown.min()
        if sell_trades:
            wins = [t for t in sell_trades if t.pnl and t.pnl > 0]
            win_rate = len(wins) / len(sell_trades)
            avg_win = np.mean([t.pnl for t in wins]) if wins else 0
            losses = [t for t in sell_trades if t.pnl and t.pnl <= 0]
            avg_loss = abs(np.mean([t.pnl for t in losses])) if losses else 0
            total_pnl = sum(t.pnl or 0 for t in sell_trades)
            avg_trade_pnl = total_pnl / len(sell_trades)
        else:
            win_rate = avg_win = avg_loss = avg_trade_pnl = total_pnl = 0
        rf_daily = 0.02 / 252
        valid_returns = eq_df['daily_return'].dropna()
        sharpe = ((valid_returns.mean() - rf_daily) / valid_returns.std() * np.sqrt(252)
                  if len(valid_returns) > 1 and valid_returns.std() > 0 else 0)
        profit_factor = avg_win / avg_loss if avg_loss > 0 else (999 if avg_win > 0 else 0)
        calmar = annual_return / abs(max_drawdown) if abs(max_drawdown) > 0 else 0
        metrics = {
            'total_return': total_return, 'annual_return': annual_return,
            'max_drawdown': max_drawdown, 'sharpe_ratio': sharpe, 'calmar_ratio': calmar,
            'win_rate': win_rate, 'total_trades': len(sell_trades),
            'total_pnl': total_pnl, 'avg_trade_pnl': avg_trade_pnl,
            'avg_win': avg_win, 'avg_loss': avg_loss, 'profit_factor': profit_factor,
            'final_equity': self.broker.account.equity,
            'total_commission': self.broker.account.total_commission,
            'total_stamp_duty': self.broker.account.total_stamp_duty,
            'start_date': start_date, 'end_date': end_date,
        }
        if self.version == 2:
            metrics['adjusted_return'] = total_return * self.confidence_penalty
            metrics['confidence_penalty'] = self.confidence_penalty
        return metrics

    def _empty_metrics(self) -> dict:
        m = {
            'total_return': 0, 'annual_return': 0, 'max_drawdown': 0,
            'sharpe_ratio': 0, 'calmar_ratio': 0, 'win_rate': 0,
            'total_trades': 0, 'total_pnl': 0, 'avg_trade_pnl': 0,
            'avg_win': 0, 'avg_loss': 0, 'profit_factor': 0,
            'final_equity': self.initial_capital,
            'total_commission': 0, 'total_stamp_duty': 0,
        }
        if self.version == 2:
            m['adjusted_return'] = 0
            m['confidence_penalty'] = self.confidence_penalty
        return m
