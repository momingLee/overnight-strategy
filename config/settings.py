"""
配置管理模块 — 加载和解析 YAML 配置文件
"""

import yaml
from pathlib import Path
from typing import Optional, Any


class Config:
    """统一配置对象，支持点号访问嵌套配置"""

    def __init__(self, data: dict):
        for key, value in data.items():
            if isinstance(value, dict):
                value = Config(value)
            setattr(self, key, value)

    def get(self, key: str, default: Any = None) -> Any:
        """安全获取配置项"""
        return getattr(self, key, default)

    def to_dict(self) -> dict:
        """转换为字典"""
        result = {}
        for key, value in self.__dict__.items():
            if isinstance(value, Config):
                result[key] = value.to_dict()
            else:
                result[key] = value
        return result


def load_config(config_path: Optional[str] = None) -> Config:
    """加载配置文件

    Args:
        config_path: YAML 配置文件路径，默认为同目录下的 settings.yaml

    Returns:
        Config 对象
    """
    if config_path is None:
        config_path = Path(__file__).parent / "settings.yaml"

    path = Path(config_path)
    if not path.exists():
        print(f"配置文件不存在: {path}，使用默认配置")
        return _default_config()

    with open(path, 'r', encoding='utf-8') as f:
        data = yaml.safe_load(f)

    if data is None:
        print("配置文件为空，使用默认配置")
        return _default_config()

    return Config(data)


def _default_config() -> Config:
    """返回默认配置（含V1和V2完整参数）"""
    default_data = {
        'data': {
            'request_interval': 1.0,
            'max_retries': 3,
            'timeout': 30,
            'cache': {
                'db_path': './outputs/cache/market_data.db',
                'calendar_path': './outputs/cache/trading_calendar.json',
                'daily_cache_days': 365,
                'minute_cache_days': 30,
            }
        },
        'strategy': {
            'version': 2,
            'filters': {
                'min_volume_ratio': 1.5,
                'min_tail_lift_pct': 1.0,
                'min_turnover_rate_pct': 3.0,
                'exclude_limit_up': True,
                'exclude_limit_down': True,
                'min_market_cap_billion': 2.0,
                'max_market_cap_billion': 500.0,
                'exclude_st': True,
                'min_price': 3.0,
            },
            'market': {
                'min_advance_decline_ratio': 1.0,
                'max_market_decline_pct': 2.0,
                'require_sector_up': True,
                'sector_top_n': 10,
            },
            'ranking': {
                'weights': {
                    'volume_ratio': 0.25,
                    'tail_lift_magnitude': 0.25,
                    'turnover_rate': 0.20,
                    'sector_strength': 0.15,
                    'market_cap_fit': 0.15,
                },
                'max_positions': 5,
                'position_sizing': 'equal',
                'single_position_pct': 0.20,
            },
            # ===== V2 策略参数 =====
            'v2': {
                # Rule 1: 大盘环境 (一票否决)
                'market_environment': {
                    'index_codes': ["000001", "399006"],
                    'require_above_ma5': True,
                    'require_volume_above_ma5': True,
                },
                # Rule 2: 板块效应
                'sector_effect': {
                    'top_n': 20,
                    'min_limit_up_in_sector': 2,
                    'exclude_ipo_one_line': True,
                    'no_lone_wolves': True,
                },
                # Rule 3: 个股股性
                'stock_character': {
                    'memory_window_days': 10,
                    'require_recent_limit_up': True,
                    'detect_failed_limit_up': True,
                },
                # Rule 4: 日内强度
                'intraday_strength': {
                    'gain_percentile_threshold': 0.85,
                    'require_above_vwap': True,
                    'vwap_approximation': 'typical',
                },
                # Rule 5: 市值流动性
                'market_cap_liquidity': {
                    'min_market_cap_billion': 50,
                    'max_market_cap_billion': 200,
                    'min_turnover_rate_pct': 5.0,
                    'max_turnover_rate_pct': 10.0,
                    'min_volume_ratio': 1.0,
                },
                # Part 2: 尾盘进场信号
                'entry_signals': {
                    'require_any_n': 2,
                    'signal_a': {
                        'enabled': True,
                        'observation_start': '14:30',
                        'large_order_threshold_hands': 500,
                        'require_net_inflow': True,
                    },
                    'signal_b': {
                        'enabled': True,
                        'breakout_time_start': '14:40',
                        'morning_high_lookback_start': '09:30',
                        'volume_confirmation_multiplier': 1.2,
                        'pattern_check': 'rounded_flat',
                    },
                    'signal_c': {
                        'enabled': True,
                        'max_amplitude_pct': 1.0,
                        'consolidation_start': '14:30',
                        'dip_volume_percentile': 20,
                    },
                },
                # Part 3: 次日离场
                'exit': {
                    'auction_observation': {
                        'enabled': True,
                        'observation_start': '09:15',
                        'observation_end': '09:20',
                        'danger_patterns': ['limit_down_test_order', 'high_open_then_cancel'],
                    },
                    'graded_execution': {
                        'flat_open_threshold': 0.5,
                        'normal_high_low': 0.5,
                        'normal_high_high': 3.0,
                        'super_high_threshold': 3.0,
                        'hold_to_time': '09:35',
                        'trailing_stop_to_open': True,
                    },
                    'hold_condition': {
                        'require_first_to_limit_up': True,
                        'order_book_ratio': 10,
                        'fallback_on_break': True,
                    },
                },
                # V2 排序权重
                'ranking': {
                    'weights': {
                        'sector_strength': 0.20,
                        'memory_cycle': 0.15,
                        'intraday_strength': 0.20,
                        'volume_ratio': 0.15,
                        'turnover_fit': 0.15,
                        'market_cap_fit': 0.10,
                        'entry_signal_count': 0.05,
                    },
                    'max_positions': 3,
                    'position_sizing': 'rank_weighted',
                    'single_position_pct': 0.25,
                },
            },
        },
        'execution': {
            'backtest': {
                'initial_capital': 100000.0,
                'start_date': '2023-01-01',
                'end_date': '2025-12-31',
                'commission_rate': 0.00025,
                'stamp_duty_rate': 0.001,
                'min_commission': 5.0,
                'slippage': 0.001,
                'backtest_confidence_penalty': 0.85,
            },
            'realtime': {
                'entry_time': '14:55',
                'exit_time': '09:35',
                'auction_start': '09:15',
                'auction_end': '09:20',
                'polling_intervals': {
                    'pre_scan': 60,
                    'entry_window': 30,
                    'final_window': 10,
                    'auction': 10,
                },
                'notify': {
                    'enabled': True,
                    'method': 'console',
                },
                'dry_run': True,
            }
        },
        'risk': {
            'max_daily_loss_pct': 3.0,
            'max_consecutive_losses': 3,
            'max_single_position_pct': 0.20,
            'position_lot_size': 100,
            'allow_chinext': True,
            'allow_star_market': False,
            'allow_bse': False,
            'min_position_value': 5000.0,
        },
        'output': {
            'log_dir': './outputs/logs',
            'report_dir': './outputs/reports',
            'log_level': 'INFO',
            'save_signals': True,
            'save_trades': True,
            'generate_charts': True,
        }
    }
    return Config(default_data)
