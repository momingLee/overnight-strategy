"""
本地 SQLite 数据缓存模块
缓存历史日线和分钟线数据，减少重复 API 调用
"""

import sqlite3
import pandas as pd
from datetime import datetime, date
from pathlib import Path
from typing import Optional


class LocalCache:
    """基于 SQLite 的本地数据缓存"""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = Path(__file__).parent.parent / "outputs" / "cache" / "market_data.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        """获取数据库连接"""
        return sqlite3.connect(str(self.db_path))

    def _init_db(self):
        """初始化数据库表"""
        with self._get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_kline (
                    symbol TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    amount REAL,
                    pct_chg REAL,
                    turnover REAL,
                    PRIMARY KEY (symbol, date)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS minute_kline (
                    symbol TEXT NOT NULL,
                    time TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    amount REAL,
                    PRIMARY KEY (symbol, time)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS stock_info (
                    symbol TEXT PRIMARY KEY,
                    name TEXT,
                    market_cap REAL,
                    industry TEXT,
                    board TEXT,
                    updated_at TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_daily_symbol_date ON daily_kline(symbol, date)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS index_daily (
                    index_code TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open REAL,
                    high REAL,
                    low REAL,
                    close REAL,
                    volume REAL,
                    amount REAL,
                    PRIMARY KEY (index_code, date)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_minute_symbol_time ON minute_kline(symbol, time)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_index_code_date ON index_daily(index_code, date)
            """)
            conn.commit()

    # ========== 日线数据 ==========

    def upsert_daily_data(self, symbol: str, df: pd.DataFrame):
        """插入或更新日线数据"""
        if df is None or df.empty:
            return

        required_cols = {'date', 'open', 'high', 'low', 'close', 'volume'}
        if not required_cols.issubset(set(df.columns)):
            # 尝试标准化列名
            return

        with self._get_conn() as conn:
            for _, row in df.iterrows():
                conn.execute("""
                    INSERT OR REPLACE INTO daily_kline (symbol, date, open, high, low, close, volume, amount, pct_chg, turnover)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    symbol,
                    str(row['date'])[:10],
                    float(row.get('open', 0)),
                    float(row.get('high', 0)),
                    float(row.get('low', 0)),
                    float(row.get('close', 0)),
                    float(row.get('volume', 0)),
                    float(row.get('amount', 0)) if 'amount' in row else 0,
                    float(row.get('pct_chg', 0)) if 'pct_chg' in row else 0,
                    float(row.get('turnover', 0)) if 'turnover' in row else 0,
                ))
            conn.commit()

    def get_daily_data(self, symbol: str, start_date: Optional[str] = None, end_date: Optional[str] = None) -> pd.DataFrame:
        """获取缓存的日线数据"""
        query = "SELECT * FROM daily_kline WHERE symbol = ?"
        params = [symbol]

        if start_date:
            query += " AND date >= ?"
            params.append(str(start_date)[:10])
        if end_date:
            query += " AND date <= ?"
            params.append(str(end_date)[:10])

        query += " ORDER BY date ASC"

        with self._get_conn() as conn:
            df = pd.read_sql_query(query, conn, params=params)
        return df

    def get_latest_daily_date(self, symbol: str) -> Optional[str]:
        """获取某只股票最新缓存日期"""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "SELECT MAX(date) FROM daily_kline WHERE symbol = ?", (symbol,)
            )
            result = cursor.fetchone()
            return result[0] if result and result[0] else None

    # ========== 分钟线数据 ==========

    def upsert_minute_data(self, symbol: str, df: pd.DataFrame):
        """插入或更新分钟线数据"""
        if df is None or df.empty:
            return

        with self._get_conn() as conn:
            for _, row in df.iterrows():
                time_str = str(row.get('time', row.get('date', '')))[:19]
                conn.execute("""
                    INSERT OR REPLACE INTO minute_kline (symbol, time, open, high, low, close, volume, amount)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    symbol,
                    time_str,
                    float(row.get('open', 0)),
                    float(row.get('high', 0)),
                    float(row.get('low', 0)),
                    float(row.get('close', 0)),
                    float(row.get('volume', 0)),
                    float(row.get('amount', 0)) if 'amount' in row else 0,
                ))
            conn.commit()

    def get_minute_data(self, symbol: str, date_str: str) -> pd.DataFrame:
        """获取某只股票某日的分钟线数据"""
        with self._get_conn() as conn:
            df = pd.read_sql_query(
                "SELECT * FROM minute_kline WHERE symbol = ? AND time LIKE ? ORDER BY time ASC",
                conn,
                params=(symbol, f"{date_str}%")
            )
        return df

    # ========== 股票信息 ==========

    def upsert_stock_info(self, symbol: str, name: str = '', market_cap: float = 0,
                          industry: str = '', board: str = ''):
        """更新股票基本信息"""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO stock_info (symbol, name, market_cap, industry, board, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (symbol, name, market_cap, industry, board, datetime.now().isoformat()))
            conn.commit()

    def get_stock_info(self, symbol: str) -> Optional[dict]:
        """获取股票信息"""
        with self._get_conn() as conn:
            cursor = conn.execute(
                "SELECT * FROM stock_info WHERE symbol = ?", (symbol,)
            )
            row = cursor.fetchone()
            if row:
                return {
                    'symbol': row[0], 'name': row[1], 'market_cap': row[2],
                    'industry': row[3], 'board': row[4], 'updated_at': row[5]
                }
        return None

    def get_all_symbols(self) -> list:
        """获取所有已缓存的股票代码"""
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT DISTINCT symbol FROM daily_kline")
            return [row[0] for row in cursor.fetchall()]

    def get_latest_date(self) -> Optional[str]:
        """获取所有缓存中最新的日期"""
        with self._get_conn() as conn:
            cursor = conn.execute("SELECT MAX(date) FROM daily_kline")
            result = cursor.fetchone()
            return result[0] if result and result[0] else None

    # ========== 指数数据 ==========

    def upsert_index_data(self, index_code: str, df: pd.DataFrame):
        """插入或更新指数日线数据"""
        if df is None or df.empty:
            return
        with self._get_conn() as conn:
            for _, row in df.iterrows():
                date_str = str(row.get('date', ''))[:10]
                if not date_str:
                    continue
                conn.execute("""
                    INSERT OR REPLACE INTO index_daily (index_code, date, open, high, low, close, volume, amount)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    index_code,
                    date_str,
                    float(row.get('open', 0)),
                    float(row.get('high', 0)),
                    float(row.get('low', 0)),
                    float(row.get('close', 0)),
                    float(row.get('volume', 0)),
                    float(row.get('amount', 0)) if 'amount' in row else 0,
                ))
            conn.commit()

    def get_index_data(self, index_code: str, start_date: str = None, end_date: str = None) -> pd.DataFrame:
        """获取缓存的指数日线数据"""
        query = "SELECT * FROM index_daily WHERE index_code = ?"
        params = [index_code]
        if start_date:
            query += " AND date >= ?"
            params.append(start_date[:10])
        if end_date:
            query += " AND date <= ?"
            params.append(end_date[:10])
        query += " ORDER BY date ASC"
        with self._get_conn() as conn:
            return pd.read_sql_query(query, conn, params=params)

    # ========== 维护 ==========

    def clean_old_data(self, keep_days: int = 365):
        """清理过期数据"""
        from datetime import timedelta
        cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
        with self._get_conn() as conn:
            conn.execute("DELETE FROM daily_kline WHERE date < ?", (cutoff,))
            conn.execute("DELETE FROM minute_kline WHERE time < ?", (cutoff,))
            conn.commit()

    def get_cache_stats(self) -> dict:
        """获取缓存统计信息"""
        with self._get_conn() as conn:
            daily_count = conn.execute("SELECT COUNT(*) FROM daily_kline").fetchone()[0]
            minute_count = conn.execute("SELECT COUNT(*) FROM minute_kline").fetchone()[0]
            stock_count = conn.execute("SELECT COUNT(DISTINCT symbol) FROM daily_kline").fetchone()[0]
        return {
            'daily_records': daily_count,
            'minute_records': minute_count,
            'unique_stocks': stock_count,
            'db_path': str(self.db_path),
        }
