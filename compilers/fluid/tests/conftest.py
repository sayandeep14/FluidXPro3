import sys
from pathlib import Path

COMPILERS = Path(__file__).resolve().parents[2]
for sub in ("fluid", "fluidc", "fx3c", "fluidc/tests"):
    sys.path.insert(0, str(COMPILERS / sub))
