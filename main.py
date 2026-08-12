# alphaQuantSystem/main.py
"""Dual EMA strategy — entry point

Usage:
    python main.py              # backtest (default)
    python main.py backtest     # backtest
    python main.py live         # live trading
    python main.py backtest --debug              # 逐步执行日志（每步 STEP 编号）
    python main.py backtest --debug-trades-only  # 仅成交/信号/风控关键步骤（推荐）
    python main.py --debug                       # 同上

推荐解释器（含 QMT）:
    D:/quant/venv/Scripts/python.exe alphaQuantSystem/main.py backtest --debug-trades-only
"""
from __future__ import annotations
import sys

from alphaQuantSystem import App
from alphaQuantSystem.monitor.logger import setup_logger
from alphaQuantSystem.monitor.trace import enable as enable_trace
# from alphaQuantSystem.examples.dual_ema import DualEmaStrategy
from alphaQuantSystem.examples.wufu_local_v1 import WufuLocalV1Strategy, build_wufu_symbols

from alphaQuantSystem.risk.presets import isolated_risk

# QMT 资金账号：模拟盘 55012491 有资金；8882405326 当前为空。
# is_live=True  → ACCOUNT_REAL (8882405326)
# is_live=False → ACCOUNT_SIMULATED (55012491)
QMT_USE_REAL_ACCOUNT = False

INITIAL_CASH = 1_000_000
BACKTEST_START = "20251001"
BACKTEST_END = "20251210"
# 手续费: 万0.5 双向 (ETF免印花税)
COMMISSION_RATE = 0.00005
# 滑点: 千分之一
SLIPPAGE = 0.001
# 均线预热需要 slow_period 根 bar
WARMUP_BARS = 20

_DEBUG_FLAGS = ("--debug", "-d", "--debug-trades-only", "-t")


def _parse_args(argv: list[str]) -> tuple[str, bool, bool]:
    """解析 mode、--debug、--debug-trades-only。"""
    full_debug = "--debug" in argv or "-d" in argv
    trades_only = "--debug-trades-only" in argv or "-t" in argv
    debug = full_debug or trades_only
    positional = [a for a in argv if a not in _DEBUG_FLAGS]
    mode = positional[0] if positional else "backtest"
    return mode, debug, trades_only and not full_debug


def enable_debug_mode(*, trades_only: bool = False) -> None:
    """逐步 STEP 追踪；全量 --debug 时额外打开 DEBUG 级别。"""
    setup_logger(level="INFO" if trades_only else "DEBUG")
    enable_trace(trades_only=trades_only)


def build_app(mode: str) -> App:
    """构建 App 实例，回测/实盘通过 mode 参数切换。"""
    app = App()

    if mode == "live":
        app.use_qmt(is_live=QMT_USE_REAL_ACCOUNT)
        app.with_data(sources=["qmt"])
    else:
        app.use_qmt(is_live=False)
        app.with_data(sources=["qmt", "akshare"])

    app.with_trading(commission_rate=COMMISSION_RATE, slippage=SLIPPAGE)

    # app.add_strategy(
    #     DualEmaStrategy,
    #     symbols=[SYMBOL],
    #     warmup_bars=WARMUP_BARS,
    #     # 仅启用 1% 固定止损，其余规则关闭（见 risk/presets.py）
    #     risk=isolated_risk(max_drawdown=0.05),
    # )

    app.add_strategy(
        WufuLocalV1Strategy,
        symbols=build_wufu_symbols(),
        period="D",
        warmup_bars=30,
        schedule={"10:33": "morning_routine", "10:46": "afternoon_routine", "18:10": "reset_daily_flags"},
        risk={"enabled": False}
    )

    return app


def run_backtest() -> None:
    app = build_app("backtest")
    result = app.run(
        mode="backtest",
        start=BACKTEST_START,
        end=BACKTEST_END,
        initial_cash=INITIAL_CASH,
    )
    if result is not None:
        result.print_summary()


def run_live() -> None:
    app = build_app("live")
    app.run(mode="live")


if __name__ == "__main__":
    mode, debug, trades_only = _parse_args(sys.argv[1:])
    if debug:
        enable_debug_mode(trades_only=trades_only)
    if mode == "live":
        run_live()
    else:
        run_live()
