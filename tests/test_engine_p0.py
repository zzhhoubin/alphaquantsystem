"""P0 修复回归：日终结算去重、RiskContext 填充。"""
from __future__ import annotations

from datetime import datetime

from alphaQuantSystem.core import BarData, Direction, SignalData
from alphaQuantSystem.engine.engine import StrategyEngine
from alphaQuantSystem.engine.signal_pipeline import SignalPipeline
from alphaQuantSystem.backtest.report import BacktestReporter
from alphaQuantSystem.services.account import AccountService
from alphaQuantSystem.services.position import PositionService
from alphaQuantSystem.services.risk import RiskService, RiskResult


def test_settle_backtest_day_updates_all_symbols_once():
  engine = StrategyEngine()
  reporter = BacktestReporter()
  engine._account_svc = AccountService(1_000_000)
  engine._position_svc = PositionService()

  day = datetime(2024, 1, 2)
  bars = [
      BarData(symbol="AAA", open=10, high=11, low=9, close=10.5, volume=1000, event_time=day, interval="D"),
      BarData(symbol="BBB", open=20, high=21, low=19, close=20.5, volume=2000, event_time=day, interval="D"),
      BarData(symbol="CCC", open=30, high=31, low=29, close=30.5, volume=3000, event_time=day, interval="D"),
  ]

  engine._settle_backtest_day(reporter, bars, day.date())

  assert len(reporter._account_snapshots) == 1
  snap = reporter._account_snapshots[0]
  assert snap.date == day.date()
  assert snap.market_value == 0.0


def test_build_risk_context_uses_account_and_positions():
  captured = {}

  class _Strategy:
      def __init__(self):
          from alphaQuantSystem.core.context import StrategyContext
          self.ctx = StrategyContext()
          self.ctx._position_svc = PositionService()
          self.ctx._account_svc = AccountService(500_000)

  strategy = _Strategy()
  pipeline = SignalPipeline(execution=_NoopExecution(), risk=_CaptureRisk(captured))
  pipeline.set_strategy(strategy)

  bar = BarData(
      symbol="600000", open=10, high=11, low=9, close=10.0, volume=1000,
      event_time=datetime(2024, 1, 2), interval="D",
  )
  ctx = pipeline._build_risk_context(
      SignalData(strategy_id="t", symbol="600000", direction=Direction.LONG, volume=100, price=10.0),
      bar,
  )

  assert ctx.available_cash == 500_000
  assert ctx.total_value == 500_000
  assert ctx.current_price == 10.0
  assert ctx.total_positions_count == 0


class _NoopExecution:
  def execute(self, signal, *, bar=None):
      return None


class _CaptureRisk:
  def __init__(self, store):
      self._store = store

  def evaluate(self, signal, ctx):
      self._store["ctx"] = ctx
      return RiskResult(True)
