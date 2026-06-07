"""
实时监控引擎 V2 — 基于 APScheduler 的实时交易监控
V2 新增:
- 9:15-9:20 竞价观察 (Rule 9)
- 9:25 分级离场执行 (Rule 10)
- 14:30+ 进场信号检测 (Part 2)
"""

import json
import time
from datetime import datetime, date, time as dtime
from pathlib import Path
from typing import Optional

import pandas as pd

from data.fetcher import DataFetcher
from data.cache import LocalCache
from data.calendar import TradingCalendar
from strategy.scanner import StockScanner
from strategy.ranking import StockRanker
from strategy.market_filter import MarketFilter
from strategy.exit_strategy import ExitStrategy, ExitAction
from risk.rules import RiskManager
from engine.broker import SimulatedBroker


class RealtimeEngine:
    """实时监控引擎 V2 — 在交易时段自动执行策略"""

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
        self.version = int(config.strategy.get('version', 1))

        self.scanner = StockScanner(config, fetcher, cache, calendar, backtest_mode=False)
        self.ranker = StockRanker(config)
        self.market_filter = MarketFilter(config)
        self.broker = SimulatedBroker(config, calendar)
        self.risk_manager = RiskManager(config)

        if self.version == 2:
            self.exit_strategy = ExitStrategy(config, backtest_mode=False)

        self.dry_run = bool(config.execution.realtime.get('dry_run', True))
        if self.dry_run:
            print("⚠️  当前为模拟模式 (dry_run=true)")

        self.state_dir = Path(config.output.report_dir) / "state"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.state_dir / "realtime_state.json"

        self._running = False
        self._today_signals: list = []
        self._today_trades: list = []
        self._auction_alerts: list = []       # V2: 竞价危险信号记录
        self._current_exit_decisions: dict = {}  # V2: 当前离场决策

    def start(self):
        today = date.today()
        if not self.calendar.is_trading_day(today):
            print(f"今天 ({today}) 不是交易日，监控未启动")
            return

        print(f"\n{'='*60}")
        print(f"  一夜持股法 实时监控系统 {'V2' if self.version == 2 else 'V1'}")
        print(f"  日期: {today}")
        print(f"  模式: {'模拟' if self.dry_run else '实盘'}")
        print(f"{'='*60}\n")

        self._load_state()
        initial_capital = float(self.config.execution.backtest.initial_capital)
        self.broker.reset(initial_capital)
        self.risk_manager.reset_daily(self.broker.account.equity)
        self._running = True

        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            scheduler = BackgroundScheduler()
        except ImportError:
            print("APScheduler 未安装，切换到手动轮询模式...")
            self._run_manual_loop()
            return

        # === V2: 竞价观察 (9:15-9:20, 每10秒) ===
        if self.version == 2:
            scheduler.add_job(
                self._on_auction_observation,
                'cron', hour=9, minute='15-20',
                id='auction_obs'
            )

        # 早盘卖出 (9:25 - V2在竞价结束后执行; V1 9:35)
        exit_hour, exit_min = 9, 25 if self.version == 2 else 35
        scheduler.add_job(
            self._on_morning_exit,
            'cron', hour=exit_hour, minute=exit_min,
            id='morning_exit'
        )

        # V2: 9:35 复核 (持有至9:35的仓位重新判断)
        if self.version == 2:
            scheduler.add_job(
                self._on_0935_recheck,
                'cron', hour=9, minute=35,
                id='0935_recheck'
            )

        # 预扫描 (14:00-14:30)
        scheduler.add_job(
            self._on_pre_scan, 'cron', hour=14, minute='0-29', id='pre_scan'
        )

        # 窗口扫描 (14:30-14:49)
        scheduler.add_job(
            self._on_entry_window_scan, 'interval', seconds=30, id='entry_scan'
        )

        # 最终决策 (14:50-14:59)
        scheduler.add_job(
            self._on_final_scan, 'interval', seconds=10, id='final_scan'
        )

        # 收盘保存 (15:05)
        scheduler.add_job(
            self._on_market_close, 'cron', hour=15, minute=5, id='market_close'
        )

        scheduler.start()
        print("✅ 实时监控已启动")
        if self.version == 2:
            print("   V2 竞价观察: 9:15-9:20")
            print("   V2 分级离场: 9:25 + 9:35 复核")
        print("   按 Ctrl+C 停止\n")

        try:
            while self._running:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n正在停止...")
            self.stop()
            scheduler.shutdown()

    def stop(self):
        self._running = False
        self._save_state()
        print("✅ 监控已停止")

    # ===== V2: 竞价观察 (Rule 9) =====

    def _on_auction_observation(self):
        """9:15-9:20 竞价观察"""
        if not self._is_entry_window_time(9, 15, 9, 21):
            return

        positions = self.broker.account.positions if self.broker.account else []
        if not positions:
            return

        now = datetime.now()
        print(f"\n[{now.strftime('%H:%M:%S')}] 竞价观察...")

        for pos in positions:
            # 收集竞价数据
            auction_data = {
                'auction_price': pos.buy_price,
                'prev_close': pos.buy_price,
                'test_orders_detected': False,
                'bid_changes': [],
                'limit_down_price': pos.buy_price * 0.9,
            }

            # 尝试获取实时竞价数据
            try:
                spot_df = self.fetcher.fetch_real_time_spot()
                if spot_df is not None:
                    sym_row = spot_df[spot_df['代码'] == pos.symbol]
                    if not sym_row.empty:
                        auction_data['prev_close'] = float(sym_row.iloc[0].get('昨收', pos.buy_price))
                        auction_data['auction_price'] = float(sym_row.iloc[0].get('最新价', pos.buy_price))
                        pct = float(sym_row.iloc[0].get('涨跌幅', 0))
                        # 跌停试单判断: 价格接近跌停且成交量极低
                        if pct <= -9.5:
                            auction_data['test_orders_detected'] = True
            except Exception:
                pass

            decision = self.exit_strategy.evaluate_auction(pos.symbol, auction_data)
            if decision:
                print(f"  🚨 {pos.name}({pos.symbol}): {decision.reason}")
                self._auction_alerts.append({
                    'symbol': pos.symbol, 'name': pos.name,
                    'time': now.isoformat(),
                    'action': decision.action.value,
                    'reason': decision.reason,
                })

    # ===== V2: 早盘分级离场 (Rule 10) =====

    def _on_morning_exit(self):
        """早盘卖出 (V2: 分级执行)"""
        positions = self.broker.account.positions if self.broker.account else []
        if not positions:
            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 无持仓")
            return

        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 早盘离场...")

        # 获取实时价格
        price_map = {}
        open_map = {}
        try:
            spot_df = self.fetcher.fetch_real_time_spot()
            if spot_df is not None:
                for _, row in spot_df.iterrows():
                    sym = str(row.get('代码', ''))
                    price_map[sym] = float(row.get('最新价', 0))
                    open_map[sym] = float(row.get('开盘', row.get('今开', row.get('最新价', 0))))
        except Exception as e:
            print(f"  ❌ 获取价格失败: {e}")

        today = date.today()

        for pos in positions:
            open_price = open_map.get(pos.symbol, pos.buy_price)
            current_price = price_map.get(pos.symbol, open_price)

            if self.version == 2:
                # V2 分级离场
                # 检查是否有竞价危险信号
                auction_alert = any(
                    a['symbol'] == pos.symbol for a in self._auction_alerts
                )

                if auction_alert:
                    # Rule 9 触发 → 无条件跌停价卖出
                    trade = self.broker.execute_graded_sell(
                        pos, open_price, current_price, today, ExitAction.SELL_AT_LIMIT_DOWN
                    )
                    if trade:
                        self._today_trades.append(trade)
                    continue

                # Rule 10 分级执行
                decision = self.exit_strategy.evaluate_graded_exit(
                    symbol=pos.symbol, buy_price=pos.buy_price,
                    prev_close=pos.buy_price, open_price=open_price,
                    current_price=current_price,
                    current_time=datetime.now().strftime('%H:%M'),
                    new_high_made=current_price > open_price * 1.005,
                )
                self._current_exit_decisions[pos.symbol] = decision

                if decision.action in (ExitAction.SELL_IMMEDIATELY, ExitAction.SELL_AT_LIMIT_DOWN):
                    trade = self.broker.execute_graded_sell(
                        pos, open_price, current_price, today, decision.action
                    )
                    if trade:
                        self._today_trades.append(trade)
                elif decision.action == ExitAction.HOLD_TO_0935:
                    print(f"  ⏳ {pos.name}({pos.symbol}) 持有至9:35观察 (高开正常)")
                elif decision.action == ExitAction.TRAILING_STOP:
                    print(f"  📈 {pos.name}({pos.symbol}) 移动止盈，止损={decision.stop_price}")
            else:
                # V1 简单卖出
                trade = self.broker.execute_sell(pos, current_price, today)
                if trade:
                    self._today_trades.append(trade)

        self._save_state()

    def _on_0935_recheck(self):
        """V2 9:35 复核 (对 HOLD_TO_0935 的仓位重新判断)"""
        if not self._is_entry_window_time(9, 35, 9, 40):
            return

        positions = self.broker.account.positions if self.broker.account else []
        if not positions:
            return

        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 9:35 持仓复核...")

        today = date.today()
        try:
            spot_df = self.fetcher.fetch_real_time_spot()
        except Exception:
            spot_df = None

        for pos in list(positions):
            current_price = pos.buy_price
            new_high = False
            if spot_df is not None:
                sym_row = spot_df[spot_df['代码'] == pos.symbol]
                if not sym_row.empty:
                    current_price = float(sym_row.iloc[0].get('最新价', pos.buy_price))
                    open_price = float(sym_row.iloc[0].get('今开', pos.buy_price))
                    high = float(sym_row.iloc[0].get('最高', pos.buy_price))
                    new_high = high > open_price * 1.005

            # 检查是否有之前缓存的决策
            prev_decision = self._current_exit_decisions.get(pos.symbol)

            # 对 HOLD_TO_0935 仓位进行复核
            if prev_decision and prev_decision.action == ExitAction.HOLD_TO_0935:
                if new_high:
                    # 创新高 → 移动止盈
                    self.broker.update_position_stop(pos, open_price if 'open_price' in dir() else pos.buy_price)
                    print(f"  📈 {pos.name}({pos.symbol}) 9:35创新高 → 止损上移至开盘价")
                else:
                    # 未创新高 → 卖出
                    trade = self.broker.execute_sell(pos, current_price, today)
                    if trade:
                        self._today_trades.append(trade)
                        print(f"  ✅ {pos.name}({pos.symbol}) 9:35未创新高 → 卖出")

        self._save_state()

    # ===== 扫描阶段 =====

    def _on_pre_scan(self):
        if not self._is_entry_window_time(14, 0, 14, 30):
            return
        now = datetime.now()
        print(f"\n[{now.strftime('%H:%M:%S')}] 预扫描...")
        try:
            spot_df = self.fetcher.fetch_real_time_spot()
            if spot_df is not None and not spot_df.empty:
                breadth = self.market_filter.calculate_market_breadth(spot_df)
                print(f"  情绪:{breadth['sentiment']} 涨跌比:{breadth['ad_ratio']}")

                # V2: 指数检查
                if self.version == 2:
                    index_data = self.scanner._get_index_data()
                    veto_ok, veto_msg = self.market_filter.check_index_environment(index_data or {})
                    print(f"  指数: {'✓' if veto_ok else '✗'} {veto_msg}")
        except Exception as e:
            print(f"  ⚠️  {e}")

    def _on_entry_window_scan(self):
        if not self._is_entry_window_time(14, 30, 14, 50):
            return
        now = datetime.now()
        print(f"\n[{now.strftime('%H:%M:%S')}] 窗口扫描...")
        candidates = self.scanner.quick_scan()
        if candidates:
            print(f"  {len(candidates)} 只通过初筛")

    def _on_final_scan(self):
        if not self._is_entry_window_time(14, 50, 15, 0):
            return
        now = datetime.now()
        print(f"\n[{now.strftime('%H:%M:%S')}] 最终决策...")

        selected = self.scanner.scan(now)
        if not selected:
            print("  无符合条件的股票")
            self._today_signals = []
            return

        self._today_signals = selected
        available = self.broker.account.equity * 0.95
        selected = self.ranker.calculate_position_sizes(selected, available)

        print(f"\n{'='*40}")
        print(f"  📊 买入决策 ({now.strftime('%H:%M:%S')})")
        print(f"{'='*40}")
        for s in selected:
            extra = f" 信号{s.get('signal_count', 0)}/3" if self.version == 2 else ""
            print(f"  #{s['rank']} {s['name']}({s['symbol']}) "
                  f"¥{s['price']:.2f} x {s.get('target_shares', 0)}股 "
                  f"≈ ¥{s.get('target_value', 0):,.0f} "
                  f"得分:{s['score']:.1f}{extra}")

        if now.hour == 14 and now.minute >= 55:
            self._execute_entry(selected)

    def _execute_entry(self, selected: list):
        if self.dry_run:
            print(f"\n  🔒 模拟模式 — 将买入 {len(selected)} 只")
            return
        print(f"\n  🔴 执行买入 — {len(selected)} 只")
        trades = self.broker.execute_buy_batch(selected, date.today())
        self._today_trades.extend(trades)
        self._save_state()

    def _on_market_close(self):
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 收盘处理...")
        self._save_state()
        summary = self.broker.get_account_summary()
        print(f"\n  总权益: ¥{summary.get('equity', 0):,.2f}")
        print(f"  持仓: {summary.get('num_positions', 0)}")
        print(f"  今日买入: {len([t for t in self._today_trades if t.direction == 'buy'])}")
        print(f"  今日卖出: {len([t for t in self._today_trades if t.direction == 'sell'])}")

    # ===== 辅助方法 =====

    def _run_manual_loop(self):
        print("手动轮询: 每30秒")
        try:
            while self._running:
                now = datetime.now()
                phase = self.calendar.get_session_phase(now)
                if phase == 'pre_auction':
                    self._on_auction_observation()
                elif phase in ('call_auction', 'morning_trading'):
                    if 9 <= now.hour and now.minute <= 40:
                        self._on_morning_exit()
                elif phase == 'late_entry':
                    self._on_entry_window_scan()
                    if now.minute >= 50:
                        self._on_final_scan()
                elif phase == 'post_market':
                    self._on_market_close()
                time.sleep(30)
        except KeyboardInterrupt:
            self.stop()

    def _is_entry_window_time(self, sh: int, sm: int, eh: int, em: int) -> bool:
        now = datetime.now()
        return now.replace(hour=sh, minute=sm, second=0) <= now <= now.replace(hour=eh, minute=em, second=0)

    def _save_state(self):
        if self.broker.account is None:
            return
        positions = []
        for p in self.broker.account.positions:
            positions.append({
                'symbol': p.symbol, 'name': p.name,
                'buy_date': p.buy_date.isoformat(),
                'buy_price': p.buy_price, 'shares': p.shares,
                'buy_amount': p.buy_amount,
            })
        state = {
            'saved_at': datetime.now().isoformat(),
            'today': date.today().isoformat(),
            'version': self.version,
            'cash': self.broker.account.cash,
            'equity': self.broker.account.equity,
            'positions': positions,
            'today_signals': self._today_signals,
            'auction_alerts': self._auction_alerts,
        }
        with open(self.state_file, 'w', encoding='utf-8') as f:
            json.dump(state, f, ensure_ascii=False, indent=2, default=str)

    def _load_state(self):
        if not self.state_file.exists():
            return
        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                state = json.load(f)
            saved_date = state.get('today', '')
            today_str = date.today().isoformat()
            if saved_date != today_str:
                if state.get('positions'):
                    next_td = self.calendar.next_trading_day(date.fromisoformat(saved_date))
                    if next_td.isoformat() == today_str:
                        print(f"  恢复昨日持仓: {len(state['positions'])} 只")
            else:
                self._today_signals = state.get('today_signals', [])
        except Exception as e:
            print(f"  加载状态失败: {e}")
