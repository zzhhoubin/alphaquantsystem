"""
日志模块 —— Loguru 封装

统一日志目录默认落在 ``alphaQuantSystem/logs``（相对本包根目录的 ``logs``），
避免随进程当前工作目录分散到仓库各处。
"""
from __future__ import annotations
import sys
from pathlib import Path
from typing import Optional, Union
from loguru import logger
_PACKAGE_ROOT = Path(__file__).resolve().parent.parent
_logger_configured = False

def package_root() -> Path:
    """返回 ``alphaQuantSystem`` 包根目录。"""
    return _PACKAGE_ROOT

def default_log_dir() -> Path:
    """默认统一日志目录：``alphaQuantSystem/logs``。"""
    return _PACKAGE_ROOT / 'logs'

def resolve_log_dir(log_dir: Optional[Union[str, Path]]) -> Path:
    """
    解析日志目录。
    - ``None`` 或空字符串：``default_log_dir()``
    - 绝对路径：原样使用
    - 相对路径：相对于 ``alphaQuantSystem`` 包根（例如 ``logs`` -> ``alphaQuantSystem/logs``）
    """
    if log_dir is None or (isinstance(log_dir, str) and (not log_dir.strip())):
        return default_log_dir()
    p = Path(log_dir)
    if p.is_absolute():
        return p
    return (_PACKAGE_ROOT / p).resolve()

def is_logger_configured() -> bool:
    """是否已通过 setup_logger 配置（含 main.py --debug 提前初始化）。"""
    return _logger_configured


def setup_logger(log_dir: Optional[Union[str, Path]]=None, level: str='INFO', rotation: str='1 day', retention: str='30 days'):
    """
    初始化 Loguru 日志
    - 控制台：彩色输出
    - 文件：按天轮转，保留 30 天
    """
    global _logger_configured
    logger.remove()
    logger.add(sys.stdout, level=level, format='<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>', colorize=True)
    log_path = resolve_log_dir(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)
    logger.add(log_path / 'alphaquant_{time:YYYY-MM-DD}.log', level=level, rotation=rotation, retention=retention, encoding='utf-8', format='{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{line} - {message}')
    _logger_configured = True
    return logger
