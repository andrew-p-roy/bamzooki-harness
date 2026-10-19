# Model Instructions

Everything a model is told about the game, in one place. Edit these files to change what models
see; nothing else needs changing.

| File | What it is | How models get it |
|---|---|---|
| `Agent Prompt.md` | The system prompt for an entrant: rules (tools only), how to work, and how to present its challenger at the end | `tools/arena.py` sends it as the system prompt; for Claude Code/Desktop paste it as the first message |
| `Zook Design Guide.md` | How Zooks and the Zook Kit work: geometry, joints, movement paths, steering, gait recipes, what wins, testing strategy, and a map from Zook Kit options to spec fields | the `get_design_guide` tool |
| `BAMZOOKi Manual.pdf` | **Your own copy** of the official Zook Kit manual (BBC material: gitignored and blocked by the asset guard, never committed) | the `read_manual` tool (contents, page ranges, search) |

The challenge itself (task text, run budget, evaluation game) lives in `challenges/*.toml`, and
the contest rules models see are in `src/bamzooki/runner/contests.py` (`RULES`).

If the manual is missing (e.g. a fresh clone), copy your PDF here as `BAMZOOKi Manual.pdf`;
`read_manual` reports that it's unavailable until then.
