"""Fail if game files, archives or derived keys are about to be committed.

  python tools/check_assets.py            # everything git would commit
  python tools/check_assets.py FILE...    # just these files (pre-commit passes staged files)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bamzooki.guard import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
