import sys
from pathlib import Path

fixture_root = Path(__file__).resolve().parents[1]
if str(fixture_root) not in sys.path:
    sys.path.insert(0, str(fixture_root))
