"""
模拟券商模块
模拟A股交易执行（T+1、手续费、印花税、涨跌停限制）
"""

from datetime import date, datetime
from typing import Optional
from dataclasses import dataclass, field

from risk.rules import AShareRules


@dataclass
class Position:
    """持仓记录"""
    symbol: str
    name: str
    buy_date: date
    buy_price: float
    shares: int
    buy_amount: float  # 买入总金额（含手续费）


@dataclass
class Trade:
    """交易记录"""
    symbol: str
    name: str
    direction: str  # 'buy' | 'sell'
    date: date
    price: float
    shares: int
    amount: float
    commission: float
    stamp_duty: float
    pnl: Optional[float] = None  # 仅卖出时有
    pnl_pct: Optional[float] = None


@dataclass
class Account:
    """账户状态"""
    initial_capital: float
    cash: float
    positions: list = field(default_factory=list)
    total_commission: float = 0.0
    total_stamp_duty: float = 0.0
    trades: list = field(default_factory=list)

    @property
    def position_value(self) -> float:
        """持仓市值 (按成本价估算, 因未跟踪市价)"""
        return sum(p.shares * p.buy_price for p in self.positions)

    @property
    def equity(self) -> float:
        """总权益 = 现金 + 持仓市值"""
        return self.cash + self.position_value

    @property
    def total_return(self) -> float:
        """总收益率"""
        return (self.equity - self.initial_capital) / self.initial_capital


class SimulatedBroker:
    """模拟券商，处理交易执行和费用计算"""

    def __init__(self, config, calendar):
        exe_cfg = config.execution.backtest
        self.commission_rate = float(exe_cfg.commission_rate)
        self.stamp_duty_rate = float(exe_cfg.stamp_duty_rate)
        self.min_commission = float(exe_cfg.min_commission)
        self.slippage = float(exe_cfg.slippage)
        self.lot_size = int(config.risk.position_lot_size)
        self.calendar = calendar

        # A股权限
        self.allow_chinext = bool(config.risk.get('allow_chinext', True))
        self.allow_star = bool(config.risk.get('allow_star_market', False))
        self.allow_bse = bool(config.risk.get('allow_bse', False))

        self.account: Optional[Account] = None

    def reset(self, initial_capital: float):
        """重置账户"""
        self.account = Account(
            initial_capital=initial_capital,
            cash=initial_capital,
        )

    def can_buy(self, symbol: str, price: float, shares: int) -> tuple:
        """
        检查是否可以买入

        Returns:
            (can_buy: bool, reason: str)
        """
        if self.account is None:
            return False, "账户未初始化"

        if price <= 0:
            return False, "价格无效"

        if shares < self.lot_size:
            return False, f"最少买入 {self.lot_size} 股"

        # 检查是否是100的整数倍
        if shares % self.lot_size != 0:
            return False, f"买入数量必须是 {self.lot_size} 的整数倍"

        # 检查资金
        commission = max(price * shares * self.commission_rate, self.min_commission)
        estimated_cost = price * shares + commission
        if estimated_cost > self.account.cash:
            return False, f"资金不足 (需要 {estimated_cost:.2f}, 可用 {self.account.cash:.2f})"

        # 检查板块权限
        board = AShareRules.get_board(symbol)
        if board == 'gem' and not self.allow_chinext:
            return False, "不允许创业板"
        if board == 'star' and not self.allow_star:
            return False, "不允许科创板"
        if board == 'bse' and not self.allow_bse:
            return False, "不允许北交所"

        # 检查是否已有该股持仓（T+1下同一天不能重复买同一只）
        for pos in self.account.positions:
            if pos.symbol == symbol:
                return False, "已有该股持仓"

        return True, "OK"

    def can_sell(self, symbol: str, buy_date: date, sell_date: date) -> tuple:
        """
        检查是否可以卖出（T+1规则）

        Returns:
            (can_sell: bool, reason: str)
        """
        if self.account is None:
            return False, "账户未初始化"

        # T+1 检查
        next_td = self.calendar.next_trading_day(buy_date)
        if sell_date < next_td:
            return False, f"T+1限制: 买入日{buy_date}, 最早卖出日{next_td}, 当前{sell_date}"

        return True, "OK"

    def execute_buy(
        self, symbol: str, name: str, price: float, shares: int,
        trade_date: Optional[date] = None
    ) -> Optional[Trade]:
        """
        执行买入

        Args:
            symbol: 股票代码
            name: 股票名称
            price: 买入价格
            shares: 买入股数
            trade_date: 交易日期

        Returns:
            Trade 对象，失败返回 None
        """
        if trade_date is None:
            trade_date = date.today()

        # 滑点（买入价略高）
        exec_price = price * (1 + self.slippage)

        # 计算费用
        amount = exec_price * shares
        commission = max(amount * self.commission_rate, self.min_commission)
        total_cost = amount + commission

        # 检查资金
        if total_cost > self.account.cash:
            print(f"  ⚠️  {symbol} {name} 买入失败: 资金不足")
            return None

        # 扣款
        self.account.cash -= total_cost
        self.account.total_commission += commission

        # 记录持仓
        position = Position(
            symbol=symbol,
            name=name,
            buy_date=trade_date,
            buy_price=exec_price,
            shares=shares,
            buy_amount=total_cost,
        )
        self.account.positions.append(position)

        # 记录交易
        trade = Trade(
            symbol=symbol,
            name=name,
            direction='buy',
            date=trade_date,
            price=exec_price,
            shares=shares,
            amount=amount,
            commission=commission,
            stamp_duty=0,
        )
        self.account.trades.append(trade)

        print(f"  ✅ 买入 {name}({symbol}) {shares}股 @ {exec_price:.2f} "
              f"金额 {amount:.2f} 佣金 {commission:.2f}")

        return trade

    def execute_sell(
        self, position: Position, price: float, trade_date: Optional[date] = None
    ) -> Optional[Trade]:
        """
        执行卖出

        Args:
            position: 持仓对象
            price: 卖出价格
            trade_date: 交易日期

        Returns:
            Trade 对象，失败返回 None
        """
        if trade_date is None:
            trade_date = date.today()

        # 滑点（卖出价略低）
        exec_price = price * (1 - self.slippage)

        # 计算费用
        amount = exec_price * position.shares
        commission = max(amount * self.commission_rate, self.min_commission)
        stamp_duty = amount * self.stamp_duty_rate  # 印花税仅卖出收取
        net_proceeds = amount - commission - stamp_duty

        # 计算盈亏
        pnl = net_proceeds - position.buy_amount
        pnl_pct = pnl / position.buy_amount * 100 if position.buy_amount > 0 else 0

        # 入账
        self.account.cash += net_proceeds
        self.account.total_commission += commission
        self.account.total_stamp_duty += stamp_duty

        # 移除持仓
        self.account.positions = [p for p in self.account.positions if p.symbol != position.symbol]

        # 记录交易
        trade = Trade(
            symbol=position.symbol,
            name=position.name,
            direction='sell',
            date=trade_date,
            price=exec_price,
            shares=position.shares,
            amount=amount,
            commission=commission,
            stamp_duty=stamp_duty,
            pnl=pnl,
            pnl_pct=pnl_pct,
        )
        self.account.trades.append(trade)

        sign = '+' if pnl >= 0 else ''
        print(f"  ✅ 卖出 {position.name}({position.symbol}) {position.shares}股 @ {exec_price:.2f} "
              f"盈亏 {sign}{pnl:.2f} ({pnl_pct:+.2f}%)")

        return trade

    def execute_buy_batch(
        self, signals: list[dict], trade_date: Optional[date] = None,
        max_single_pct: float = 0.20
    ) -> list[Trade]:
        """
        批量执行买入

        Args:
            signals: 买入信号列表
            trade_date: 交易日期
            max_single_pct: 单票最大仓位

        Returns:
            成功执行的交易列表
        """
        if trade_date is None:
            trade_date = date.today()

        trades = []
        total_equity = self.account.equity

        for signal in signals:
            symbol = signal.get('symbol', '')
            name = signal.get('name', '')
            price = signal.get('price', 0)
            target_shares = signal.get('target_shares', 0)

            if target_shares <= 0:
                # 根据仓位计算
                max_value = total_equity * max_single_pct / max(1, len(signals))
                shares = int(max_value / price / self.lot_size) * self.lot_size
            else:
                shares = target_shares

            if shares < self.lot_size:
                continue

            # 检查买入条件
            can, reason = self.can_buy(symbol, price, shares)
            if not can:
                print(f"  ⚠️  {symbol} {name}: {reason}")
                continue

            # 执行
            trade = self.execute_buy(symbol, name, price, shares, trade_date)
            if trade:
                trades.append(trade)

            # 如果资金用完则停止
            if self.account.cash < price * self.lot_size:
                break

        return trades

    def execute_sell_all(self, positions: list, prices: dict, trade_date: date) -> list[Trade]:
        """
        卖出所有持仓 (次日早盘)

        Args:
            positions: 持仓列表
            prices: {symbol: price} 价格字典
            trade_date: 交易日期

        Returns:
            交易列表
        """
        trades = []
        for pos in positions:
            sell_price = prices.get(pos.symbol, pos.buy_price)

            # T+1 检查
            can, reason = self.can_sell(pos.symbol, pos.buy_date, trade_date)
            if not can:
                print(f"  ⚠️  {pos.symbol} {pos.name}: {reason} — 延期卖出")
                # T+1 不满足通常不会发生（一夜持股天然满足），但不放心的检查
                continue

            # 跌停检查
            try:
                if AShareRules.is_at_limit_down(pos.symbol, sell_price, pos.buy_price):
                    print(f"  ⚠️  {pos.symbol} {pos.name}: 跌停无法卖出，次日继续持有")
                    # 更新买入日期为下个交易日（模拟跌停排队）
                    pos.buy_date = self.calendar.next_trading_day(trade_date)
                    continue
            except Exception:
                pass

            trade = self.execute_sell(pos, sell_price, trade_date)
            if trade:
                trades.append(trade)

        return trades

    def execute_graded_sell(
        self, position: Position, open_price: float, current_price: float,
        trade_date: date, exit_action=None
    ) -> Optional[Trade]:
        """
        V2 分级卖出 (Rule 10)

        Args:
            position: 持仓
            open_price: 开盘价
            current_price: 当前价格
            trade_date: 交易日期
            exit_action: ExitAction 枚举值 (来自 ExitStrategy)

        Returns:
            Trade 或 None
        """
        if exit_action is None:
            # 降级：直接卖
            return self.execute_sell(position, current_price, trade_date)

        from strategy.exit_strategy import ExitAction

        if exit_action == ExitAction.SELL_AT_LIMIT_DOWN:
            # Rule 9: 挂跌停价
            sell_price = open_price * 0.9  # 近似跌停价
            return self.execute_sell(position, sell_price, trade_date)

        elif exit_action == ExitAction.SELL_IMMEDIATELY:
            # 市价卖出
            return self.execute_sell(position, current_price if current_price > 0 else open_price, trade_date)

        elif exit_action == ExitAction.HOLD_FOR_LIMIT_UP:
            # Rule 11: 格局持有
            print(f"  🔒 {position.name}({position.symbol}) 格局持有，博弈连板")
            return None  # 不卖出

        elif exit_action == ExitAction.TRAILING_STOP:
            # 移动止盈
            stop_price = open_price  # 止损移到开盘价
            if current_price > stop_price:
                print(f"  📈 {position.name}({position.symbol}) 移动止盈，止损={stop_price:.2f}")
                return None  # 继续持有
            else:
                return self.execute_sell(position, current_price, trade_date)

        else:
            # HOLD_TO_0935 或其他
            return self.execute_sell(position, current_price, trade_date)

    def update_position_stop(self, position: Position, stop_price: float):
        """更新持仓止盈价 (Rule 10 移动止盈)"""
        position._stop_price = stop_price

    def get_account_summary(self) -> dict:
        """获取账户摘要"""
        if self.account is None:
            return {}

        buys = [t for t in self.account.trades if t.direction == 'buy']
        sells = [t for t in self.account.trades if t.direction == 'sell']

        return {
            'initial_capital': self.account.initial_capital,
            'equity': self.account.equity,
            'cash': self.account.cash,
            'position_value': self.account.position_value,
            'total_return': self.account.total_return,
            'total_commission': self.account.total_commission,
            'total_stamp_duty': self.account.total_stamp_duty,
            'num_buys': len(buys),
            'num_sells': len(sells),
            'num_positions': len(self.account.positions),
        }
