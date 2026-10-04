"""语料红线机械反查（提交前检查）：产物文本里不得出现题库原文或其片段。

**为什么要有这个脚本**：CLAUDE.md 的红线是「派生文本里的题库原文一律不入库，判据是
文本里会不会出现题库原文或其片段」——但这条规矩**靠自己记是执行不了的**。实例
（2026-10-01，M12 会话 2）：立完规矩后写 SPEC / CLAUDE.md / docstring / 测试夹具时，
为说明「评分官会截断长关键点」顺手引了题库关键点片段当例子，四处违规而**毫无察觉**
（写文档的注意力在「说清楚」，不在「这段是不是题库原文」）。靠机械反查才抓出来。
**结论：规则要有不依赖记忆的执行器，明知规矩也一样跑。**

**判据**：把两侧文本都归一到 **CJK 骨架**（只留中文字符，去掉空白、标点、数字、英文），
再在骨架上滑 **12 字窗口**比对。为什么要归一：① 直接按原文本滑窗，数字/箭头会把片段切碎
（「…上限100条」「…痛点→方案→收益」这类会漏掉）；② 不归一就要靠「窗口里 CJK 占比」过滤
噪声，而那会把含数字的真实片段一起滤掉（实测：植入一条题库关键点，检查器 0 命中——**漏报**）；
③ 归一后技术词（`tool_choice`/`checkpoint`）被整体剥掉，只剩下真正连续的 12 个中文字——
随机文本凑不出 12 个连续相同中文字，命中的就是复制来的。

用法：
    python data/scripts/check_redline.py                # 查「现在提交会进仓库的改动」
    python data/scripts/check_redline.py --all          # 查全部已入库文件
    python data/scripts/check_redline.py path/to/a.md   # 查指定文件

**不要传 `data/eval/golden/` 与 `data/eval/results/`**：那些产物按设计就含题库内容
（题面/回答），靠 `.gitignore` 隔离，不属于本检查的范围。

命中即非零退出。**输出里会打印命中的片段**（否则没法定位），别把它粘回仓库文件里。
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import bootstrap  # noqa: F401  # 把 backend/ 加进 sys.path

from app.config import get_settings

REPO_ROOT = Path(__file__).resolve().parents[2]

NGRAM = 12
"""骨架窗口长度：**连续的 12 个中文字**。短了会命中通用表述（「第一种做法是」这类），
长了会漏掉本身只有十来字的要点。实测 12 在真库上既抓住植入片段、又不误报。"""

# 受检文件类型（文本）；二进制与图片不在其列，读不出 UTF-8 的也跳过
SUFFIXES = (".md", ".py", ".toml", ".json", ".ts", ".tsx", ".js", ".j2", ".yml", ".yaml", ".sh", ".css", ".html")
CJK_ONLY_RE = re.compile(r"[^一-鿿]+")

# 个人题库的 source 前缀（M5 起公开语料另有来源，不在红线内）
PERSONAL_SOURCE_PREFIX = "个人题库%"
BANK_SQL = (
    "SELECT question, answer, key_points FROM questions"
    " WHERE user_id IS NOT NULL OR source LIKE ?"
)


def cjk_skeleton(text: str) -> str:
    """把文本归一成「只留中文字符」的骨架：空白、标点、数字、英文全部去掉。

    「…列表，上限100条」与「…列表上限条」在骨架里是同一串——数字与标点不再切碎片段，
    技术词（`tool_choice` 之类）也整体消失，两侧比的是真正连续的中文。
    """
    return CJK_ONLY_RE.sub("", text)


def _ngrams(skeleton: str) -> list[str]:
    """骨架串上所有 N 字窗口。"""
    return [skeleton[i : i + NGRAM] for i in range(len(skeleton) - NGRAM + 1)]


def key_point_texts(raw: object) -> list[str]:
    """关键点列的文本列表。库里存的是 **JSON 文本**（老库/夹具也可能是列表，两种都容错）。

    ⚠️ 这里踩过一回（P2-M10 修）：原写法 `*(key_points or [])` 把 JSON 文本当可迭代**拆成
    单字**——单字滑不出 12 字窗口，于是**关键点一条都没参与比对，检查却全绿**。
    「顺手把字符串展开」这种写法不会报错，只会静默少查一路。
    """
    if not raw:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw]
    try:
        parsed = json.loads(str(raw))
    except ValueError:
        return [str(raw)]
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return [str(parsed)]


def build_bank_ngrams(db_path: Path) -> set[str]:
    """题库文本（题干 + 答案 + 关键点）的骨架 n-gram 集合。只读打开。"""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = con.execute(BANK_SQL, (PERSONAL_SOURCE_PREFIX,)).fetchall()
    finally:
        con.close()
    grams: set[str] = set()
    for question, answer, key_points in rows:
        texts = [question or "", answer or "", *key_point_texts(key_points)]
        for text in texts:
            grams.update(_ngrams(cjk_skeleton(str(text))))
    return grams


def _git(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def pending_files() -> list[Path]:
    """「现在提交会进仓库的改动」= 与 HEAD 有差异的已跟踪文件 + 未跟踪且未被忽略的文件。"""
    names = set(_git("diff", "--name-only", "HEAD"))
    names.update(_git("ls-files", "--others", "--exclude-standard"))
    return [REPO_ROOT / name for name in sorted(names)]


def all_tracked_files() -> list[Path]:
    names = set(_git("ls-files"))
    names.update(_git("ls-files", "--others", "--exclude-standard"))
    return [REPO_ROOT / name for name in sorted(names)]


def scan(paths: list[Path], bank: set[str]) -> list[tuple[Path, str]]:
    """逐文件找命中；返回 `[(文件, 命中片段)]`。

    **连续命中的窗口要合并成一段**：复制来的一段话会命中一串首尾相接的窗口（10 个窗口
    报 10 条，没人看得下去，也读不出「这是同一段」）——合并后报的是复制进来的那一段。
    """
    hits: list[tuple[Path, str]] = []
    for path in paths:
        if path.suffix not in SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # 二进制或读不了：不在受检范围
        skeleton = cjk_skeleton(text)
        spans: list[list[int]] = []  # [start, end)
        for i in range(len(skeleton) - NGRAM + 1):
            if skeleton[i : i + NGRAM] in bank:
                if spans and i <= spans[-1][1]:  # 与上一段重叠或相接 → 同一段
                    spans[-1][1] = i + NGRAM
                else:
                    spans.append([i, i + NGRAM])
        hits.extend((path, skeleton[start:end]) for start, end in spans)
    return hits


DISPLAY_CHARS = 40


def _short(span: str) -> str:
    return span if len(span) <= DISPLAY_CHARS else span[:DISPLAY_CHARS] + "…"


def main() -> None:
    parser = argparse.ArgumentParser(description="语料红线机械反查（提交前检查）")
    parser.add_argument("paths", nargs="*", help="指定文件（默认查待提交的改动）")
    parser.add_argument("--all", action="store_true", help="查全部已跟踪文件（发布前全量自查）")
    parser.add_argument("--db", default="", help="业务库路径（默认取 settings.db_path）")
    args = parser.parse_args()

    db_path = Path(args.db) if args.db else get_settings().db_path
    bank = build_bank_ngrams(db_path)
    if not bank:
        raise SystemExit(f"题库里没取到任何文本（{db_path}）——路径不对？检查中止。")

    if args.paths:
        targets = [Path(p) for p in args.paths]
        scope = f"指定 {len(targets)} 个文件"
    elif args.all:
        targets = all_tracked_files()
        scope = f"全量已跟踪文件 {len(targets)} 个"
    else:
        targets = pending_files()
        scope = f"待提交改动 {len(targets)} 个文件"

    print(f"题库片段 {len(bank)} 条 · 检查范围：{scope}")
    hits = scan(targets, bank)
    if not hits:
        print("✅ 0 命中：受检文本里没有题库原文或其片段")
        return

    print(f"\n❌ 命中 {len(hits)} 处（题库原文或其片段）：")
    for path, span in hits:
        try:
            shown = path.relative_to(REPO_ROOT)
        except ValueError:  # 仓库外的文件（如 /tmp 下的探针）照原路径显示
            shown = path
        print(f"  {shown}（{len(span)} 字）：{_short(span)}")
    print(
        "\n改写建议：把「引原句举例」换成「描述差在哪」（抽象描述不改论证力度）。\n"
        "注意：以上片段来自题库，**不要粘回任何仓库文件**。"
    )
    sys.exit(1)


if __name__ == "__main__":
    main()
