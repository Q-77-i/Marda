"""domain 单测：域集合不变量（P2-M11）+ 项目深挖题数量公式（P1-M4.6-C）。"""

from __future__ import annotations

from app.domain import (
    ASKABLE_DOMAINS,
    DOMAIN_LABELS,
    DOMAIN_WEIGHTS,
    ENABLED_DOMAINS,
    PROJECT_DOMAIN,
    project_count,
)


def test_项目深挖可出题但不属于技术配额():
    """P2-M11 第 0 步：8 道项目叙事题归位 project 域，该域第一次有题库身份。

    与行为面同款语义（ASKABLE_DOMAINS = 可出题但不属于技术配额）：技术面配额只
    在 DOMAIN_WEIGHTS 分配，永远抽不到它；项目深挖题由 PROJECT 阶段按域检索题库。
    私有题库不开放该域（ENABLED_DOMAINS 不含），上传表单也列不出来。
    """
    assert PROJECT_DOMAIN in ASKABLE_DOMAINS
    assert PROJECT_DOMAIN not in DOMAIN_WEIGHTS, "项目深挖进了技术配额，技术面会按配额抽到它"
    assert PROJECT_DOMAIN not in ENABLED_DOMAINS, "私有题库不开放项目深挖域"
    assert DOMAIN_LABELS[PROJECT_DOMAIN] == "项目深挖"


def test_两题场保底一道技术题():
    # min(3, max(2, ceil(2/3)), 1) = 1 → 1 项目 + 1 技术
    assert project_count(2) == 1


def test_三题场两道项目题():
    # min(3, max(2, ceil(3/3)), 2) = 2 → 2 项目 + 1 技术
    assert project_count(3) == 2


def test_五题场两道项目题():
    # 拍板口径：5 题场 2 项目 + 3 技术
    assert project_count(5) == 2


def test_十题及以上封顶三道项目题():
    assert project_count(10) == 3
    assert project_count(15) == 3
    assert project_count(20) == 3


def test_全区间至少保底一道技术题():
    """N−1 约束：任意合法题量（2-20）下技术题恒 ≥ 1，项目题 ≤ 3。"""
    for n in range(2, 21):
        p = project_count(n)
        assert n - p >= 1, f"N={n} 技术题不足 1"
        assert 0 <= p <= 3
