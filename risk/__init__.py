"""
风控模块 —— 六层架构标准化实现

六层架构（严格按 Skill 第三节）：
  Layer 1  data_layer    - 数据接入层：行情/资金/持仓标准化
  Layer 2  calc_layer    - 风控计算层：纯指标计算，带时间戳快照
  Layer 3  rule_engine   - 规则引擎层：多规则 + 优先级仲裁 + 状态幂等
  Layer 4  exec_layer    - 执行调度层：统一动作池（拒单/平仓/锁仓/告警）
  Layer 5  scene_layer   - 场景适配层：回测/实盘差异隔离
  Layer 6  log_layer     - 日志监控层：全量事件落库、可查询、可复盘

规则插件（policies/）：
  price_risk      - L3 止盈止损/追踪/阶梯
  drawdown_risk   - L2 回撤/亏损/连续亏损
  position_risk   - L4 仓位/手数/频率
  abnormal_risk   - L1 滑点/跳空/涨跌停
  time_risk       - L2 持仓超时 + L4 交易时段

状态机：
  NORMAL → LIMIT_OPEN → PAUSE_TRADE → LOCKED（单向升级）
"""
from .risk_limits import RiskLimits
from .presets import RISK_DISABLED, isolated_risk, production_risk
from .risk_gateway import RiskGateway
from .state_machine import RiskStateMachine, RiskState
from .data_layer import DataLayer, RiskSnapshot
from .calc_layer import CalcLayer, IndicatorSnapshot
from .rule_engine import RuleEngine
from .exec_layer import ExecLayer
from .scene_layer import SceneAdapter
from .log_layer import LogLayer
from .policies import RiskEvent, RiskAction, BaseRiskPolicy
from .policies.daily_drop_risk import DailyDropRiskPolicy

__all__ = [
    # 对外主接口（兼容旧版）
    'RiskLimits',
    'RISK_DISABLED',
    'isolated_risk',
    'production_risk',
    'RiskGateway',
    # 状态机
    'RiskStateMachine',
    'RiskState',
    # 六层架构（供高级用户定制）
    'DataLayer',
    'RiskSnapshot',
    'CalcLayer',
    'IndicatorSnapshot',
    'RuleEngine',
    'ExecLayer',
    'SceneAdapter',
    'LogLayer',
    # 规则插件基类
    'RiskEvent',
    'RiskAction',
    'BaseRiskPolicy',
    'DailyDropRiskPolicy',
]
