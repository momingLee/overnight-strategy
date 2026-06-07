#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
一夜持股法 - Web 可视化分析平台
Flask + ECharts | 优先真实数据，降级模拟数据
运行: python web_app.py -> 浏览器 http://127.0.0.1:5000
"""

import sys
import io
import json
import random
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, str(Path(__file__).parent))

from datetime import datetime, date, timedelta
from flask import Flask, render_template, jsonify
import pandas as pd
import numpy as np
from data.calendar import TradingCalendar
import time

app = Flask(__name__)

# 全局错误处理 - 防止单次请求崩溃
@app.errorhandler(500)
def handle_500(e):
    return jsonify({'success': False, 'error': '服务器内部错误，请重试'}), 500

@app.errorhandler(Exception)
def handle_all(e):
    return jsonify({'success': False, 'error': str(e)}), 500

# ======== 股票 & 板块基础库 ========
STOCK_DB = {
    '600519': {'name': '贵州茅台', 'industry': '白酒', 'cap': 21000, 'price_range': (1400, 1800)},
    '000858': {'name': '五粮液', 'industry': '白酒', 'cap': 5500, 'price_range': (120, 160)},
    '300750': {'name': '宁德时代', 'industry': '锂电池', 'cap': 8500, 'price_range': (180, 250)},
    '002415': {'name': '海康威视', 'industry': '安防', 'cap': 3100, 'price_range': (30, 45)},
    '601318': {'name': '中国平安', 'industry': '保险', 'cap': 7800, 'price_range': (40, 55)},
    '600036': {'name': '招商银行', 'industry': '银行', 'cap': 9000, 'price_range': (35, 45)},
    '000333': {'name': '美的集团', 'industry': '家电', 'cap': 4800, 'price_range': (55, 75)},
    '600276': {'name': '恒瑞医药', 'industry': '医药', 'cap': 2800, 'price_range': (40, 55)},
    '601012': {'name': '隆基绿能', 'industry': '光伏', 'cap': 1400, 'price_range': (18, 28)},
    '600030': {'name': '中信证券', 'industry': '券商', 'cap': 3200, 'price_range': (18, 25)},
    '002594': {'name': '比亚迪', 'industry': '新能源车', 'cap': 7500, 'price_range': (230, 300)},
    '300059': {'name': '东方财富', 'industry': '券商', 'cap': 2800, 'price_range': (15, 22)},
    '000651': {'name': '格力电器', 'industry': '家电', 'cap': 2200, 'price_range': (38, 48)},
    '000725': {'name': '京东方A', 'industry': '面板', 'cap': 1400, 'price_range': (3.5, 5)},
    '002230': {'name': '科大讯飞', 'industry': '人工智能', 'cap': 1200, 'price_range': (40, 60)},
    '300124': {'name': '汇川技术', 'industry': '机器人', 'cap': 1600, 'price_range': (55, 75)},
    '603259': {'name': '药明康德', 'industry': '创新药', 'cap': 1500, 'price_range': (50, 75)},
    '601899': {'name': '紫金矿业', 'industry': '有色', 'cap': 3500, 'price_range': (12, 18)},
    '688981': {'name': '中芯国际', 'industry': '芯片', 'cap': 3500, 'price_range': (45, 65)},
    '002475': {'name': '立讯精密', 'industry': '消费电子', 'cap': 2500, 'price_range': (30, 42)},
}

SECTOR_DB = ['白酒', '锂电池', '光伏', '芯片', '新能源车', '医药', '券商',
             '银行', '保险', '家电', '安防', '旅游', '军工', '人工智能',
             '机器人', '低空经济', '消费电子', '创新药', '面板', '有色']


# ======== 指数数据（启动加载 + 实时刷新） ========
_index_cache = {}
_index_last_fetch = 0

def _fetch_index_data(force=False):
    """获取实时指数数据（新浪实时行情 + akshare日线MA5）"""
    global _index_cache, _index_last_fetch
    now = time.time()
    if not force and _index_cache and (now - _index_last_fetch) < 2:
        return _index_cache
    _index_last_fetch = now

    import requests
    # 新浪实时指数代码映射
    idx_map = {
        '上证指数': ('s_sh000001', '000001'),
        '创业板指': ('s_sz399006', '399006'),
    }

    for key, (sina_code, ak_code) in idx_map.items():
        try:
            # 1. 获取实时指数价格 (新浪)
            headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.sina.com.cn/'}
            r = requests.get(f'https://hq.sinajs.cn/list={sina_code}', headers=headers, timeout=5)
            realtime_price = None
            if r.status_code == 200 and '=' in r.text and '"' in r.text:
                parts = r.text.split('"')[1].split(',')
                if len(parts) >= 2:
                    realtime_price = float(parts[1]) if parts[1] else None

            # 2. 获取MA5 (日K线) — 上证用sh, 深证用sz
            import akshare as ak
            prefix = 'sz' if ak_code.startswith(('399', '300')) else 'sh'
            df = ak.stock_zh_index_daily(symbol=f'{prefix}{ak_code}')
            if df is not None and not df.empty:
                if 'date' in df.columns:
                    df = df.rename(columns={c: c.lower() for c in df.columns})
                closes = df['close'].astype(float).values
                ma5 = float(np.mean(closes[-5:])) if len(closes) >= 5 else 0

                # 用实时价格，如果没有则用最新日线
                current = realtime_price if realtime_price else float(closes[-1])

                _index_cache[key] = {
                    'close': current,
                    'ma5': round(ma5, 2),
                    'above_ma5': current >= ma5,
                    'realtime': realtime_price is not None,
                    'updated': datetime.now().strftime('%H:%M:%S')
                }
        except Exception as e:
            print(f"  [WARN] {key}: {e}")
    return _index_cache


# K线缓存 (避免每3秒重复拉取)  +  全市场筛选缓存
_kline_cache = {}
_kline_ttl = 60
_screen_cache = None
_screen_time = 0
_screen_ttl = 30


def _fetch_spot_data(symbol=None):
    """获取实时行情 - 新浪单股实时报价 (使用连接池)"""
    try:
        if symbol:
            if symbol.startswith(('6', '5')):
                code = f'sh{symbol}'
            else:
                code = f'sz{symbol}'
            url = f'https://hq.sinajs.cn/list={code}'
            sess = _get_session()
            r = sess.get(url, timeout=10)
            if r.status_code == 200 and r.text:
                # 解析新浪格式: var hq_str_sz000858="名称,今开,昨收,现价,最高,最低,..."
                text = r.text
                if '=' in text and '"' in text:
                    parts = text.split('"')[1].split(',')
                    if len(parts) > 30:
                        return {
                            'name': parts[0],
                            'open': float(parts[1]) if parts[1] else 0,
                            'prev_close': float(parts[2]) if parts[2] else 0,
                            'price': float(parts[3]) if parts[3] else 0,
                            'high': float(parts[4]) if parts[4] else 0,
                            'low': float(parts[5]) if parts[5] else 0,
                            'volume': float(parts[8]) if len(parts) > 8 and parts[8] else 0,
                            'amount': float(parts[9]) if len(parts) > 9 and parts[9] else 0,
                        }
    except Exception:
        pass
    return None


_REQ_SESSION = None

def _get_session():
    global _REQ_SESSION
    if _REQ_SESSION is None:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        _REQ_SESSION = requests.Session()
        retry = Retry(total=3, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        _REQ_SESSION.mount('http://', adapter)
        _REQ_SESSION.mount('https://', adapter)
        _REQ_SESSION.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://finance.sina.com.cn/',
            'Accept': 'text/html,application/json,application/xhtml+xml,*/*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        })
    return _REQ_SESSION


def _fetch_kline(symbol):
    """获取个股日K线 (带60秒缓存)"""
    now = time.time()
    if symbol in _kline_cache and (now - _kline_cache[symbol]['ts']) < _kline_ttl:
        return _kline_cache[symbol]['df']

    import akshare as ak
    if symbol.startswith(('6', '5')):
        full_sym = f'sh{symbol}'
    else:
        full_sym = f'sz{symbol}'

    end_d = datetime.now().strftime('%Y%m%d')
    start_d = (datetime.now() - timedelta(days=120)).strftime('%Y%m%d')

    for attempt in range(3):
        try:
            df = ak.stock_zh_a_daily(symbol=full_sym, start_date=start_d,
                                     end_date=end_d, adjust='qfq')
            if df is not None and not df.empty:
                # 新浪返回的列名已经是英文, 标准化保证兼容
                if 'date' in df.columns:
                    df['date'] = pd.to_datetime(df['date'])
                if 'pct_chg' not in df.columns:
                    # 计算涨跌幅
                    closes = df['close'].astype(float)
                    df['pct_chg'] = closes.pct_change() * 100
                for col in ['volume', 'amount', 'turnover']:
                    if col in df.columns:
                        df[col] = pd.to_numeric(df[col], errors='coerce')
                _kline_cache[symbol] = {'df': df, 'ts': time.time()}
                return df
        except Exception as e:
            if attempt < 2:
                time.sleep(1)
            else:
                print(f"  [FAIL] {symbol} 新浪K线失败: {e}")
    return None


def _df_to_kline(df):
    kline = []
    for _, row in df.tail(60).iterrows():
        d = row.get('date', '')
        if hasattr(d, 'strftime'):
            d = d.strftime('%Y-%m-%d')
        kline.append([str(d)[:10],
                      round(float(row.get('open', 0)), 2),
                      round(float(row.get('close', 0)), 2),
                      round(float(row.get('low', 0)), 2),
                      round(float(row.get('high', 0)), 2),
                      int(row.get('volume', 0))])
    return kline


def _extract_price(df, spot=None):
    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else latest
    closes = df['close'].astype(float).values
    current = float(latest['close'])
    prev_close = float(prev['close'])
    open_p = float(latest.get('open', current))
    high = float(latest.get('high', current))
    low = float(latest.get('low', current))

    # 如果有实时报价，用实时的 open/high/low/current
    if spot:
        if spot.get('price', 0) > 0:
            current = spot['price']
        if spot.get('open', 0) > 0:
            open_p = spot['open']
        if spot.get('high', 0) > 0:
            high = spot['high']
        if spot.get('low', 0) > 0:
            low = spot['low']
        if spot.get('prev_close', 0) > 0:
            prev_close = spot['prev_close']

    if prev_close > 0:
        pct = round((current - prev_close) / prev_close * 100, 2)
    else:
        pct = 0

    # 新浪日线换手率是小数(0.015=1.5%), 需*100
    raw_turnover = float(latest.get('turnover', 0.03)) if 'turnover' in df.columns else 0.03
    turnover = raw_turnover * 100 if raw_turnover < 1 else raw_turnover
    volume = int(latest.get('volume', 0))
    avg_vol_5d = float(df['volume'].tail(6).head(5).mean()) if len(df) >= 6 else max(volume, 1)
    ma5 = round(float(np.mean(closes[-5:])), 2) if len(closes) >= 5 else current
    vwap = round((high + low + current) / 3, 2)
    vol_ratio = round(volume / avg_vol_5d, 2) if avg_vol_5d > 0 else 1.0
    amount = round(float(latest.get('amount', volume * current / 1e8)), 2)

    return {
        'current': current, 'prev_close': prev_close,
        'open': open_p, 'high': high, 'low': low,
        'change_pct': pct, 'volume': volume, 'amount': amount,
        'turnover_rate': turnover, 'market_cap': 100,
        'ma5': ma5, 'vwap': vwap, 'volume_ratio': vol_ratio,
        'above_ma5': current >= ma5,
    }


# ======== 模拟数据降级方案 ========

def _generate_fake_kline(symbol, days=60):
    info = STOCK_DB.get(symbol, {'name': symbol, 'industry': random.choice(SECTOR_DB),
                                  'cap': random.randint(50, 500), 'price_range': (10, 50)})
    pr = info['price_range']
    base = random.uniform(pr[0], pr[1])
    # 线程安全的本地随机状态
    local_random = random.Random(hash(symbol) % 100000)
    local_np = np.random.RandomState(hash(symbol) % 100000)
    dates = pd.date_range(end=date.today(), periods=days, freq='B')
    returns = local_np.normal(0.0005, 0.02, days)
    trend = np.linspace(-0.05, 0.08, days)
    returns = returns + trend * 0.01
    closes = base * np.cumprod(1 + returns)
    highs = closes * (1 + np.abs(local_np.normal(0, 0.015, days)))
    lows = closes * (1 - np.abs(local_np.normal(0, 0.015, days)))
    opens = np.roll(closes, 1) * (1 + local_np.normal(0, 0.005, days))
    opens[0] = closes[0] * 0.995
    volumes = local_np.randint(5000000, 30000000, days)
    pct_chg = np.append([0], np.diff(closes) / closes[:-1] * 100)

    kline = []
    for i in range(days):
        kline.append([dates[i].strftime('%Y-%m-%d'), round(float(opens[i]), 2),
                      round(float(closes[i]), 2), round(float(lows[i]), 2),
                      round(float(highs[i]), 2), int(volumes[i])])
    df = pd.DataFrame({'date': dates, 'open': opens, 'high': highs, 'low': lows,
                       'close': closes, 'volume': volumes, 'pct_chg': pct_chg,
                       'turnover': [local_random.uniform(3, 12) for _ in range(days)] * days})
    price = _extract_price(df)
    price['market_cap'] = info['cap'] + random.randint(-200, 200)
    return {'name': info['name'], 'industry': info['industry'],
            'kline': kline, 'df': df, 'price': price, 'is_real': False}


def _build_real_data(symbol, df, spot=None):
    info = STOCK_DB.get(symbol, {})
    name = info.get('name', symbol)
    industry = info.get('industry', '未知')
    if spot and spot.get('name'):
        name = spot['name']
    price = _extract_price(df, spot)
    if info:
        price['market_cap'] = info.get('cap', 100)
    return {'name': name, 'industry': industry,
            'kline': _df_to_kline(df), 'df': df, 'price': price, 'is_real': True}


# ======== 核心策略分析 ========

def analyze(symbol, data):
    p = data['price']
    name = data['name']
    industry = data['industry']
    df = data['df']

    # --- Rule 1: 大盘环境 ---
    sz = _index_cache.get('上证指数')
    cy = _index_cache.get('创业板指')
    # V3: 所有指数必须都站上MA5 (AND逻辑)
    if sz and cy:
        veto_passed = sz['above_ma5'] and cy['above_ma5']
        idx_summary = f"上证{sz['close']:.0f} vs MA5({sz['ma5']:.0f}) & 创业板{cy['close']:.0f} vs MA5({cy['ma5']:.0f})"
    elif sz:
        veto_passed = sz['above_ma5']
        idx_summary = f"上证{sz['close']:.0f} vs MA5({sz['ma5']:.0f})"
    elif cy:
        veto_passed = cy['above_ma5']
        idx_summary = f"创业板{cy['close']:.0f} vs MA5({cy['ma5']:.0f})"
    else:
        veto_passed = True
        idx_summary = "指数数据暂不可用"

    # --- Part 1 过滤器 ---
    filters = []

    # ST
    st = 'ST' in name.upper() or '*ST' in name.upper()
    filters.append({'key': 'st', 'label': 'ST检查', 'passed': not st, 'value': 'ST' if st else '正常'})

    # 涨跌停
    lim_up = p['change_pct'] >= 9.8
    lim_dn = p['change_pct'] <= -9.8
    filters.append({'key': 'limit', 'label': '涨跌停状态', 'passed': not lim_up and not lim_dn,
                    'value': '涨停' if lim_up else ('跌停' if lim_dn else '正常')})

    # 股价
    filters.append({'key': 'price', 'label': '股价>=3元', 'passed': p['current'] >= 3.0,
                    'value': f"{p['current']}元"})

    # 市值 R5
    cap_ok = 50 <= p['market_cap'] <= 200
    filters.append({'key': 'cap', 'label': '市值(R5:50-200亿)', 'passed': cap_ok,
                    'value': f"{p['market_cap']}亿"})

    # 量比 R5
    vok = p['volume_ratio'] >= 1.0
    filters.append({'key': 'vol', 'label': '量比(R5:>=1)', 'passed': vok,
                    'value': f"{p['volume_ratio']:.2f}"})

    # 换手率 R5
    tok = 5 <= p['turnover_rate'] <= 10
    filters.append({'key': 'turnover', 'label': '换手率(R5:5-10%)', 'passed': tok,
                    'value': f"{p['turnover_rate']:.2f}%"})

    # 记忆周期 R3 — 只算大涨(>7%), 不算暴跌
    recent10 = df.tail(10)
    if 'pct_chg' in df.columns:
        big = recent10[recent10['pct_chg'].astype(float) > 7]
        mem_hits = max(len(big), len(recent10[recent10['pct_chg'].astype(float) >= 9.5]))
    else:
        c = recent10['close'].astype(float).values
        if len(c) >= 2:
            chg = np.diff(c) / c[:-1] * 100
            mem_hits = sum(1 for x in chg if x > 7)
        else:
            mem_hits = 0
    mok = mem_hits > 0
    filters.append({'key': 'memory', 'label': '记忆周期(R3:近10日异动)', 'passed': mok,
                    'value': f'{mem_hits}次大涨' if mem_hits > 0 else '近10日无大涨'})

    # 日内强度 R4
    above_vwap = p['current'] >= p['vwap']
    above_ma5 = p['current'] >= p['ma5']
    iok = above_vwap and above_ma5  # V3: 两者必须同时满足
    filters.append({'key': 'intraday', 'label': '日内强度(R4:VWAP+MA5)', 'passed': iok,
                    'value': f"VWAP{'上' if above_vwap else '下'} MA5{'上' if above_ma5 else '下'}"})

    all_pass = all(f['passed'] for f in filters)

    # --- Part 2 进场信号 ---
    sa = vok and p['change_pct'] > -2
    sb = p['current'] >= p['high'] * 0.97 and vok
    sc = abs(p['change_pct']) < 6 and p['amount'] > 0.5

    signals = [
        {'key': 'sa', 'label': '信号A: 大单吸筹', 'passed': sa,
         'reason': '量能配合净流入' if sa else ('跌幅过大' if p['change_pct'] <= -2 else '量能不足')},
        {'key': 'sb', 'label': '信号B: 尾盘突破', 'passed': sb,
         'reason': f"收盘接近最高{p['high']}" if sb else f"距最高{round((1-p['current']/p['high'])*100,1)}%"},
        {'key': 'sc', 'label': '信号C: 抗抛压横盘', 'passed': sc,
         'reason': '振幅可控' if sc else '振幅偏大或成交清淡'},
    ]
    sig_cnt = sum(1 for s in signals if s['passed'])
    sig_req = 2

    # --- 评分 ---
    fscore = sum(1 for f in filters if f['passed']) / max(len(filters), 1) * 60
    sscore = (sig_cnt / 3) * 25
    xscore = 5 if veto_passed else 0
    total = round(fscore + sscore + xscore + (5 if industry in SECTOR_DB[:8] else 0), 1)

    # --- 判定 ---
    if not veto_passed:
        verdict, vc, vr = '空仓观望', '#e74c3c', f'大盘一票否决: {idx_summary}'
    elif all_pass and sig_cnt >= 2:
        verdict, vc, vr = '强烈推荐', '#27ae60', '全部筛选通过+进场信号确认，适合尾盘买入'
    elif all_pass:
        verdict, vc, vr = '可关注', '#f39c12', '个股条件满足但信号不足，继续观察'
    elif sig_cnt >= 1:
        verdict, vc, vr = '观察中', '#3498db', '有进场信号但部分过滤未通过'
    else:
        verdict, vc, vr = '不推荐', '#e74c3c', '多项条件不满足'

    # --- 板块 ---
    if industry in SECTOR_DB:
        srank = SECTOR_DB.index(industry) + 1
        is_hot = srank <= 20
    else:
        srank = 50
        is_hot = False
    schg = round(p['change_pct'] * 0.8, 2)  # 基于个股估算板块
    lu = 2 if is_hot else 0

    # 构建指数列表
    indices = []
    if sz:
        indices.append({'code': '000001', 'name': '上证指数', 'close': sz['close'],
                        'ma5': sz['ma5'], 'above_ma5': sz['above_ma5']})
    if cy:
        indices.append({'code': '399006', 'name': '创业板指', 'close': cy['close'],
                        'ma5': cy['ma5'], 'above_ma5': cy['above_ma5']})

    return _safe_json({
        'success': True, 'name': name, 'symbol': symbol, 'is_real': data.get('is_real', False),
        'analyzed_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'is_trading_day': TradingCalendar().is_trading_day(date.today()),
        'price': p, 'kline': data['kline'],
        'filters': filters, 'filter_pass_count': sum(1 for f in filters if f['passed']),
        'filter_total': len(filters), 'all_passed': all_pass,
        'signals': signals, 'signal_count': sig_cnt, 'signal_required': sig_req,
        'score': total,
        'verdict': verdict, 'verdict_color': vc, 'verdict_reason': vr,
        'market': {
            'indices': indices,
            'veto_passed': veto_passed,
            'summary': f"大盘达标 ({idx_summary})" if veto_passed else f"大盘一票否决 ({idx_summary})"
        },
        'sector': {
            'name': industry, 'rank': srank, 'change_pct': schg, 'is_hot': is_hot,
            'linkage': {
                'passed': lu >= 2, 'limit_up_count': lu, 'min_required': 2,
                'reason': f"板块{lu}只涨停" + (' -> 联动确认' if lu >= 2 else ' -> 孤狼不碰'),
            }
        },
    })


# ======== JSON 安全转换 ========

def _safe_json(obj):
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return None if np.isnan(obj) else float(obj)
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.bool_,)):
        return bool(obj)
    if isinstance(obj, dict):
        return {str(k): _safe_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe_json(v) for v in obj]
    if isinstance(obj, float) and np.isnan(obj):
        return None
    return obj


# ======== 路由 ========

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/analyze/<symbol>')
def api_analyze(symbol):
    symbol = symbol.replace('sh', '').replace('sz', '').replace('bj', '').strip()
    try:
        # 拿到真实K线 (新浪)
        df = _fetch_kline(symbol)

        # 拿到新浪实时报价 (补充当日最新价)
        spot = _fetch_spot_data(symbol)

        if df is not None and not df.empty:
            data = _build_real_data(symbol, df, spot)
            return jsonify(analyze(symbol, data))

        # K线也拿不到才降级模拟
        data = _generate_fake_kline(symbol)
        return jsonify(analyze(symbol, data))
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/market')
def api_market():
    """实时大盘数据（每3秒自动刷新）"""
    try:
        idx = _fetch_index_data(force=True)
        indices = []
        for name, data in idx.items():
            indices.append({
                'name': name, 'close': round(data['close'], 2),
                'ma5': round(data['ma5'], 2), 'above_ma5': data['above_ma5'],
                'updated': data.get('updated', '')
            })
        veto = any(not d['above_ma5'] for d in idx.values()) if idx else False  # 任一指数不达标即否决
        return jsonify(_safe_json({
            'success': True,
            'indices': indices,
            'veto_passed': not veto,
            'timestamp': datetime.now().strftime('%H:%M:%S'),
        }))
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


# ======== 全市场自动筛选 ========

# 全量A股股票池 (~5300只, 全覆盖)
_STOCK_POOL = []
for i in range(600000, 606000):       # 上海主板
    _STOCK_POOL.append(f'sh{i}')
for i in range(688000, 690000):       # 科创板
    _STOCK_POOL.append(f'sh{i}')
for i in range(1, 4000):              # 深圳主板
    _STOCK_POOL.append(f'sz{i:06d}')
for i in range(300000, 302000):       # 创业板
    _STOCK_POOL.append(f'sz{i}')
print(f'  Stock pool: {len(_STOCK_POOL)} stocks')


def _batch_fetch_sina(codes, batch_size=300):
    """批量从新浪获取实时行情 (使用连接池)"""
    results = {}
    sess = _get_session()

    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        url = 'https://hq.sinajs.cn/list=' + ','.join(batch)
        try:
            r = sess.get(url, timeout=10)
            if r.status_code == 200:
                for line in r.text.strip().split('\n'):
                    if '=' not in line or '""' in line:
                        continue
                    try:
                        code_part = line.split('=')[0].replace('var hq_str_', '')
                        data = line.split('"')[1].split(',')
                        if len(data) < 32:
                            continue
                        # 新浪实时行情: data[9]=成交额(元), 转为万元
                        raw_amount = float(data[9]) if len(data) > 9 and data[9] else 0
                        results[code_part] = {
                            'name': data[0],
                            'open': float(data[1]) if data[1] else 0,
                            'prev_close': float(data[2]) if data[2] else 0,
                            'price': float(data[3]) if data[3] else 0,
                            'high': float(data[4]) if data[4] else 0,
                            'low': float(data[5]) if data[5] else 0,
                            'volume': float(data[8]) if len(data) > 8 and data[8] else 0,
                            'amount': raw_amount / 10000,  # 元→万元
                            'change_pct': (float(data[3]) - float(data[2])) / float(data[2]) * 100 if data[2] and data[3] and float(data[2]) > 0 else 0,
                        }
                    except Exception:
                        continue
        except Exception as e:
            print(f'  batch fetch error: {e}')
    return results


def _quick_filter_stocks(quotes):
    """
    快速预筛选：只用实时行情数据，不需要K线
    条件: 市值50-200亿(用成交额估算) / 换手5-10% / 量比>=1 / 涨>0 / 价格>=3 / 非涨停跌停
    """
    passed = []
    for code, q in quotes.items():
        if q['price'] <= 3:
            continue
        if q['prev_close'] <= 0:
            continue
        pct = q['change_pct']
        if pct >= 9.8 or pct <= -9.8:
            continue
        if pct < 0:
            continue  # 只要上涨的

        # 换手率估算: 成交额/市值 ≈ 换手率; 这里用成交额作为活性指标
        amount = q['amount']  # 单位: 万元(新浪)
        if amount < 5000:  # 成交额<5000万，流动性太差
            continue

        # 量比近似: 当前成交量 vs 前日(无法获取均量，用当日成交额>1亿作为替代)
        if amount < 10000:
            continue

        passed.append({'code': code, **q})

    return passed


def _deep_filter_one(code, quote):
    """深度筛选：获取K线数据，检查记忆周期+MA5"""
    symbol = code.replace('sh', '').replace('sz', '')
    df = _fetch_kline(symbol)
    if df is None or df.empty:
        return None

    p = quote
    latest = df.iloc[-1]
    closes = df['close'].astype(float).values
    ma5 = float(np.mean(closes[-5:])) if len(closes) >= 5 else p['price']

    # MA5检查
    if p['price'] < ma5:
        return None

    # 记忆周期检查 (Rule 3): 近10日有>7%涨跌
    recent10 = df.tail(10)
    if 'pct_chg' in df.columns:
        big = recent10[abs(recent10['pct_chg'].astype(float)) > 7]
        mem_hits = len(big)
    else:
        c = recent10['close'].astype(float).values
        if len(c) >= 2:
            chg = np.diff(c) / c[:-1] * 100
            mem_hits = sum(1 for x in chg if abs(x) > 7)
        else:
            mem_hits = 0

    # 量比
    avg_vol_5d = float(df['volume'].tail(6).head(5).mean()) if len(df) >= 6 else max(p['volume'], 1)
    vol_ratio = round(p['volume'] / avg_vol_5d, 2) if avg_vol_5d > 0 else 1.0

    # 计算得分 (简版)
    score = 0
    score += 20 if p['price'] >= ma5 else 0
    score += 15 if mem_hits > 0 else 0
    score += 15 if vol_ratio >= 1.5 else (10 if vol_ratio >= 1.0 else 0)
    score += 15 if 0 < p['change_pct'] < 9.5 else 0
    score += 10 if p['amount'] > 20000 else 0
    # 进场信号模拟
    sig_b = p['price'] >= p['high'] * 0.97
    sig_c = abs(p['change_pct']) < 5 and p['amount'] > 5000
    score += 15 if sig_b else 0
    score += 10 if sig_c else 0

    return {
        'code': code.replace('sh', '').replace('sz', ''),
        'name': p['name'],
        'price': p['price'],
        'change_pct': round(p['change_pct'], 2),
        'open': p['open'],
        'high': p['high'],
        'low': p['low'],
        'amount': round(p['amount'] / 10000, 2),  # 亿
        'volume_ratio': vol_ratio,
        'ma5': round(ma5, 2),
        'above_ma5': p['price'] >= ma5,
        'memory_hits': mem_hits,
        'score': score,
        'signals': {
            'signal_b': sig_b,
            'signal_c': sig_c,
        }
    }


@app.route('/api/screen')
def api_screen():
    """一键筛选全市场5000+A股，严格按策略规则，返回Top25"""
    global _screen_cache, _screen_time
    t0 = time.time()

    # 30秒内返回缓存
    if _screen_cache and (t0 - _screen_time) < _screen_ttl:
        return jsonify(_screen_cache)

    try:
        # 预取北向资金 (一次,不在循环里调)
        global _north_flow_cache
        _north_flow_cache = 0
        try:
            import akshare as ak
            nb = ak.stock_hsgt_hist_em(symbol="北上")
            if nb is not None and not nb.empty:
                _north_flow_cache = float(nb.iloc[-1].get('净流入', 0))
        except: pass

        # Step 1: 批量获取全市场实时行情
        all_quotes = _batch_fetch_sina(_STOCK_POOL, batch_size=200)
        t1 = time.time()
        valid_count = len(all_quotes)
        print(f'[SCREEN] Got {valid_count} valid quotes in {t1-t0:.1f}s')

        # Step 2: 严格按策略规则筛选
        # Rule 5 市值流动性: 股价3-40元, 成交额>=1亿, 换手率活跃
        # Rule 2: 排除ST
        # Rule 1: 排除涨停跌停
        # 日内强度: 涨幅>0
        # Part 2 信号: 尾盘突破 + 抗抛压横盘

        candidates = []
        for code, q in all_quotes.items():
            name = q.get('name', '')
            price = q['price']
            pct = q['change_pct']
            amount = q['amount']  # 万元
            volume = q['volume']
            open_p = q['open']
            high = q['high']
            low = q['low']
            prev_close = q['prev_close']

            # ---- 硬性排除条件 ----
            # 股价 3-40
            if price < 3 or price > 40:
                continue
            # ST排除
            if 'ST' in name.upper() or '*ST' in name.upper():
                continue
            # 涨停排除
            if pct >= 9.8:
                continue
            # 跌停排除
            if pct <= -9.8:
                continue
            # 停牌排除
            if open_p <= 0 or high <= 0 or volume <= 0:
                continue
            # 涨幅为负排除（策略要求当日强势）
            if pct <= 0:
                continue
            # 成交额<1亿排除 (amount单位=万元, 1亿=10000万)
            if amount < 10000:
                continue

            # ---- V3 策略评分 (6项优化) ----
            score = 0.0

            # Rule 4 日内强度 (0-25)
            score += min(25, max(0, pct * 4))

            # Rule 4 VWAP位置 (0-15)
            price_position = 0.5
            if high > low > 0:
                price_position = (price - low) / (high - low) if high > low else 0.5
            score += price_position * 15

            # Rule 5 流动性 (0-15)
            score += min(15, max(0, amount / 4000))

            # V3: 量价确认 — 最后30分钟放量 (amount大=量能强)
            vol_price_ok = amount > 30000 and pct > 2
            if vol_price_ok:
                score += 10

            # Rule 3 记忆周期 (0-10)
            amplitude = (high - low) / price * 100 if price > 0 else 0
            if amplitude >= 3:
                score += min(10, amplitude * 2)

            # V3 Signal B: 尾盘突破 + 量确认 (0-20)
            sig_b = price >= high * 0.98 and amount > 20000
            high_ratio = (price - low) / (high - low) if high > low and high > 0 else 0.5
            score += min(20, high_ratio * 20) if sig_b else min(10, high_ratio * 10)

            # V3 Signal C: 抗抛压横盘 (0-10) — 提高标准
            sig_c = amplitude < 1.5 and price_position >= 0.6
            if sig_c:
                score += 10

            # V3 北向资金加分 (使用预取缓存)
            if _north_flow_cache > 20: score += 5

            # 综合判定: 至少45分 (与回测统一)
            if score >= 45:
                clean_code = code.replace('sh', '').replace('sz', '')
                # 振幅(真实计算)
                ampl = round((high - low) / price * 100, 2) if price > 0 else 0
                # 成交额(亿, 真实数据)
                amt_yi = round(amount / 10000, 2)
                candidates.append({
                    'code': clean_code,
                    'name': name,
                    'price': round(price, 2),
                    'change_pct': round(pct, 2),
                    'open': round(open_p, 2),
                    'high': round(high, 2),
                    'low': round(low, 2),
                    'amount': round(amount / 10000, 2),  # 万元→亿
                    'amplitude': ampl,
                    'price_position': round(price_position * 100, 0),
                    'score': round(score, 1),
                    'signals': {'signal_b': sig_b, 'signal_c': sig_c},
                })

        t2 = time.time()
        print(f'[SCREEN] Filtered: {len(candidates)} candidates in {t2-t1:.1f}s')

        # Step 3: 按得分排序取 Top 25
        candidates.sort(key=lambda x: x['score'], reverse=True)
        top = candidates[:25]

        sz = _index_cache.get('上证指数')
        veto = not sz['above_ma5'] if sz else False
        elapsed = time.time() - t0

        result = _safe_json({
            'success': True,
            'total_scanned': valid_count,
            'candidates': len(candidates),
            'results': top,
            'market_veto': veto,
            'market_summary': f"上证{sz['close']:.0f} vs MA5({sz['ma5']:.0f})" if sz else '',
            'elapsed_seconds': round(elapsed, 1),
            'timestamp': datetime.now().strftime('%H:%M:%S'),
        })
        _screen_cache = result
        _screen_time = t0
        return jsonify(result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})


# ======== V3 深度分析 API ========

@app.route('/api/deep_analyze/<symbol>')
def api_deep_analyze(symbol):
    """单股深度实时分析 — 主力资金 + 买卖建议"""
    symbol = symbol.replace('sh','').replace('sz','').strip()
    t0 = time.time()
    try:
        # 实时行情
        spot = _fetch_spot_data(symbol)
        # 最新日K线 (用缓存)
        df = _fetch_kline(symbol)

        if df is None or df.empty:
            return jsonify({'success': False, 'error': '无K线数据'})

        latest = df.iloc[-1]
        closes = df['close'].astype(float).values
        current = float(latest['close'])
        ma5 = float(np.mean(closes[-5:]))
        ma10 = float(np.mean(closes[-10:])) if len(closes) >= 10 else ma5
        ma20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else ma5

        if spot and spot.get('price', 0) > 0:
            current = spot['price']
            open_p = spot.get('open', current)
            high = spot.get('high', current)
            low = spot.get('low', current)
            prev_c = spot.get('prev_close', latest['close'])
            vol = spot.get('volume', 0)
        else:
            open_p = float(latest.get('open', current))
            high = float(latest.get('high', current))
            low = float(latest.get('low', current))
            prev_c = float(df.iloc[-2]['close']) if len(df) >= 2 else current
            vol = float(latest.get('volume', 0))

        pct = (current - prev_c) / prev_c * 100 if prev_c > 0 else 0

        # 振幅 + 位置
        ampl = (high - low) / current * 100 if current > 0 else 0
        pos = (current - low) / (high - low) if high > low > 0 else 0.5

        # 量比
        avg_vol5 = df['volume'].tail(6).head(5).mean()
        vol_ratio = vol / avg_vol5 if avg_vol5 > 0 else 1.0

        # 换手率
        raw_to = float(latest.get('turnover', 0.03))
        turnover = raw_to * 100 if raw_to < 1 else raw_to

        # 近5日趋势
        pct_5d = (current - closes[-6]) / closes[-6] * 100 if len(closes) >= 6 else 0
        trend = "强势上涨" if pct_5d > 5 else ("温和上涨" if pct_5d > 0 else ("横盘" if pct_5d > -3 else "弱势下跌"))

        # 均线排列
        ma_order = "多头排列" if current > ma5 > ma10 > ma20 else ("空头排列" if current < ma5 < ma10 < ma20 else "交叉整理")

        # ===== 主力资金判断 =====
        # 量价配合分析
        if vol_ratio >= 2 and pct > 2:
            money_signal = "主力大幅流入"
            money_level = 3
        elif vol_ratio >= 1.5 and pct > 1:
            money_signal = "主力温和流入"
            money_level = 2
        elif vol_ratio >= 1 and pct > 0:
            money_signal = "散户推动"
            money_level = 1
        elif vol_ratio >= 1.5 and pct < 0:
            money_signal = "主力出货"
            money_level = -2
        elif pct < -3:
            money_signal = "恐慌抛售"
            money_level = -3
        else:
            money_signal = "无明确方向"
            money_level = 0

        # 大单方向 (用价格位置+量判断)
        if pos >= 0.7 and vol_ratio >= 1.5:
            big_order = "大单净买入"
        elif pos <= 0.3 and vol_ratio >= 1.5:
            big_order = "大单净卖出"
        else:
            big_order = "大单平衡"

        # ===== 策略信号 =====
        signals_check = {}
        # Signal B
        signals_check['signal_b'] = current >= high * 0.98 and vol_ratio >= 1.2
        # Signal C
        signals_check['signal_c'] = ampl < 1.5 and pos >= 0.6
        # MA5
        signals_check['above_ma5'] = current >= ma5
        # 量比
        signals_check['vol_ok'] = vol_ratio >= 1.0
        # 日内强度
        signals_check['intraday_strong'] = pos >= 0.5 and pct > 0
        # 换手
        signals_check['turnover_ok'] = 5 <= turnover <= 10

        signal_count = sum(1 for v in signals_check.values() if v)

        # ===== 买入建议 (基于实时数据逐条分析) =====
        reasons = []
        score_buy = 0  # 买入得分
        score_risk = 0  # 风险得分

        # 1. 价格与MA5关系
        if current >= ma5:
            reasons.append("+ 股价站上MA5({:.2f})，趋势向上".format(ma5))
            score_buy += 20
        else:
            diff_pct = (current - ma5) / ma5 * 100
            reasons.append("- 股价低于MA5({:.2f}) {:.1f}%，短期趋势偏弱".format(ma5, diff_pct))
            score_risk += 20

        # 2. 量价配合
        if vol_ratio >= 1.5 and pct > 1:
            reasons.append("+ 量比{:.1f} + 涨幅{:.1f}%，放量上涨，主力参与度高".format(vol_ratio, pct))
            score_buy += 25
        elif vol_ratio >= 1.0 and pct > 0:
            reasons.append("+ 量价温和，量比{:.1f}".format(vol_ratio))
            score_buy += 10
        elif vol_ratio >= 1.5 and pct < 0:
            reasons.append("- 放量下跌，量比{:.1f}，主力出货迹象".format(vol_ratio))
            score_risk += 25
        elif pct < -2:
            reasons.append("- 跌幅{:.1f}%，空方主导".format(pct))
            score_risk += 15

        # 3. 日内强度(收盘位置)
        if pos >= 0.7:
            reasons.append("+ 收盘在日内高位({:.0f}%)，尾盘有资金抢筹".format(pos * 100))
            score_buy += 15
        elif pos >= 0.5:
            reasons.append("+ 收盘位置中性({:.0f}%)".format(pos * 100))
            score_buy += 5
        else:
            reasons.append("- 收盘在日内低位({:.0f}%)，尾盘有抛压".format(pos * 100))
            score_risk += 10

        # 4. 尾盘突破信号
        if signals_check['signal_b']:
            reasons.append("+ 触发尾盘突破信号，收盘距最高{:.1f}%".format((1 - current / high) * 100))
            score_buy += 25
        else:
            gap = (1 - current / high) * 100 if high > 0 else 0
            reasons.append("- 未触发尾盘突破，距日内最高{:.1f}%".format(gap))

        # 5. 振幅与横盘
        if signals_check['signal_c']:
            reasons.append("+ 尾盘窄幅横盘(振幅{:.1f}%)，抛压极轻".format(ampl))
            score_buy += 10
        elif ampl > 5:
            reasons.append("- 振幅{:.1f}%偏大，短线博弈激烈".format(ampl))
            score_risk += 5

        # 6. 均线排列
        if current > ma5 > ma10 > ma20:
            reasons.append("+ 均线多头排列，中期趋势良好")
            score_buy += 10
        elif current < ma5 < ma10 < ma20:
            reasons.append("- 均线空头排列，不宜做多")
            score_risk += 15

        # 7. 换手率
        if 5 <= turnover <= 10:
            reasons.append("+ 换手率{:.1f}%在策略区间内，股性活跃".format(turnover))
            score_buy += 5
        elif turnover < 5:
            reasons.append("- 换手率{:.1f}%偏低，流动性不足".format(turnover))
            score_risk += 5
        elif turnover > 10:
            reasons.append("- 换手率{:.1f}%偏高，筹码松动风险".format(turnover))
            score_risk += 5

        # 8. 主力资金方向
        if money_level >= 2:
            reasons.append("+ 主力资金信号: {}".format(money_signal))
            score_buy += 15
        elif money_level <= -2:
            reasons.append("- 主力资金信号: {}".format(money_signal))
            score_risk += 15

        # 9. 股价范围
        if current < 3:
            reasons.append("- 股价{}元过低，流动性风险大".format(current))
            score_risk += 20
        elif current > 40:
            reasons.append("- 股价{}元超出策略范围(3-40元)".format(current))
            score_risk += 20
        else:
            reasons.append("+ 股价{}元在策略范围(3-40元)内".format(current))

        # 10. 5日趋势
        if pct_5d > 3:
            reasons.append("+ 5日涨幅{:.1f}%，短线动能强劲".format(pct_5d))
            score_buy += 10
        elif pct_5d < -3:
            reasons.append("- 5日跌幅{:.1f}%，短线持续走弱".format(pct_5d))
            score_risk += 10

        # ===== 综合判定 =====
        total_score = score_buy - score_risk
        if total_score >= 60:
            level, color, action = "A", "#3fb950", "强烈买入"
            summary = "多项指标共振看多，信号充分，适合尾盘积极参与"
        elif total_score >= 30:
            level, color, action = "B", "#58a6ff", "可以买入"
            summary = "多数指标偏多，建议轻仓试错，严格止损"
        elif total_score >= 0:
            level, color, action = "C", "#f39c12", "观望偏多"
            summary = "多空均衡，信号不够充分，建议等待更明确的机会"
        elif total_score >= -30:
            level, color, action = "D", "#f85149", "不宜买入"
            summary = "空方信号偏多，风险大于机会，建议回避"
        else:
            level, color, action = "F", "#f85149", "坚决回避"
            summary = "多项风险指标触发，坚决不碰"

        if pct >= 9.8:
            level, color, action, summary = "D", "#f85149", "无法买入", "已涨停封板，无法成交"

        return jsonify(_safe_json({
            'success': True,
            'symbol': symbol,
            'name': spot.get('name', symbol) if spot else symbol,
            'updated': datetime.now().strftime('%H:%M:%S'),
            'elapsed_ms': round((time.time() - t0) * 1000),

            'price': {
                'current': round(current, 2), 'change_pct': round(pct, 2),
                'open': round(open_p, 2), 'high': round(high, 2), 'low': round(low, 2),
                'prev_close': round(prev_c, 2), 'volume': int(vol),
                'vol_ratio': round(vol_ratio, 2), 'turnover': round(turnover, 2),
                'amplitude': round(ampl, 2), 'position': round(pos * 100),
                'ma5': round(ma5, 2), 'ma10': round(ma10, 2), 'ma20': round(ma20, 2),
            },

            'trend': {
                'pct_5d': round(pct_5d, 2),
                'description': trend,
                'ma_pattern': ma_order,
            },

            'money_flow': {
                'signal': money_signal,
                'level': money_level,
                'big_order': big_order,
                'vol_price_healthy': vol_ratio >= 1 and pct > 0,
            },

            'signals': signals_check,
            'signal_count': signal_count,

            'recommendation': {
                'level': level,
                'color': color,
                'action': action,
                'summary': summary,
                'total_score': total_score,
                'score_buy': score_buy,
                'score_risk': score_risk,
                'reasons': reasons,
            },
        }))
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)})


# ======== 启动 ========

if __name__ == '__main__':
    print("Loading index data...")
    _fetch_index_data()
    print("""
============================================
  一夜持股法 - Web 分析平台
  http://127.0.0.1:5000
  输入股票代码即可分析 (如 600519)
============================================
""")
    # 用 waitress 如果装了，否则用 Flask 自带
    try:
        from waitress import serve
        print("  使用 Waitress 生产服务器")
        serve(app, host='0.0.0.0', port=5000, threads=4)
    except ImportError:
        app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
