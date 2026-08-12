"""
回测交易明细报告 —— 由成交序列推导现金、佣金与盈亏（框架层，策略无关）。

get_trades_detail_df 输出字段说明：
    trade_id: 成交编号
    datetime: 成交时间
    symbol: 标的代码
    direction: long=买入 / short=卖出
    action: 买入 / 卖出（中文，便于阅读）
    price: 成交单价（含滑点，不含佣金）
    volume: 成交数量
    amount: 成交额 = price × volume（名义金额，不含佣金）
    commission: 本笔单边佣金
    cash_before: 成交前账户现金（含历史佣金）
    cash_after: 成交后账户现金（含本笔成交额与佣金）
    total_value: 成交后总资产 = 现金 + 持仓市值（按成交价 mark）
    cost_price: [仅卖出] FIFO 配对买入加权均价（含滑点，不含佣）
    buy_commission: [仅卖出] FIFO 配对买入佣金合计
    sell_commission: [仅卖出] 本笔卖出佣金（同 commission 列）
    pnl_gross: [仅卖出] 价差毛盈亏，不含佣金
    pnl: [仅卖出] 净盈亏 = pnl_gross - buy_commission - sell_commission（仅对已 FIFO 配对数量；无配对时为 null）
    pnl_pct: [仅卖出] 净收益率 = pnl / (配对成本 + buy_commission)
"""
from __future__ import annotations

from typing import Dict, List, Optional, TYPE_CHECKING

import pandas as pd

from alphaQuantSystem.core import TradeData, Direction

if TYPE_CHECKING:
    from alphaQuantSystem.backtest.commission import CommissionModel


# Excel「交易记录说明」sheet 与文档引用
TRADE_DETAIL_FIELD_DOCS: Dict[str, str] = {
    'trade_id': '成交编号',
    'datetime': '成交时间',
    'symbol': '标的代码',
    'direction': 'long=买入 / short=卖出',
    'action': '买入 / 卖出',
    'price': '成交单价（含滑点，不含佣金）',
    'volume': '成交数量',
    'amount': '成交额 = price×volume（不含佣金）',
    'commission': '本笔单边佣金',
    'cash_before': '成交前账户现金（含历史佣金）',
    'cash_after': '成交后账户现金（含本笔成交额与佣金）',
    'total_value': '成交后总资产 = 现金 + 持仓市值',
    'cost_price': '[仅卖出] FIFO 配对买入加权均价',
    'buy_commission': '[仅卖出] FIFO 配对买入佣金合计',
    'sell_commission': '[仅卖出] 本笔卖出佣金',
    'pnl_gross': '[仅卖出] 价差毛盈亏（不含佣金）',
    'pnl': '[仅卖出] 净盈亏（已扣买卖双边手续费；无 FIFO 配对时不计）',
    'pnl_pct': '[仅卖出] 净收益率 = pnl / 成本基数',
}


def _lot_buy_commission(lot: dict, matched_volume: float) -> float:
    orig = float(lot.get('original_volume') or lot['volume'] or 0)
    if orig <= 0:
        return 0.0
    return float(lot.get('commission', 0) or 0) * matched_volume / orig


def build_trade_detail_records(
    trades: List[TradeData],
    initial_cash: float,
    commission_model: Optional['CommissionModel'] = None,
) -> List[dict]:
    """
    按成交时间顺序重建现金、持仓，并计算每笔交易的佣金与盈亏。
    买入侧 FIFO 队列与 analyze.metrics 平仓 round-trip 口径一致。
    """
    if not trades:
        return []

    balance = float(initial_cash)
    # symbol -> [{price, volume, original_volume, commission}]
    fifo_lots: Dict[str, List[dict]] = {}
    # symbol -> {volume, current_price}
    positions: Dict[str, dict] = {}

    records: List[dict] = []

    for trade in trades:
        symbol = trade.symbol
        amount = trade.price * trade.volume
        commission = (
            float(commission_model.calculate(trade))
            if commission_model is not None
            else 0.0
        )
        is_buy = trade.direction == Direction.LONG
        cash_before = balance

        if is_buy:
            balance -= amount + commission
            fifo_lots.setdefault(symbol, []).append({
                'price': trade.price,
                'volume': trade.volume,
                'original_volume': trade.volume,
                'commission': commission,
            })
            pos = positions.setdefault(symbol, {'volume': 0.0, 'current_price': trade.price})
            old_val = pos['volume'] * pos.get('avg_price', trade.price)
            pos['volume'] += trade.volume
            pos['avg_price'] = (old_val + amount) / pos['volume'] if pos['volume'] > 0 else trade.price
            pos['current_price'] = trade.price
        else:
            balance += amount - commission
            sell_remaining = trade.volume
            gross_pnl = 0.0
            matched_buy_commission = 0.0
            cost_amount = 0.0
            matched_volume = 0.0

            for lot in fifo_lots.get(symbol, []):
                if sell_remaining <= 0:
                    break
                if lot['volume'] <= 0:
                    continue
                matched = min(sell_remaining, lot['volume'])
                gross_pnl += (trade.price - lot['price']) * matched
                matched_buy_commission += _lot_buy_commission(lot, matched)
                cost_amount += lot['price'] * matched
                matched_volume += matched
                lot['volume'] -= matched
                sell_remaining -= matched

            fifo_lots[symbol] = [lot for lot in fifo_lots.get(symbol, []) if lot['volume'] > 0]

            pos = positions.get(symbol)
            if pos:
                pos['volume'] = max(0.0, pos['volume'] - trade.volume)
                pos['current_price'] = trade.price
                if pos['volume'] <= 0:
                    positions.pop(symbol, None)

            cost_price = cost_amount / matched_volume if matched_volume > 0 else 0.0
            pnl_gross = gross_pnl
            if matched_volume > 0 and trade.volume > 0:
                matched_sell_commission = commission * matched_volume / trade.volume
                round_trip_commission = matched_buy_commission + matched_sell_commission
                pnl = pnl_gross - round_trip_commission
                cost_basis = cost_amount + matched_buy_commission
                pnl_pct = pnl / cost_basis if cost_basis > 0 else 0.0
            else:
                # 无 FIFO 配对买入时不强行记 round-trip 盈亏，避免 pnl 显示为负佣金
                pnl = None
                pnl_pct = None

        cash_after = balance
        market_value = sum(
            p['volume'] * p['current_price'] for p in positions.values()
        )
        total_value = cash_after + market_value

        row = {
            'trade_id': trade.trade_id,
            'datetime': trade.event_time,
            'symbol': symbol,
            'direction': trade.direction.value,
            'action': '买入' if is_buy else '卖出',
            'price': trade.price,
            'volume': trade.volume,
            'amount': amount,
            'commission': commission,
            'cash_before': cash_before,
            'cash_after': cash_after,
            'total_value': total_value,
        }

        if not is_buy:
            row.update({
                'cost_price': cost_price,
                'buy_commission': matched_buy_commission,
                'sell_commission': commission,
                'pnl_gross': pnl_gross,
                'pnl': pnl,
                'pnl_pct': pnl_pct,
            })

        records.append(row)

    return records


def build_trade_detail_df(
    trades: List[TradeData],
    initial_cash: float,
    commission_model: Optional['CommissionModel'] = None,
) -> pd.DataFrame:
    """交易明细 DataFrame，无成交时返回空表。"""
    records = build_trade_detail_records(trades, initial_cash, commission_model)
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records)
