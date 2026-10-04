"""语料红线检查器的单测（P2-M10）：CI 里能查的那一半。

**为什么要有**：红线门禁的「真比对」必须在有语料的机器上跑（个人题库不进仓库，CI 没有语料），
CI 能查的是**检查器本身没坏**——骨架归一、窗口滑动、命中合并、语料取值（哪些行算红线语料）。
夹具一律**合成文本**：这个文件本身要进公开仓库，写真题就自我违规。

**它已经抓过一次真 bug**（写这个文件的当天）：`key_points` 在库里是 JSON 文本，
原来 `*(key_points or [])` 把字符串拆成了**单字**，单字滑不出 12 字窗口——关键点于是
「一条都没参与比对」。判据全绿、覆盖悄悄少一块，正是本文件要防的那种事。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from check_redline import NGRAM, _ngrams, build_bank_ngrams, cjk_skeleton, scan


def _personal_db(tmp_path: Path) -> Path:
    """两行合成语料：一行个人题库（红线语料）、一行公开源（不参与比对）。"""
    path = tmp_path / "marda.sqlite3"
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE questions (question TEXT, answer TEXT, key_points TEXT,"
        " user_id TEXT, source TEXT)"
    )
    con.execute(
        "INSERT INTO questions VALUES (?, ?, ?, ?, ?)",
        ("合成题干甲" * 4, "合成答案乙" * 4,
         json.dumps(["合成关键点丙" * 3], ensure_ascii=False), None, "个人题库上传"),
    )
    con.execute(
        "INSERT INTO questions VALUES (?, ?, ?, ?, ?)",
        ("公开题干丁" * 4, "公开答案戊" * 4, json.dumps(["公开关键点己" * 3], ensure_ascii=False),
         None, "开源语料A"),
    )
    con.commit()
    con.close()
    return path


def test_骨架只留中文():
    assert cjk_skeleton("上限100条（含）— abc tool_choice") == "上限条含"


def test_窗口是连续十二个字():
    grams = _ngrams("甲乙丙丁戊己庚辛壬癸子丑寅卯")  # 14 字 → 3 个窗口
    assert len(grams) == 3 and all(len(g) == NGRAM for g in grams)
    assert grams[0] == "甲乙丙丁戊己庚辛壬癸子丑"


def test_命中要合并成一段而不是十个窗口(tmp_path):
    """复制一段 20 字的话会命中 9 个首尾相接的窗口——报告要报「那一段」，不是九条。"""
    bank = set(_ngrams("甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉"))  # 20 字骨架
    target = tmp_path / "doc.md"
    target.write_text("前言\n甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉\n后记", encoding="utf-8")

    hits = scan([target], bank)

    assert len(hits) == 1 and hits[0][1] == "甲乙丙丁戊己庚辛壬癸子丑寅卯辰巳午未申酉"


def test_无关文本零命中(tmp_path):
    bank = set(_ngrams("甲乙丙丁戊己庚辛壬癸子丑"))
    target = tmp_path / "doc.md"
    target.write_text("这段文字与语料没有任何关系，纯属自写。", encoding="utf-8")

    assert scan([target], bank) == []


def test_语料取值_只收个人题库且关键点参与比对(tmp_path):
    """语料 = `user_id` 非空 **或** source 为个人题库；**题干/答案/关键点三处都要进**。

    公开源（开源语料）不参与——红线管的是个人题库。
    """
    bank = build_bank_ngrams(_personal_db(tmp_path))

    assert _ngrams(cjk_skeleton("合成题干甲" * 4))[0] in bank  # 题干
    assert _ngrams(cjk_skeleton("合成答案乙" * 4))[0] in bank  # 答案
    assert _ngrams(cjk_skeleton("合成关键点丙" * 3))[0] in bank  # 关键点（曾经漏掉的一路）
    assert _ngrams(cjk_skeleton("公开题干丁" * 4))[0] not in bank  # 公开源不参与
