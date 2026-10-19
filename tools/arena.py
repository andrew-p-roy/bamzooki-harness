"""BAMZOOKi Arena: OpenRouter models take the challenge one after another; chat with them.

  python tools/arena.py [--challenger <openrouter model id>]... [--challenge challenges/short.toml]

Type /next for the next challenger, /help for all commands. See src/bamzooki/arena.py.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bamzooki.arena import main  # noqa: E402

if __name__ == "__main__":
    main()
