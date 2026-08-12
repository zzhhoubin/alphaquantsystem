"""Configuration manager"""
from __future__ import annotations
import logging
from typing import Any, Dict


class ConfigManager:
    def __init__(self):
        self._config: Dict[str, Any] = {}

    def load_yaml(self, path: str) -> "ConfigManager":
        try:
            import yaml
            with open(path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            self._merge(data)
        except Exception as e:
            logging.getLogger(__name__).warning(f'Config load failed {path}: {e}')
        return self

    def load_dict(self, data: Dict[str, Any]) -> "ConfigManager":
        self._merge(data)
        return self

    def _merge(self, data: Dict[str, Any]) -> None:
        self._config.update(data)

    def get(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    def get_section(self, key: str) -> Dict[str, Any]:
        section = self._config.get(key, {})
        return section if isinstance(section, dict) else {}

    def to_dict(self) -> Dict[str, Any]:
        return dict(self._config)
