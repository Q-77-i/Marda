"""图片通道单测（P2-M6 FR-26）：magic bytes 判定 / 落盘 / 取路径 / LLM 编码 / 清理。

风控口径（2026-10-04 用户补充）：文件缺失或损坏 → 跳过该图 + warning，退化为纯文字，
绝不因一张图丢文件而拒答。
"""

from __future__ import annotations

import logging

import pytest

from app.tools import images

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 32


def test_magic_bytes_判定_png_jpeg_webp():
    assert images.sniff_mime(PNG) == ("png", "image/png")
    assert images.sniff_mime(JPEG) == ("jpg", "image/jpeg")
    assert images.sniff_mime(WEBP) == ("webp", "image/webp")


def test_非图片与截断前缀一律拒绝():
    assert images.sniff_mime(b"GIF89a" + b"\x00" * 16) is None  # 不支持的类型
    assert images.sniff_mime(b"RIFF\x00\x00\x00\x00XXXX" + b"\x00" * 8) is None  # RIFF 但不是 WEBP
    assert images.sniff_mime(b"\x89PNG") is None  # 截断签名
    assert images.sniff_mime(b"") is None


def test_落盘返回随机_id_且扩展名按内容定(tmp_path):
    image_id = images.save_image(tmp_path, "iv1", PNG)

    assert images.IMAGE_ID_RE.match(image_id)
    assert (tmp_path / "iv1" / f"{image_id}.png").read_bytes() == PNG


def test_落盘拒绝空内容与超限(tmp_path):
    with pytest.raises(images.ImageError):
        images.save_image(tmp_path, "iv1", b"")
    with pytest.raises(images.ImageError):
        images.save_image(tmp_path, "iv1", PNG + b"\x00" * (images.MAX_IMAGE_BYTES + 1))


def test_落盘拒绝伪装成图片的文本(tmp_path):
    with pytest.raises(images.ImageError):
        images.save_image(tmp_path, "iv1", "<html>不是图片</html>".encode())


def test_取路径_坏_id_与穿越尝试返回_none(tmp_path):
    assert images.image_path(tmp_path, "iv1", "not-a-hex-id") is None
    # 即使 id 形状像路径也不能穿过目录边界（id 必须匹配纯 hex）
    assert images.image_path(tmp_path, "iv1", "../../etc/passwd") is None
    assert images.image_path(tmp_path, "iv1", "a" * 32) is None  # 格式合法但文件不存在


def test_load_parts_编码为_data_uri(tmp_path):
    image_id = images.save_image(tmp_path, "iv1", PNG)

    parts = images.load_image_parts(tmp_path, "iv1", [image_id])

    assert len(parts) == 1
    assert parts[0]["type"] == "image_url"
    assert parts[0]["image_url"]["url"].startswith("data:image/png;base64,")


def test_load_parts_缺失文件跳过并告警(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        parts = images.load_image_parts(tmp_path, "iv1", ["a" * 32])

    assert parts == []
    assert any("图片文件缺失" in record.message for record in caplog.records)


def test_load_parts_损坏文件跳过_混合时只保留可用的(tmp_path, caplog):
    good = images.save_image(tmp_path, "iv1", PNG)
    broken = "b" * 32
    (tmp_path / "iv1" / f"{broken}.png").write_bytes(b"not an image anymore")

    with caplog.at_level(logging.WARNING):
        parts = images.load_image_parts(tmp_path, "iv1", [broken, good])

    assert len(parts) == 1
    assert any("magic bytes 不匹配" in record.message for record in caplog.records)


def test_load_parts_只取最近_n_张(tmp_path):
    ids = [images.save_image(tmp_path, "iv1", PNG + bytes([i])) for i in range(5)]  # 内容各异好辨认

    parts = images.load_image_parts(tmp_path, "iv1", ids)

    assert len(parts) == images.MAX_IMAGES_TO_LLM
    expected = [
        images.load_image_parts(tmp_path, "iv1", [image_id])[0]["image_url"]["url"]
        for image_id in ids[-3:]  # 「最近」= 列表尾部
    ]
    assert [p["image_url"]["url"] for p in parts] == expected


def test_附件消息形状_说明文字在图前(tmp_path):
    image_id = images.save_image(tmp_path, "iv1", PNG)
    parts = images.load_image_parts(tmp_path, "iv1", [image_id])

    message = images.attachment_message(parts, images.NOTE_JUDGE)

    assert message["role"] == "user"
    assert message["content"][0] == {"type": "text", "text": images.NOTE_JUDGE}
    assert message["content"][1]["type"] == "image_url"


def test_删除场次目录_不存在时静默(tmp_path):
    image_id = images.save_image(tmp_path, "iv1", PNG)

    images.remove_interview_images(tmp_path, "iv1")
    images.remove_interview_images(tmp_path, "不存在的场次")  # 不抛

    assert images.image_path(tmp_path, "iv1", image_id) is None
    assert not (tmp_path / "iv1").exists()


def test_计数按场次隔离(tmp_path):
    images.save_image(tmp_path, "iv1", PNG)
    images.save_image(tmp_path, "iv1", JPEG)
    images.save_image(tmp_path, "iv2", PNG)

    assert images.count_images(tmp_path, "iv1") == 2
    assert images.count_images(tmp_path, "iv2") == 1
    assert images.count_images(tmp_path, "iv3") == 0
