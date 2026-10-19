"""Decrypt the install's scripts and decode its Zooks into the gitignored cache.

  python tools/extract.py [--out DIR]

Output (default <cache_dir>/extract/):
  scripts/<install-relative path>.lua   every .ssx/.sax as Lua source
  zooks/<origin>/<relative path>.xml    every .zook (install and save dir) decoded
  archives/<origin>/<relative path>.txt other Bonsai archives (Components, .bmi, ...)
  report.txt                            summary, including Lua version hints

Derived keys are used in memory only and never written or printed.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bamzooki.codec import ArchiveError, archive  # noqa: E402
from bamzooki.install import Install  # noqa: E402

# Lua idioms that distinguish 4.0 / 5.0 / 5.1 (counted over all decrypted sources).
LUA_HINTS = {
    "for k,v in <table> do (4.0/5.0-compat)": rb"\bfor\s+\w+\s*,\s*\w+\s+in\s+(?!pairs|ipairs|next)[\w.\[\]\"]+\s+do\b",
    "pairs()/ipairs() (5.0+)": rb"\b(?:i?pairs)\s*\(",
    "%upvalue (4.0 only)": rb"[\s(,=]%[A-Za-z_]\w*",
    "local function (5.0+)": rb"\blocal\s+function\b",
    "setmetatable (5.0+)": rb"\bsetmetatable\s*\(",
    "settagmethod/tag methods (4.0)": rb"\b(?:settagmethod|newtag|gettagmethod)\s*\(",
    "vararg `arg` table (5.0)": rb"\barg\.n\b|\barg\[\d",
    "Munki class header `# X specializes Y` (agents)": rb"(?m)^#\s*\w+\s+specializes\b",
    "String.* namespace (engine)": rb"\bString\.\w+\s*\(",
    "Number.* namespace (engine)": rb"\bNumber\.\w+\s*\(",
    "getn/tinsert (4.0 names)": rb"\b(?:getn|tinsert|tremove)\s*\(",
    "_VERSION": rb"\b_VERSION\b",
}
EXE_VERSION_RE = rb"\$?Lua(?:: | )(?:Lua )?\d\.\d[\w. ]*|Lua \d\.\d[\w.]*|Copyright \(C\) \d{4}(?:-\d{4})? Tecgraf[^\0]{0,40}"


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, help="output directory (default: <cache_dir>/extract)")
    args = ap.parse_args(argv)

    inst = Install.discover()
    out = args.out or inst.config.cache_dir / "extract"
    report: list[str] = [f"install: {inst.root}", f"save dir: {inst.save_dir}", ""]

    # 1. scripts
    hints: Counter[str] = Counter()
    failures = []
    scripts = inst.scripts()
    for p in scripts:
        rel = p.relative_to(inst.root)
        try:
            src = inst.read_script(str(rel))
        except Exception as e:  # keep going; report at the end
            failures.append(f"{rel}: {e}")
            continue
        _write(out / "scripts" / (str(rel) + ".lua"), src)
        for name, pat in LUA_HINTS.items():
            hints[name] += len(re.findall(pat, src))
    report.append(f"scripts decrypted: {len(scripts) - len(failures)}/{len(scripts)}")
    report += [f"  FAILED {f}" for f in failures]

    # 2. archives (.zook, Components, .bmi, ...) in the install and save dir
    origins = {"install": inst.root}
    if inst.save_dir and inst.save_dir.is_dir():
        origins["save"] = inst.save_dir
    counts: Counter[str] = Counter()
    for origin, root in origins.items():
        for p in sorted(root.rglob("*")):
            if not p.is_file() or p.suffix.lower() in (".ssx", ".sax", ".exe", ".dll", ".png", ".bmp"):
                continue
            with p.open("rb") as f:
                if not archive.is_archive(f.read(len(archive.MAGIC))):
                    continue
            rel = p.relative_to(root)
            data = p.read_bytes()
            plain = None
            for key in (inst.zook_key, b"", inst.registry_key):
                if key is None:
                    continue
                try:
                    header, plain = archive.decode(data, key)
                    break
                except ArchiveError:
                    continue
            if plain is None:
                counts["undecodable"] += 1
                report.append(f"  could not decode {origin}/{rel}")
                continue
            kind = "zooks" if p.suffix.lower() == ".zook" else "archives"
            ext = ".xml" if kind == "zooks" else ".txt"
            _write(out / kind / origin / (str(rel) + ext), plain)
            counts[f"{kind} ({'encrypted' if key else 'plain'})"] += 1
    report.append("archives decoded: " + ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())))

    # 3. Lua version
    exe = inst.exe.read_bytes()
    exe_hits = sorted({m.group(0).decode("latin-1").strip() for m in re.finditer(EXE_VERSION_RE, exe)})
    report += ["", "Lua version strings in Bonsai.exe:"] + [f"  {h!r}" for h in exe_hits[:20]]
    report += ["", "Lua idiom counts across decrypted sources:"]
    report += [f"  {n:6d}  {name}" for name, n in sorted(hints.items(), key=lambda kv: -kv[1])]

    text = "\n".join(report) + "\n"
    _write(out / "report.txt", text.encode())
    print(text)
    print(f"output: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
