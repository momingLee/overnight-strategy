"""
A股交易日历模块
获取和管理A股交易日期，判断交易日/节假日
"""

import pandas as pd
from datetime import date, datetime, timedelta, time as dtime
from pathlib import Path
from typing import Optional
import json

try:
    import akshare as ak
except ImportError:
    ak = None


class TradingCalendar:
    """A股交易日历管理"""

    def __init__(self, cache_path: Optional[str] = None):
        self._calendar: Optional[pd.DataFrame] = None
        self._trading_days: Optional[set] = None
        self.cache_path = Path(cache_path) if cache_path else Path(__file__).parent.parent / "outputs" / "cache" / "trading_calendar.json"
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

    def load_calendar(self) -> pd.DataFrame:
        """加载交易日历，优先从缓存读取"""
        if self._calendar is not None:
            return self._calendar

        # 尝试从缓存加载
        if self.cache_path.exists():
            try:
                with open(self.cache_path, 'r') as f:
                    data = json.load(f)
                dates = [date.fromisoformat(d) for d in data['trading_days']]
                self._trading_days = set(dates)
                self._calendar = pd.DataFrame({'trade_date': sorted(dates)})
                self._calendar['trade_date'] = pd.to_datetime(self._calendar['trade_date'])
                return self._calendar
            except Exception:
                pass

        # 从 akshare 获取
        if ak is not None:
            try:
                tool_trade_date_hist_df = ak.tool_trade_date_hist_sina()
                self._calendar = tool_trade_date_hist_df
                self._calendar.columns = ['trade_date']
                self._calendar['trade_date'] = pd.to_datetime(self._calendar['trade_date'])
                self._trading_days = set(self._calendar['trade_date'].dt.date)
                self._save_cache()
                return self._calendar
            except Exception:
                pass

        # 降级：生成一个不含周末和节假日的近似日历
        print("警告: 无法获取交易日历，使用近似日历（仅排除周末）")
        return self._generate_approx_calendar()

    def _generate_approx_calendar(self, start_year: int = 2000, end_year: int = 2030) -> pd.DataFrame:
        """生成近似交易日历（仅排除周末）"""
        dates = pd.date_range(start=f'{start_year}-01-01', end=f'{end_year}-12-31', freq='B')
        self._calendar = pd.DataFrame({'trade_date': dates})
        self._trading_days = set(d.date() for d in dates)
        return self._calendar

    def _save_cache(self):
        """保存交易日历到缓存"""
        if self._trading_days:
            data = {
                'trading_days': [d.isoformat() for d in sorted(self._trading_days)],
                'updated_at': datetime.now().isoformat()
            }
            with open(self.cache_path, 'w') as f:
                json.dump(data, f)

    def is_trading_day(self, check_date: date) -> bool:
        """判断某天是否为交易日"""
        if self._trading_days is None:
            self.load_calendar()
        return check_date in self._trading_days

    def next_trading_day(self, from_date: date) -> date:
        """获取下一个交易日"""
        if self._trading_days is None:
            self.load_calendar()
        next_day = from_date + timedelta(days=1)
        # 最多往前找30天
        for _ in range(30):
            if next_day in self._trading_days:
                return next_day
            next_day += timedelta(days=1)
        return next_day  # 降级返回

    def previous_trading_day(self, from_date: date) -> date:
        """获取上一个交易日"""
        if self._trading_days is None:
            self.load_calendar()
        prev_day = from_date - timedelta(days=1)
        for _ in range(30):
            if prev_day in self._trading_days:
                return prev_day
            prev_day -= timedelta(days=1)
        return prev_day

    def trading_days_between(self, start: date, end: date) -> list:
        """获取两个日期之间的所有交易日"""
        if self._trading_days is None:
            self.load_calendar()
        result = []
        current = start
        while current <= end:
            if current in self._trading_days:
                result.append(current)
            current += timedelta(days=1)
        return result

    def get_session_phase(self, now: datetime = None) -> str:
        """
        获取当前交易时段阶段

        Returns:
            'pre_auction' (9:15-9:25)
            'call_auction' (9:25)
            'morning_trading' (9:30-11:30)
            'lunch_break' (11:30-13:00)
            'afternoon_trading' (13:00-15:00)
            'late_entry' (14:30-15:00)
            'post_market' (15:00+)
            'closed' (非交易时段)
        """
        if now is None:
            now = datetime.now()

        today = now.date()
        if not self.is_trading_day(today):
            return 'closed'

        t = now.time()

        # 9:15-9:25 集合竞价
        if dtime(9, 15) <= t < dtime(9, 25):
            return 'pre_auction'
        # 9:25 竞价结束
        if dtime(9, 25) <= t < dtime(9, 30):
            return 'call_auction'
        # 9:30-11:30 早盘连续交易
        if dtime(9, 30) <= t < dtime(11, 30):
            return 'morning_trading'
        # 11:30-13:00 午休
        if dtime(11, 30) <= t < dtime(13, 0):
            return 'lunch_break'
        # 13:00-15:00 下午连续交易
        if dtime(13, 0) <= t < dtime(15, 0):
            # 细分尾盘进场时段
            if dtime(14, 30) <= t < dtime(15, 0):
                return 'late_entry'
            return 'afternoon_trading'
        # 15:00后
        if t >= dtime(15, 0):
            return 'post_market'
        # 9:15前
        return 'pre_market'

    def get_today_trading_status(self) -> dict:
        """获取今日交易状态"""
        today = date.today()
        is_td = self.is_trading_day(today)

        # 判断当前时间所在的交易时段
        now = datetime.now()
        morning_start = now.replace(hour=9, minute=30, second=0)
        morning_end = now.replace(hour=11, minute=30, second=0)
        afternoon_start = now.replace(hour=13, minute=0, second=0)
        afternoon_end = now.replace(hour=15, minute=0, second=0)

        status = {
            'is_trading_day': is_td,
            'date': today.isoformat(),
            'time': now.strftime('%H:%M:%S'),
            'session': 'closed'
        }

        if is_td:
            if morning_start <= now <= morning_end:
                status['session'] = 'morning'
            elif afternoon_start <= now <= afternoon_end:
                status['session'] = 'afternoon'
            elif now > afternoon_end:
                status['session'] = 'closed'
            elif now < morning_start:
                status['session'] = 'pre_open'
            elif morning_end < now < afternoon_start:
                status['session'] = 'lunch_break'

        return status
