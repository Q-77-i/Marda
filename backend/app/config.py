"""配置加载：从项目根目录 .env 读取，密钥不进仓库、不打印。"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py → 项目根目录
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # DeepSeek（openai SDK + base_url 直连，见 CLAUDE.md 坑位清单）
    deepseek_api_key: str
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"

    # 嵌入（SiliconFlow BGE-M3，demo 阶段）
    siliconflow_api_key: str
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_model: str = "BAAI/bge-m3"

    # 数据
    db_path: Path = REPO_ROOT / "data" / "marda.sqlite3"
    checkpoint_db_path: Path = REPO_ROOT / "data" / "checkpoints.sqlite3"
    qdrant_url: str = "http://localhost:6333"

    # 面试默认值
    default_question_count: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
