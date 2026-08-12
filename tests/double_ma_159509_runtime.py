"""
双均线策略：统一入口 ``run_strategy``（回测：159509，全仓，万 0.5 佣金等见包内 YAML）。

运行（仓库根目录）::

    python -m alphaQuantSystem.examples.strategies.double_ma
"""
from __future__ import annotations
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
from loguru import logger
from alphaQuantSystem.examples.strategies.double_ma import DoubleMaStrategy
from alphaQuantSystem.strategy.runtime import run_strategy

import alphaQuantSystem.examples.strategies.double_ma as _double_ma_pkg

_YAML = Path(_double_ma_pkg.__path__[0]) / 'double_ma_159509_runtime.yaml'


def main() -> None:
    summary = run_strategy(
        DoubleMaStrategy,
        'backtest',
        strategy_yaml=str(_YAML))
    logger.info('回测摘要: {}', summary)


if __name__ == '__main__':
    main()
