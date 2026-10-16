"""Operator CLI wrapper; the frozen rollback implementation is standalone."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from deploy.pvp_ops import main
if __name__=="__main__":raise SystemExit(main())
