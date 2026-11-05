"""Installation-specific checkpointed update entry point."""
import runpy
from pathlib import Path
runpy.run_path(str(Path(__file__).resolve().parents[3]/'.local/us-tail-continuation-20261023.py'),run_name='__main__')
