"""Add the project root to sys.path so scripts run as `uv run python scripts/xxx.py`."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
