"""
配置加载模块
"""
import os
import json
import yaml
from pathlib import Path
from typing import Any, Dict, List


class ConfigLoader:
    """加载和管理配置"""

    def __init__(self, config_dir: str = None):
        if config_dir is None:
            self.config_dir = Path(__file__).parent.parent.parent / "config"
        else:
            self.config_dir = Path(config_dir)
        self._config = None
        self._keywords = None

    @property
    def config(self) -> Dict[str, Any]:
        if self._config is None:
            self._load_config()
        return self._config

    @property
    def keywords(self) -> Dict[str, List[str]]:
        if self._keywords is None:
            self._load_keywords()
        return self._keywords

    def _load_config(self):
        config_file = self.config_dir / "config.yaml"
        if not config_file.exists():
            raise FileNotFoundError(f"配置文件不存在: {config_file}")
        with open(config_file, "r", encoding="utf-8") as f:
            self._config = yaml.safe_load(f)

    def _load_keywords(self):
        keywords_file = self.config_dir / "keywords.json"
        if not keywords_file.exists():
            raise FileNotFoundError(f"关键词文件不存在: {keywords_file}")
        with open(keywords_file, "r", encoding="utf-8") as f:
            self._keywords = json.load(f)

    def save_keywords(self, keywords: Dict[str, List[str]]):
        """保存关键词到文件"""
        keywords_file = self.config_dir / "keywords.json"
        with open(keywords_file, "w", encoding="utf-8") as f:
            json.dump(keywords, f, ensure_ascii=False, indent=2)
        self._keywords = keywords

    def get(self, key: str, default=None) -> Any:
        """获取配置项，支持点号分隔的嵌套key"""
        keys = key.split(".")
        value = self.config
        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default
        return value

    @property
    def project_root(self) -> Path:
        return self.config_dir.parent

    @property
    def output_dir(self) -> Path:
        return self.project_root / "output"

    @property
    def data_dir(self) -> Path:
        return self.project_root / "data"

    def ensure_dirs(self):
        """确保所有必要目录存在"""
        dirs = [
            self.output_dir / "daily",
            self.output_dir / "biweekly",
            self.output_dir / "archive",
            self.data_dir,
            self.project_root / "logs",
        ]
        for d in dirs:
            d.mkdir(parents=True, exist_ok=True)
