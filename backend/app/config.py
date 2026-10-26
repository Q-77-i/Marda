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

    # 语音（P2-M5 FR-24）：ASR = 豆包流式语音识别（火山「语音控制台」签发的 key，
    # **与方舟 Ark key 不通用**——探针实测：Ark key 走 X-Api-Key 一律 401）；
    # 留空 = 语音通道整体降级（前端提示改走文字，面试链路零影响）
    volcano_speech_api_key: str = ""
    volcano_asr_url: str = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
    volcano_asr_resource_id: str = "volc.seedasr.sauc.duration"  # 豆包流式语音识别 2.0 小时版
    tts_voice: str = "zh-CN-XiaoxiaoNeural"  # edge-tts 音色（免费，见 P2-M5 决策④）

    # 数据
    db_path: Path = REPO_ROOT / "data" / "marda.sqlite3"
    checkpoint_db_path: Path = REPO_ROOT / "data" / "checkpoints.sqlite3"
    qdrant_url: str = "http://localhost:6333"
    # 面试图片通道（P2-M6 FR-26）：图存磁盘、state 只存 id（容器内由 compose 覆盖到 /app/data）
    upload_dir: Path = REPO_ROOT / "data" / "uploads"

    # 上游可靠性（P2-M9）：并发闸门 + 断路器，见 reliability.py / SPEC §3。
    # DeepSeek 限流是**并发数**不是 QPS（账户级 flash 2500 / pro 500，CLAUDE.md 坑位 3）
    # ——下面默认值是**自设的保守上限**（demo 形态保护上游 + M11 压测削峰），环境变量可调。
    llm_max_concurrency_flash: int = 16
    llm_max_concurrency_pro: int = 4
    llm_acquire_timeout_s: float = 30.0
    llm_breaker_threshold: int = 5  # 连续「上游不可用」次数（内容类失败不计）
    llm_breaker_cooldown_s: float = 30.0

    # 面试默认值
    default_question_count: int = 10

    # ===== P4 生产化（docs/private/升级方案.md §4）=====
    # 业务库后端：sqlite（默认，单机 / 单测）| postgres（生产）
    db_backend: str = "sqlite"
    database_url: str = ""  # DATABASE_URL（postgres 模式必填）
    db_pool_min_size: int = 1
    db_pool_max_size: int = 5
    db_connect_timeout_s: float = 10.0

    # checkpointer 后端：sqlite（默认）| postgres（多 worker 共享状态）
    checkpoint_backend: str = "sqlite"
    checkpoint_database_url: str = ""  # CHECKPOINT_DATABASE_URL（独立库）
    checkpoint_pool_max_size: int = 10
    # 每场保留最近 N 张快照 + 最终一张（更早的不可回放，见 docs/P4-生产化.md §4.3）
    checkpoint_retention: int = 20

    # 协调后端：memory（默认，单进程 / 测试）| redis（多 worker 共享）
    coordination_backend: str = "memory"
    redis_url: str = "redis://localhost:6379/0"
    # 会话锁 TTL 要大于一轮最长耗时；worker 崩了靠 TTL 自动过期
    interview_lock_ttl_s: int = 600
    # 单用户每分钟请求上限（Redis 模式下全局生效）
    user_rate_limit_per_min: int = 30
    # 全局 LLM 并发槽（**跨 worker 共享**，不是每 worker 一份）
    llm_slots_flash: int = 16
    llm_slots_pro: int = 4
    llm_slot_lease_s: int = 120

    @property
    def business_db(self) -> Path | str:
        """业务库定位：postgres 模式返回连接串，sqlite 模式返回文件路径。

        **唯一分派点**：db 层门面按这个值的类型选后端（Path → SQLite、str → PostgreSQL），
        路由层只管传 `settings.business_db`，不出现 SQL 方言分支。
        题库（questions）永远留在 `db_path` 指向的 SQLite 里——离线资产，不跟着业务库迁。
        """
        if self.db_backend == "postgres":
            if not self.database_url:
                raise RuntimeError("DB_BACKEND=postgres 时必须配置 DATABASE_URL")
            return self.database_url
        return self.db_path


@lru_cache
def get_settings() -> Settings:
    return Settings()
