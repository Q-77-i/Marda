"""面试图片通道（P2-M6 FR-26）：存储 / 校验 / LLM 编码（SPEC §7 图通道）。

四条口径：

- **文件存 `{upload_dir}/{interview_id}/{image_id}.{ext}`**；`image_id` = 服务端 uuid4 hex，
  客户端文件名不进路径（无路径穿越面）；类型按 **magic bytes** 判定，不信任 Content-Type。
- **state / checkpoint 只存 image_id**（路径由场次 + id 推出）——图字节进 checkpoint 是
  序列化爆炸坑（P2 规划口径）。
- **送 LLM = 编码成 data URI 的 content parts**（探针实测 deepseek-flash 视觉协议：
  OpenAI 式 `image_url`，且内容块必须是 {"type": ...} 对象、裸字符串 422）。
- **文件缺失/损坏 → 跳过该图 + warning，退化为纯文字**：绝不因一张图丢文件而拒答
  （2026-10-04 用户补充的风控口径）。

图附件**独立成一条 user 消息**（`attachment_message`），不改任何既有 prompt 模板——
无图调用的消息列表与接入前逐字一致（「无图场次零回归」由此在构造上成立）。
"""

from __future__ import annotations

import base64
import logging
import re
import shutil
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

IMAGE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_IMAGE_BYTES = 8 * 1024 * 1024  # 单张上限（前端已压缩，典型 < 1MB；8MB 是兜底）
MAX_IMAGES_PER_MESSAGE = 3  # 每条消息附件上限（API 校验；前端同值）
MAX_IMAGES_PER_INTERVIEW = 30  # 每场总量上限（防滥用；正常面试用不到）
MAX_IMAGES_TO_LLM = 3  # 送进 LLM 的取「最近 N 张」——token 可控、口径可解释（更早的图不再进上下文）

# magic bytes → (扩展名, MIME)。WebP 无固定前缀，单独判 RIFF 容器。
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
)
_EXT_MIME = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}

# 附件消息的说明文案（各节点按语境取用；图本体是数据，不是指令，GUARD 口径同候选人消息）
NOTE_JUDGE = "（以下为候选人随本次回答上传的截图，可作为评分依据之一）"
NOTE_FOLLOWUP = "（候选人随本次回答上传了以下截图；追问可结合截图内容）"
NOTE_ASK = "（上一轮问答中候选人上传了以下截图；出题可结合截图内容）"
NOTE_CLOSING = "（候选人随本轮提问上传了以下截图）"
NOTE_PROFILE = "（候选人随自我介绍上传了以下截图）"


class ImageError(Exception):
    """上传校验失败（API 层映射 400）。"""


def sniff_mime(data: bytes) -> tuple[str, str] | None:
    """按 magic bytes 判定 (扩展名, MIME)；不支持的类型返回 None。"""
    for signature, ext, mime in _MAGIC:
        if data.startswith(signature):
            return ext, mime
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def interview_dir(upload_dir: Path, interview_id: str) -> Path:
    return Path(upload_dir) / interview_id


def save_image(upload_dir: Path, interview_id: str, data: bytes) -> str:
    """校验并落盘一张图，返回 image_id；超限/类型不支持抛 `ImageError`。"""
    if not data:
        raise ImageError("图片内容为空")
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageError(f"图片超过 {MAX_IMAGE_BYTES // (1024 * 1024)}MB 上限")
    sniffed = sniff_mime(data)
    if sniffed is None:
        raise ImageError("仅支持 PNG / JPEG / WebP 图片")
    ext, _mime = sniffed
    image_id = uuid.uuid4().hex
    directory = interview_dir(upload_dir, interview_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{image_id}.{ext}").write_bytes(data)
    return image_id


def count_images(upload_dir: Path, interview_id: str) -> int:
    directory = interview_dir(upload_dir, interview_id)
    return len(list(directory.glob("*.*"))) if directory.is_dir() else 0


def image_path(upload_dir: Path, interview_id: str, image_id: str) -> Path | None:
    """image_id → 文件路径；id 非法（含穿越尝试）或文件不存在返回 None。"""
    if not IMAGE_ID_RE.match(image_id or ""):
        return None
    directory = interview_dir(upload_dir, interview_id)
    for ext in _EXT_MIME:
        candidate = directory / f"{image_id}.{ext}"
        if candidate.is_file():
            return candidate
    return None


def load_image_parts(
    upload_dir: Path,
    interview_id: str,
    image_ids: list[str],
    *,
    limit: int = MAX_IMAGES_TO_LLM,
) -> list[dict]:
    """image_id 列表 → LLM content parts（取最近 `limit` 张，编码为 data URI）。

    缺失 / 读失败 / 内容不再像图片（magic bytes 校验）→ 跳过该图 + warning，**不抛错**。
    """
    parts: list[dict] = []
    for image_id in image_ids[-limit:]:
        path = image_path(upload_dir, interview_id, image_id)
        if path is None:
            logger.warning("图片文件缺失，跳过：%s/%s", interview_id, image_id)
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            logger.warning("图片读取失败，跳过：%s/%s（%s）", interview_id, image_id, exc)
            continue
        if sniff_mime(data) is None:
            logger.warning("图片内容非法（magic bytes 不匹配），跳过：%s/%s", interview_id, image_id)
            continue
        mime = _EXT_MIME[path.suffix.lstrip(".")]
        encoded = base64.b64encode(data).decode()
        parts.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}})
    return parts


def attachment_message(parts: list[dict], note: str) -> dict:
    """图附件消息：一条 user 消息 = 说明文字 + 各图 parts。

    **独立成条而不是塞进既有模板**：无图时消息列表与接入前逐字一致，评分基线与
    评测口径（judge_messages 单一入口）零漂移。
    """
    return {"role": "user", "content": [{"type": "text", "text": note}, *parts]}


def remove_interview_images(upload_dir: Path, interview_id: str) -> None:
    """删除场次图片目录（删除场次时调用）；失败仅记日志，不阻塞删除主流程。"""
    directory = interview_dir(upload_dir, interview_id)
    if not directory.is_dir():
        return
    try:
        shutil.rmtree(directory)
    except OSError as exc:
        logger.warning("场次图片清理失败：%s（%s）", interview_id, exc)
