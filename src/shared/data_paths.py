"""
Per-portfolio data directory. Defaults to "data" (today's single-portfolio
behavior, unchanged). A second portfolio running as its own OS process sets
MM_DATA_DIR in the shell before launching python, so its balance/positions/
trade book/decisions never collide with the first portfolio's files.
"""
import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("MM_DATA_DIR", "data"))
