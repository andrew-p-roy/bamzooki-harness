"""Operator tool for challenges: show a model's progress, reset its budget.

  python tools/challenge.py status --model anthropic/claude-sonnet-4.5 [--challenge challenges/short.toml]
  python tools/challenge.py reset  --model anthropic/claude-sonnet-4.5 [--challenge challenges/short.toml]

--model is the entrant's full model name (as in Zook Entries, with "/" or "_").

The challenge file defaults to bamzooki.toml's `challenge` or challenges/default.toml.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bamzooki.challenge import Challenge, ChallengeState  # noqa: E402
from bamzooki.config import load_config  # noqa: E402
from bamzooki.sdk.store import entrant_folder  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["status", "reset"])
    ap.add_argument("--model", required=True)
    ap.add_argument("--challenge")
    a = ap.parse_args()
    cfg = load_config()
    challenge = Challenge.load(a.challenge or cfg.challenge)
    state = ChallengeState(challenge, cfg.zoo_dir / entrant_folder(a.model))
    if a.action == "reset":
        state.reset()
        print(f"reset {a.model} in challenge {challenge.name!r}: {challenge.runs or 'unlimited'} runs available")
        return 0
    print(json.dumps({"challenge": challenge.name, "file": str(challenge.path), **state.budget(),
                      "log": state.data["log"][-10:],
                      "submissions": [{k: s.get(k) for k in ("design", "outcome", "score", "time")}
                                      for s in state.data["submissions"]]}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
