"""配置加载：从项目根目录 .env 读取，密钥不进仓库、不打印。"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
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
    deepseek_pro_model: str = "deepseek-v4-pro"  # 深度档：报告生成（SPEC §3）

    # 账号（FR-23）：JWT 签名密钥，本地 .env 生成随机值（不进仓库）；
    # 长度下限 32（HS256 推荐）：弱密钥启动即报错，而不是静默签出可爆破的 token
    jwt_secret: str = Field(min_length=32)

    # 嵌入：本地 BGE-M3 容器（M3 起，dense + sparse 双向量，见 embedding_service/）
    embedding_url: str = "http://localhost:8091"

    # SiliconFlow：M3 起只留 rerank（嵌入退为本地 BGE-M3）
    siliconflow_api_key: str
    siliconflow_base_url: str = "https://api.siliconflow.cn/v1"

    # 可观测（P1-M4）：Langfuse 云形态；Key 缺失时整体降级零开销（本地/CI 无需账号）
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""
    langfuse_base_url: str = "https://cloud.langfuse.com"

    # 数据
    db_path: Path = REPO_ROOT / "data" / "marda.sqlite3"
    checkpoint_db_path: Path = REPO_ROOT / "data" / "checkpoints.sqlite3"
    qdrant_url: str = "http://localhost:6333"

    # 面试默认值
    default_question_count: int = 10


@lru_cache
def get_settings() -> Settings:
    return Settings()
