"""
数据获取模块 — 封装 akshare API 调用
支持实时行情、历史日线、分钟线、板块数据
"""

import time
import pandas as pd
from datetime import datetime, date, timedelta
from typing import Optional
from pathlib import Path

try:
    import akshare as ak
except ImportError:
    ak = None
    print("警告: akshare 未安装，数据获取功能不可用。请运行: pip install akshare")


class DataFetcher:
    """统一的数据获取接口，封装 akshare 调用"""

    def __init__(self, request_interval: float = 1.0, max_retries: int = 3, timeout: int = 30):
        self.request_interval = request_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self._last_request_time = 0

    def _rate_limit(self):
        """请求频率控制"""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.request_interval:
            time.sleep(self.request_interval - elapsed)
        self._last_request_time = time.time()

    def _retry(self, func, *args, **kwargs):
        """带重试的API调用"""
        last_error = None
        for attempt in range(self.max_retries):
            try:
                self._rate_limit()
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    wait = 2 ** attempt
                    print(f"  API 调用失败 (尝试 {attempt+1}/{self.max_retries})，{wait}秒后重试: {e}")
                    time.sleep(wait)
        raise last_error

    # ========== 实时行情 ==========

    def fetch_real_time_spot(self) -> pd.DataFrame:
        """获取全A股实时行情数据 (stock_zh_a_spot_em)"""
        if ak is None:
            raise ImportError("akshare 未安装")
        return self._retry(ak.stock_zh_a_spot_em)

    def fetch_stock_billboard(self, symbol: str = "北向资金") -> pd.DataFrame:
        """获取龙虎榜/北向资金数据"""
        if ak is None:
            raise ImportError("akshare 未安装")
        return self._retry(ak.stock_sector_fund_flow_rank, indicator=symbol)

    # ========== 历史K线 ==========

    def fetch_daily_kline(
        self, symbol: str, start_date: str, end_date: str, adjust: str = "qfq"
    ) -> pd.DataFrame:
        """
        获取个股历史日K线数据
        adjust: 'qfq'=前复权, 'hfq'=后复权, ''=不复权
        """
        if ak is None:
            raise ImportError("akshare 未安装")
        df = self._retry(
            ak.stock_zh_a_hist,
            symbol=symbol,
            period="daily",
            start_date=start_date,
            end_date=end_date,
            adjust=adjust
        )
        if df is not None and not df.empty:
            df = self._normalize_daily_columns(df)
        return df

    def fetch_minute_kline(
        self, symbol: str, period: str = "1", start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> pd.DataFrame:
        """
        获取个股分钟K线数据
        period: '1', '5', '15', '30', '60'
        """
        if ak is None:
            raise ImportError("akshare 未安装")
        # akshare 分钟线接口：stock_zh_a_hist_min_em
        try:
            df = self._retry(
                ak.stock_zh_a_hist_min_em,
                symbol=symbol,
                period=period,
                start_date=start_date or "1970-01-01",
                end_date=end_date or "2050-12-31",
                adjust="qfq"
            )
            if df is not None and not df.empty:
                df = self._normalize_minute_columns(df)
            return df
        except Exception:
            # 降级尝试另一个接口
            try:
                df = self._retry(
                    ak.stock_zh_a_hist_min_em,
                    symbol=symbol,
                    period=period,
                    adjust="qfq"
                )
                if df is not None and not df.empty:
                    df = self._normalize_minute_columns(df)
                return df
            except Exception as e:
                print(f"获取分钟数据失败 {symbol}: {e}")
                return pd.DataFrame()

    def fetch_index_daily(self, index_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        """获取指数日线数据（如上证指数 000001）"""
        if ak is None:
            raise ImportError("akshare 未安装")
        try:
            # 尝试不同格式的指数代码
            df = self._retry(
                ak.stock_zh_index_daily,
                symbol=f"sh{index_code}" if len(index_code) == 6 else index_code
            )
            if df is not None and not df.empty:
                df['date'] = pd.to_datetime(df['date'])
                df = df[(df['date'] >= start_date) & (df['date'] <= end_date)]
            return df
        except Exception:
            return pd.DataFrame()

    # ========== 板块数据 ==========

    def fetch_industry_boards(self) -> pd.DataFrame:
        """获取行业板块行情"""
        if ak is None:
            raise ImportError("akshare 未安装")
        return self._retry(ak.stock_board_industry_name_em)

    def fetch_concept_boards(self) -> pd.DataFrame:
        """获取概念板块行情"""
        if ak is None:
            raise ImportError("akshare 未安装")
        return self._retry(ak.stock_board_concept_name_em)

    def fetch_board_components(self, board_name: str) -> pd.DataFrame:
        """获取板块成分股"""
        if ak is None:
            raise ImportError("akshare 未安装")
        return self._retry(ak.stock_board_industry_cons_em, symbol=board_name)

    # ========== 资金流向 ==========

    def fetch_money_flow(self, symbol: str) -> pd.DataFrame:
        """获取个股资金流向"""
        if ak is None:
            raise ImportError("akshare 未安装")
        market = "sh" if symbol.startswith(('6', '5')) else "sz"
        return self._retry(ak.stock_individual_fund_flow, stock=symbol, market=market)

    # ========== 市场宽度 ==========

    def fetch_market_breadth(self) -> pd.DataFrame:
        """获取市场整体涨跌统计"""
        if ak is None:
            raise ImportError("akshare 未安装")
        try:
            # 从实时行情中提取
            spot_df = self.fetch_real_time_spot()
            total = len(spot_df)
            up_count = len(spot_df[spot_df['涨跌幅'] > 0])
            down_count = len(spot_df[spot_df['涨跌幅'] < 0])
            flat_count = total - up_count - down_count

            limit_up = len(spot_df[spot_df['涨跌幅'] >= 9.5])
            limit_down = len(spot_df[spot_df['涨跌幅'] <= -9.5])

            return pd.DataFrame([{
                'total': total,
                'up': up_count,
                'down': down_count,
                'flat': flat_count,
                'up_ratio': up_count / total if total > 0 else 0,
                'advance_decline_ratio': up_count / down_count if down_count > 0 else 999,
                'limit_up': limit_up,
                'limit_down': limit_down,
            }])
        except Exception as e:
            print(f"获取市场宽度失败: {e}")
            return pd.DataFrame()

    # ========== V2 新增方法 ==========

    def fetch_index_daily_ma5(self, index_code: str, lookback_days: int = 10) -> dict:
        """
        获取指数日线数据并计算5日均线和5日均量 (Rule 1)
        Returns: {'close': latest_close, 'ma5': float, 'volume_ma5': float, 'df': DataFrame}
        """
        end_date = datetime.now().strftime('%Y%m%d')
        start_date = (datetime.now() - timedelta(days=lookback_days * 2)).strftime('%Y%m%d')

        try:
            df = self.fetch_index_daily(index_code, start_date, end_date)
            if df is None or df.empty:
                return None

            if 'close' in df.columns and len(df) >= 5:
                ma5_close = df['close'].tail(5).mean()
                latest_close = float(df['close'].iloc[-1])
                if 'volume' in df.columns:
                    ma5_vol = df['volume'].tail(5).mean()
                else:
                    ma5_vol = 0
                return {
                    'index_code': index_code,
                    'latest_close': latest_close,
                    'ma5_close': ma5_close,
                    'volume_ma5': ma5_vol,
                    'above_ma5': latest_close >= ma5_close,
                    'df': df,
                }
        except Exception as e:
            print(f"获取指数{index_code}数据失败: {e}")
        return None

    def fetch_concept_board_constituents(self, board_name: str) -> pd.DataFrame:
        """
        获取概念板块成分股 (Rule 2)
        用于检测板块内涨停股数量
        """
        if ak is None:
            return pd.DataFrame()
        try:
            return self._retry(ak.stock_board_concept_cons_em, symbol=board_name)
        except Exception:
            # 尝试用行业板块接口
            try:
                return self._retry(ak.stock_board_industry_cons_em, symbol=board_name)
            except Exception:
                return pd.DataFrame()

    def fetch_money_flow_intraday(self, symbol: str) -> pd.DataFrame:
        """
        获取个股资金流向 (Rule 6 - Signal A)
        实时模式使用，回测模式返回空DataFrame
        """
        if ak is None:
            return pd.DataFrame()
        try:
            # akshare 资金流向接口
            df = self._retry(ak.stock_individual_fund_flow, stock=symbol, market="sh")
            return df if df is not None else pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    def fetch_minute_kline_today(self, symbol: str, period: str = '1') -> pd.DataFrame:
        """
        获取当日分钟K线 (Rules 4, 7, 8)
        """
        today_str = datetime.now().strftime('%Y-%m-%d')
        try:
            df = self.fetch_minute_kline(symbol, period=period,
                                         start_date=today_str, end_date=today_str)
            return df if df is not None else pd.DataFrame()
        except Exception:
            return pd.DataFrame()

    # ========== 列名标准化 ==========

    def _normalize_daily_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """标准化日线数据列名"""
        col_map = {
            '日期': 'date', '开盘': 'open', '收盘': 'close',
            '最高': 'high', '最低': 'low', '成交量': 'volume',
            '成交额': 'amount', '振幅': 'amplitude', '涨跌幅': 'pct_chg',
            '涨跌额': 'change', '换手率': 'turnover',
        }
        df = df.rename(columns=col_map)
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
        return df

    def _normalize_minute_columns(self, df: pd.DataFrame) -> pd.DataFrame:
        """标准化分钟线数据列名"""
        col_map = {
            '时间': 'time', '开盘': 'open', '收盘': 'close',
            '最高': 'high', '最低': 'low', '成交量': 'volume',
            '成交额': 'amount',
        }
        df = df.rename(columns=col_map)
        if 'time' in df.columns:
            df['time'] = pd.to_datetime(df['time'])
        return df

    # ========== 批量获取 ==========

    def fetch_batch_daily(
        self, symbols: list, start_date: str, end_date: str,
        progress_callback=None
    ) -> dict:
        """批量获取多只股票的日线数据"""
        result = {}
        total = len(symbols)
        for i, sym in enumerate(symbols):
            try:
                df = self.fetch_daily_kline(sym, start_date, end_date)
                if df is not None and not df.empty:
                    result[sym] = df
            except Exception as e:
                print(f"获取 {sym} 失败: {e}")
            if progress_callback:
                progress_callback(i + 1, total)
            elif i % 100 == 0:
                print(f"  数据获取进度: {i+1}/{total}")
        return result

    # ========== 股票基本信息 ==========

    def fetch_stock_info(self) -> pd.DataFrame:
        """获取A股股票基本信息（含市值、行业等）"""
        if ak is None:
            raise ImportError("akshare 未安装")
        try:
            return self._retry(ak.stock_zh_a_spot_em)
        except Exception:
            return pd.DataFrame()

    # ========== 龙虎榜 ==========

    def fetch_billboard(self, trade_date: Optional[str] = None) -> pd.DataFrame:
        """获取龙虎榜数据"""
        if ak is None:
            raise ImportError("akshare 未安装")
        if trade_date is None:
            trade_date = datetime.now().strftime('%Y%m%d')
        return self._retry(ak.stock_sse_summary)
