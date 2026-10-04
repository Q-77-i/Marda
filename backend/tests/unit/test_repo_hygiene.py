"""仓库卫生守卫（P2-M10）：**CI 里跑得了的那一半红线**。

红线有两条腿，跨机器能力不同：

1. **文本反查**（内容维度：文档里别出现题库原文）——比对语料是个人题库、按红线定义**不进仓库**，
   所以只能在有语料的机器上跑（`data/scripts/check_redline.py`，提交前固定动作）。CI 跑不了，
   也不该假装跑得了。
2. **入库守卫**（载体维度：语料/密钥本身别被提交）——只需要 git 索引，**CI 里可以真跑**，
   就是本文件。

两个判据：① 没有任何已跟踪文件命中 `.gitignore`（含「`git add -f` 硬塞」的形态，且规则以后
新增会自动纳入）；② 敏感路径（个人题库/解析产物/上传图/数据库/密钥/个人文档）一条都不在索引里
（防的是「规则被删/写错，于是第①条也失效」）。两条都只读 git 索引，不碰任何数据。
"""

from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]

# 敏感路径清单（与 .gitignore 对齐；.gitignore 是主口径，这里是「规则被改坏了」的兜底）
SENSITIVE = (
    "docs/题库/*",          # 个人题库原始文件
    "docs/private/*",       # 个人留档（规划报告、踩坑记录）
    "docs/规划报告.md",
    "data/raw/*",           # 开源语料原仓（体积 + 许可）
    "data/parsed/*",        # 解析产物（含题库原文）
    "data/uploads/*",       # 面试截图（P2-M6）
    "data/eval/golden/*",   # 评测 golden（含漏点原文，P1-M12 的教训）
    "data/eval/results/*",
    "*.sqlite3", "*.sqlite3-shm", "*.sqlite3-wal", "*.sqlite3.bak*", "*.db",
    ".env",
)


def _git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError) as exc:  # 不是 git 工作区：守卫失效要响
        pytest.fail(f"git {' '.join(args)} 失败——仓库卫生守卫必须真跑，不能静默跳过：{exc}")
    return result.stdout


def _sensitive_hits(tracked: list[str]) -> list[str]:
    hits = []
    for path in tracked:
        if path == ".env.example":  # 例外：模板里没有密钥（键名与占位值）
            continue
        if any(fnmatch.fnmatch(path, pattern) for pattern in SENSITIVE):
            hits.append(path)
        elif path.startswith(".env."):  # .env.local / .env.production 之类同样不许进
            hits.append(path)
    return hits


def test_没有已跟踪文件命中gitignore():
    """`git check-ignore` 命中 = 这文件本来不该进仓库（规则以后新增也自动纳入）。"""
    tracked = [line for line in _git("ls-files").splitlines() if line.strip()]
    assert tracked, "git 索引为空——目录不对？"

    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin"],
        cwd=REPO_ROOT, input="\n".join(tracked), capture_output=True, text=True,
    )
    ignored = [line for line in result.stdout.splitlines() if line.strip()]
    assert not ignored, f"这些文件已被跟踪、却又命中 .gitignore（多半是 git add -f 塞进来的）：{ignored}"


def test_敏感路径不在索引里():
    tracked = [line for line in _git("ls-files").splitlines() if line.strip()]

    hits = _sensitive_hits(tracked)

    assert not hits, (
        "语料/密钥类文件进了 git 索引（红线：个人题库与派生数据一律不进仓库）：\n  "
        + "\n  ".join(hits)
    )


def test_守卫自身有效_合成路径会被抓():
    """反向验证：守卫不是恒绿——拿合成路径喂进去必须命中（判据要有牙齿）。"""
    fake = ["docs/题库/真题.md", "data/marda.sqlite3", ".env.local", "backend/app/main.py"]
    assert _sensitive_hits(fake) == ["docs/题库/真题.md", "data/marda.sqlite3", ".env.local"]
