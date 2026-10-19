"""High-level API for models (and the MCP server): design Zooks, preview them, run contests.

    from bamzooki.studio import Studio
    s = Studio(model="claude-opus-5-5")
    r = s.save_zook({"name": "Stomper", "body": {...}})   # validate, finalise in the game, preview
    s.run("Zook Sumo", green="Stomper", red="Ant")

Every engine call (finalise, preview, contest) runs the real game on screen, one at a time.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from pathlib import Path

from .runner import TRIALS, Engine, Job, contests
from .runner.contests import RULES
from .sdk import schema
from .sdk.store import Zoo, ZookRef, safe_name
from .sdk.zook import SpecError, Zook, apply_edits, validate_spec

DEFAULT_VIEWS = ("three_quarter", "front", "top")

GUIDE_PATH = Path(__file__).with_name("docs") / "zook_design_guide.md"


class Studio:
    def __init__(self, model: str = "manual", engine: Engine | None = None):
        self.engine = engine or Engine()
        self.install = self.engine.install
        self.model = safe_name(model, "model")
        self.engine.prepare()
        self.zoo = Zoo(self.install, examples_root=self.engine.root / "Teams" / "examples" / "Creatures")
        self.lock = threading.Lock()
        self.work = self.install.config.cache_dir / "studio"
        self.work.mkdir(parents=True, exist_ok=True)

    # --- reference data ------------------------------------------------------------
    def contests(self) -> list[dict]:
        return [{"name": n, "contest_file": c.contest_file, "players": c.players, "colours": c.colours,
                 "in_game_pack": c.in_pack, "rules": RULES.get(c.contest_file, "")}
                for n, c in contests.catalog(self.install).items()]

    def contest(self, name: str) -> dict:
        c = contests.find(name, self.install)
        return {"name": c.name, "contest_file": c.contest_file, "players": c.players, "colours": c.colours,
                "rules": RULES.get(c.contest_file, ""), "behaviours": c.behaviours,
                "notes": "Contest starts ('Go') 4 s after the arena is built. Results are deterministic "
                         "for a given seed."}

    def textures(self) -> list[str]:
        """Texture names (no extension) from the game's team texture folders."""
        names = set()
        for d in (self.engine.root / "NewTeam" / "Textures", self.engine.root / "Teams" / "examples" / "Textures"):
            if d.is_dir():
                names |= {p.stem for p in d.iterdir() if p.suffix.lower() in (".bmp", ".png")}
        return sorted(names)

    def design_guide(self) -> dict:
        return {"guide": GUIDE_PATH.read_text(encoding="utf-8"), "schema": schema.describe(), "textures": self.textures(),
                "example_zooks": self.zoo.example_names(),
                "trials": {k: {"environment": v[0], "time_limit_s": v[1]} for k, v in TRIALS.items()},
                "free_play_environments": self.engine.environments(),
                "edit_ops": "edit_zook takes [{op: set|add|remove, path: 'body.children[0].size.width', value}]"}

    # --- zooks -----------------------------------------------------------------------
    def zooks(self) -> dict:
        return {"examples": self.zoo.example_names(), "mine": self.zoo.designs(self.model),
                "others": {m: self.zoo.designs(m) for m in self.zoo.models() if m != self.model}}

    def _ref(self, ref: str) -> ZookRef:
        return self.zoo.resolve(ref, self.model)

    def get_zook(self, ref: str, include_defaults: bool = False) -> dict:
        r = self._ref(ref)
        z = Zook.load(r.path, self.install.zook_key)
        info = self.zoo.read_info(r)
        return {"ref": r.label, "spec": z.to_spec(compact=not include_defaults), "parts": z.part_count(),
                "stats": z.details(), "notes": info.get("notes"), "previews": info.get("previews", {})}

    def _texture_warnings(self, spec: dict) -> list[str]:
        """Unknown textures render untextured (the engine tolerates them; some examples have them)."""
        warnings: list[str] = []
        known = set(self.textures())

        def walk(p, path):
            if not isinstance(p, dict):
                return
            t = p.get("look", {}).get("texture") if isinstance(p.get("look"), dict) else None
            stem = t.rsplit(".", 1)[0] if isinstance(t, str) and t.lower().endswith((".bmp", ".png")) else t
            if t is not None and known and stem not in known:
                warnings.append(f"{path}.look.texture: unknown texture {t!r}, will render plain")
            for i, c in enumerate(p.get("children", []) or []):
                walk(c, f"{path}.children[{i}]")

        walk(spec.get("body"), "body")
        return warnings

    def validate(self, spec: dict) -> dict:
        problems = validate_spec(spec)
        return {"valid": not problems, "problems": problems, "warnings": self._texture_warnings(spec)}

    def save_zook(self, spec: dict, notes: str = "", views=DEFAULT_VIEWS) -> dict:
        problems = validate_spec(spec)
        if problems:
            return {"saved": False, "problems": problems}
        warnings = self._texture_warnings(spec)
        name = safe_name(spec["name"])
        version, out = self.zoo.new_version(self.model, name)
        draft = self.work / f"draft-{int(time.time() * 1000)}.zook"
        Zook.from_spec(spec, owner=f"{self.model}::bamzooki-harness", notes=notes[:200]).save(
            draft, self.install.zook_key)
        with self.lock:
            res = self.engine.workshop([{"draft": draft, "out": out, "name": name, "owner": f"{self.model}::harness",
                                         "views": list(views), "preview_dir": self.work / "previews"}])[0]
        draft.unlink(missing_ok=True)
        if not out.exists():
            shutil.rmtree(out.parent) if not any(out.parent.iterdir()) else None
            return {"saved": False, "problems": [f"the game could not finalise the design: {res.get('error')}"]}
        previews = {}
        for view, png in res.get("previews", {}).items():
            dst = out.parent / f"{version}.0_{view}.png"
            shutil.move(str(png), dst)
            previews[view] = str(dst)
        final = Zook.load(out, self.install.zook_key)
        info = {"ref": f"{self.model}/{name}@{version}", "spec": final.to_spec(), "submitted_spec": spec,
                "notes": notes, "stats": final.details(), "parts": final.part_count(),
                "clamped": bool(res.get("clamped")), "previews": previews,
                "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        self.zoo.write_info(out, info)
        out_info = {k: info[k] for k in ("ref", "stats", "parts", "clamped", "previews")}
        out_info["saved"] = True
        if warnings:
            out_info["warnings"] = warnings
        if info["clamped"]:
            out_info["note"] = "Some sliders were out of range and were clamped by the game; get_zook shows the result."
        return out_info

    def edit_zook(self, ref: str, edits: list[dict], notes: str = "", new_name: str | None = None) -> dict:
        r = self._ref(ref)
        spec = Zook.load(r.path, self.install.zook_key).to_spec()
        try:
            spec = apply_edits(spec, edits)
        except SpecError as e:
            return {"saved": False, "problems": e.problems}
        if new_name:
            spec["name"] = new_name
        return self.save_zook(spec, notes=notes or f"edited from {r.label}")

    def preview(self, ref: str, views=DEFAULT_VIEWS) -> dict:
        r = self._ref(ref)
        info = self.zoo.read_info(r)
        have = info.get("previews", {})
        if have and all(v in have and Path(have[v]).exists() for v in views):
            return {"ref": r.label, "previews": {v: have[v] for v in views}}
        out = self.work / f"preview-{int(time.time() * 1000)}.zook"
        with self.lock:
            res = self.engine.workshop([{"draft": r.path, "out": out, "name": r.name, "owner": f"{self.model}::harness",
                                         "views": list(views), "preview_dir": self.work / "previews" / r.label.replace("/", "_")}])[0]
        out.unlink(missing_ok=True)
        return {"ref": r.label, "previews": {v: str(p) for v, p in res.get("previews", {}).items()},
                "error": res.get("error")}

    # --- contests ----------------------------------------------------------------------
    def run(self, contest: str, green: str, red: str | None = None, seed: int = 1, video: bool = True,
            record: bool = False, duration: float = 90.0) -> dict:
        info = contests.find(contest, self.install)
        g = self._ref(green)
        rd = self._ref(red) if red else None
        if info.players == 2 and rd is None:
            return {"error": f"{info.name} needs two Zooks (green and red)"}
        job = Job(info, g.path, rd.path if rd else None, seed=seed, duration=duration, video=video,
                  record=record, model=self.model)
        with self.lock:
            r = self.engine.run(job, name=info.contest_file)
        v = r.verdict
        sheet = None
        if r.video:
            # Stills: Go (video starts 1 s before), halfway to the finish, the finish, the end.
            fin = r.finish_time if r.finish_time is not None else None
            times = [1.0, 1.0 + (fin or 10) / 2, 1.0 + (fin if fin is not None else 20), 1e9]
            sheet = self._sheet(r.video, times)
        summary = {
            "contest": info.name, "green": g.label, "red": rd.label if rd else None, "seed": seed,
            "status": r.status, "winner": r.winner,
            "winner_zook": {"green": g.label, "red": rd.label if rd else None}.get(r.winner) if r.winner else None,
            "scores": r.scores, "decided_by": v.decided_by if v else None,
            "finish_after_go_s": None if r.finish_time is None else round(r.finish_time, 2),
            "fell": v.fell if v else {}, "notes": v.notes if v else [],
            "travel": _travel(r.trajectories()),
            "video": str(r.video) if r.video else None, "stills": str(sheet) if sheet else None,
            "replay": str(r.replay) if r.replay else None,
            "run_dir": str(r.run_dir), "error": r.error,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        log = self.zoo.root / self.model / "results.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": "contest", **summary}) + "\n")
        return summary

    def _sheet(self, video: Path, times: list[float]) -> Path | None:
        from .runner.video import VideoError, contact_sheet

        try:
            return contact_sheet(Path(video), times, Path(video).with_name(Path(video).stem + "_stills.png"))
        except VideoError:
            return None

    def trial(self, ref: str, trial: str = "sprint", duration: float = 20.0, video: bool = True) -> dict:
        """Run a Zook Kit trial (sprint, hurdles, lap, block_push, high_jump) or free play in an
        environment ("free:SlopeEnv") on its own. Quicker feedback than a contest."""
        r = self._ref(ref)
        with self.lock:
            res = self.engine.trial(r.path, r.name, trial, duration=duration, video=video, model=self.model)
        sheet = None
        if res.get("video"):
            d = (res.get("moved") or {}).get("seconds") or duration
            sheet = self._sheet(res["video"], [0.5, 3 + d / 3, 3 + 2 * d / 3, 1e9])
        out = {"zook": r.label, "trial": trial, "status": res["status"], "result": res.get("result"),
               "moved": res.get("moved"), "video": str(res["video"]) if res.get("video") else None,
               "stills": str(sheet) if sheet else None, "run_dir": str(res["run_dir"]), "error": res.get("error"),
               "time": time.strftime("%Y-%m-%d %H:%M:%S")}
        log = self.zoo.root / self.model / "results.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": "trial", **out}) + "\n")
        return out

    def results(self, limit: int = 20) -> list[dict]:
        log = self.zoo.root / self.model / "results.jsonl"
        if not log.exists():
            return []
        lines = log.read_text(encoding="utf-8").splitlines()
        return [json.loads(x) for x in lines[-limit:]]


def _travel(tracks: dict) -> dict:
    """Per colour: distance covered on the ground plane and net displacement (arena units)."""
    out = {}
    for colour, pts in tracks.items():
        if len(pts) < 2:
            continue
        path = sum(((b[1] - a[1]) ** 2 + (b[3] - a[3]) ** 2) ** 0.5 for a, b in zip(pts, pts[1:]))
        net = ((pts[-1][1] - pts[0][1]) ** 2 + (pts[-1][3] - pts[0][3]) ** 2) ** 0.5
        out[colour] = {"path": round(path, 2), "net": round(net, 2), "min_height": round(min(p[2] for p in pts), 2)}
    return out
