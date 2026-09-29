"""题目文本的公共判定（语料管道与私有上传共用，SPEC §8.1）。

`answer_substance` 的口径来自 P1-M5 会话 2：源里的占位答案（`答案：xx`、空代码块）按
实质字符数判为无答案——否则它会以 enabled 身份占配额、进向量库，却给不出任何参考答案。

管道（data/scripts/bank.py）与私有上传（private_parse）共用这一份判定，
避免两处阈值各自漂移（管道 → backend 是既有依赖方向，反向不可）。
"""

from __future__ import annotations

import re
from typing import Final

FENCE_LINE_RE = re.compile(r"^\s*```.*$", re.M)
# 占位与空壳实测在 0～2 字（`xx`、空代码块），题库里最短的真答案 8 字、真实语料 15 字
MIN_ANSWER_CHARS: Final = 5


def answer_substance(answer: str) -> int:
    """答案的实质字符数：去掉代码围栏行（```lang / ```）与首尾空白后还剩多少。

    围栏只剥「行」不剥内容——` ```python\\n\\n``` ` 这种空壳剥完就是 0；正文里的代码块照算。
    """
    return len(FENCE_LINE_RE.sub("", answer).strip())
