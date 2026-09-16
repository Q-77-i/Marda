"""把 data/scripts 与 tests/fixtures 加入 sys.path，测试可直接 import 管道脚本与 fake 组件。"""

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
for extra in (TESTS_DIR.parents[1] / "data" / "scripts", TESTS_DIR / "fixtures"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))
