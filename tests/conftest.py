import sys
from pathlib import Path

# Make sure the project root (one level up from tests/) is importable,
# so `from src.outlier_engine import ...` works no matter where pytest
# is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
