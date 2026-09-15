"""让 data/scripts 下的管道脚本能 import backend/app。

直接运行（python data/scripts/parse_md.py）时 sys.path[0] 是 data/scripts/，
backend/ 不在路径上；每个脚本开头 `import bootstrap` 即可。
"""

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
