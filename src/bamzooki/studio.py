"""High-level API for models (and the MCP server): design Zooks, preview them, run contests.

    from bamzooki.studio import Studio
    s = Studio(model="claude-opus-5-5")
    r = s.save_zook({"name": "Stomper", "body": {...}})   # validate, finalise in the game, preview
    s.run("Zook Sumo", green="Stomper", red="Ant")

Every engine call (finalise, preview, contest) runs the real game on screen, one at a time.
"""

from __future__ import annotations

import json
import random
import re
import shutil
import threading
import time
from functools import cached_property
from pathlib import Path

from . import manual, music
from .challenge import Challenge, ChallengeState
from .manual import MODEL_INSTRUCTIONS
from .runner import TRIALS, Engine, Job, contests
from .runner.contests import RULES
from .sdk import schema
from .sdk.store import Zoo, ZookRef, entrant_folder, safe_name
from .sdk.zook import SpecError, Zook, apply_edits, copied_example, spec_warnings, validate_spec

DEFAULT_VIEWS = ("three_quarter", "front", "top")

GUIDE_PATH = MODEL_INSTRUCTIONS / "Zook Design Guide.md"
# The Zook Kit's Test button opens BoxEnv (Builder.sax.lua) with the Builder's ClickableTarget.
TEST_ENV = "free:BoxEnv"
TEST_MAX_S = 30.0  # a test lasts the model's chosen duration, up to this
COPY_REFUSAL = ("This design is too close to one of the example Zooks. Copying an example, whole or with "
                "small tweaks, isn't allowed: design your own Zook, using the examples for ideas, not as a "
                "template.")


# Statuses that are real game results (count as a run; "timeout" = the game's time limit).
PLAYED = ("finished", "timeout")

class Studio:
    """One model's workspace. The model only sees the example Zooks and its own designs; other
    models' designs are hidden and can't be referenced. With a challenge, test runs are budgeted."""

    def __init__(self, model: str | None = "manual", engine: Engine | None = None,
                 challenge: Challenge | None = None, position: str = "none",
                 view_fit: tuple[float, int] | None = None, fullscreen: bool = False, live_sound: bool = False):
        """model: the entrant's full model name (e.g. "anthropic/claude-sonnet-4.5"); its folder
        is "Zook Entries/<name with / replaced>". None = the model registers itself first.
        position, view_fit, fullscreen: how test games and the challenge game show the engine (as in Job).
        live_sound: the challenge game's countdown beeps and music also play on this computer (the
        video has them either way)."""
        self.engine = engine or Engine()
        self.position, self.view_fit, self.fullscreen = position, view_fit, fullscreen
        self.live_sound = live_sound
        self.install = self.engine.install
        self.engine.prepare()
        self.zoo = Zoo(self.install, examples_root=self.engine.root / "Teams" / "examples" / "Creatures")
        self.lock = threading.Lock()
        self.work = self.install.config.cache_dir / "studio"
        self.work.mkdir(parents=True, exist_ok=True)
        self.challenge = challenge
        self.model: str | None = None  # folder name
        self.model_name: str | None = None  # full model name as given
        self.state: ChallengeState | None = None
        if model:
            self._set_model(model)

    def _set_model(self, model: str) -> None:
        self.model_name = model.strip()
        self.model = entrant_folder(model)
        self.state = ChallengeState(self.challenge, self.zoo.root / self.model) if self.challenge else None

    def register(self, model_name: str) -> dict:
        """Let a model name itself when the operator didn't (new names only, so an entrant can't
        take over someone else's folder)."""
        if self.model:
            return {"entrant": self.model_name, "note": "Already registered for this session."}
        folder = entrant_folder(model_name)
        if (self.zoo.root / folder).exists():
            return {"error": f"An entrant {model_name!r} already exists. Use your exact full model name; "
                             "if this is you resuming, the operator must start the server with --model."}
        self._set_model(model_name)
        (self.zoo.root / folder).mkdir(parents=True, exist_ok=True)
        return {"entrant": self.model_name, "folder": folder}

    def _need_model(self) -> None:
        if not self.model:
            raise RuntimeError("Not registered yet: call register_entrant with your full model name first.")

    # --- reference data ------------------------------------------------------------
    def contests(self) -> list[dict]:
        return [{"name": n, "contest_file": c.contest_file, "players": c.players, "colours": c.colours,
                 "in_game_pack": c.in_pack, "rules": RULES.get(c.contest_file, "")}
                for n, c in contests.catalog(self.install).items()]

    def contest(self, name: str) -> dict:
        c = contests.find(name, self.install)
        return {"name": c.name, "contest_file": c.contest_file, "players": c.players, "colours": c.colours,
                "rules": RULES.get(c.contest_file, ""), "behaviours": c.behaviours,
                "notes": "Contest starts ('Go') 4 s after the arena is built. Games aren't deterministic: "
                         "the same two Zooks can get a different result each time."}

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

    def read_manual(self, pages: str = "", search: str = "") -> dict:
        return manual.read(pages, search)

    # --- zooks -----------------------------------------------------------------------
    def zooks(self) -> dict:
        """What this model can use: the example Zooks and its own designs (name -> versions)."""
        self._need_model()
        return {"examples": self.zoo.example_names(), "mine": self.zoo.designs(self.model)}

    def _ref(self, ref: str) -> ZookRef:
        """Resolve a ref, allowing only example Zooks and this model's own designs."""
        if "/" not in ref and not self.model:
            return self.zoo.resolve(f"examples/{ref}")
        self._need_model()
        owner = ref.split("@", 1)[0].rsplit("/", 1)[0] if "/" in ref else None
        if owner not in (None, "examples", self.model):
            raise KeyError(f"{ref!r} is not available: use an example Zook or one of your own designs")
        return self.zoo.resolve(ref, self.model)

    def get_zook(self, ref: str, include_defaults: bool = False) -> dict:
        r = self._ref(ref)
        z = Zook.load(r.path, self.install.zook_key)
        info = self.zoo.read_info(r)
        return {"ref": r.label, "spec": z.to_spec(compact=not include_defaults), "parts": z.part_count(),
                "stats": z.details(), "notes": info.get("notes"), "previews": info.get("previews", {})}

    def _texture_warnings(self, spec: dict) -> list[str]:
        """Unknown textures render untextured (the engine tolerates them; some examples have them).
        Also settings that have no effect (spec_warnings)."""
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
        return warnings + spec_warnings(spec)

    def validate(self, spec: dict) -> dict:
        problems = validate_spec(spec)
        if not problems and self._is_copy(Zook.from_spec(spec)):
            problems = [COPY_REFUSAL]
        return {"valid": not problems, "problems": problems, "warnings": self._texture_warnings(spec)}

    @cached_property
    def _examples(self) -> dict[str, Zook]:
        return {n: Zook.load(self.zoo.resolve(f"examples/{n}").path, self.install.zook_key)
                for n in self.zoo.example_names()}

    def _is_copy(self, zook: Zook) -> bool:
        return copied_example(zook, self._examples) is not None

    def save_zook(self, spec: dict, notes: str = "", views=DEFAULT_VIEWS) -> dict:
        self._need_model()
        problems = validate_spec(spec)
        if problems:
            return {"saved": False, "problems": problems}
        # Which example and which settings stay unsaid, so the check can't be steered around.
        if self._is_copy(Zook.from_spec(spec)):
            return {"saved": False, "problems": [COPY_REFUSAL]}
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
    def run(self, contest: str, green: str, red: str | None = None, seed: int | None = None, video: bool = True,
            record: bool = False, duration: float = 120.0) -> dict:
        """One contest. Models don't choose or see seeds: each game gets a fresh random one (kept in
        the operator's log only), so to them games vary like the real thing."""
        self._need_model()
        if seed is None:
            seed = random.randrange(1, 1_000_000)
        info = contests.find(contest, self.install)
        g = self._ref(green)
        rd = self._ref(red) if red else None
        if info.players == 2 and rd is None:
            return {"error": f"{info.name} needs two Zooks (green and red)"}
        if self.state and (why := self.state.check("run_contest")):
            return {"error": why, "budget": self.state.budget()}
        if self.challenge:  # every contest in a challenge has the same time limit
            duration = self.challenge.time_limit
        vs = f" vs {_short(rd)}" if rd else ""
        job = Job(info, g.path, rd.path if rd else None, seed=seed, duration=duration, video=video,
                  record=record, model=self.model, position=self.position, view_fit=self.view_fit,
                  fullscreen=self.fullscreen,
                  video_path=self._video_path(f"{self._test_label()} - {info.name} - {_short(g)}{vs}"))
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
            "contest": info.name, "green": g.label, "red": rd.label if rd else None,
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
            f.write(json.dumps({"kind": "contest", **summary, "seed": seed}) + "\n")
        vs = rd.label if rd else "-"
        self._spend("run_contest", f"{info.name}: {g.label} vs {vs}", summary, r.status in PLAYED)
        return summary

    def _test_label(self) -> str:
        """'Test 03 - <challenge>': the number this run gets if it counts (a failed run's number is
        used again by the next one). Without a challenge: 'Run'."""
        if not self.state:
            return "Run"
        return f"Test {self.state.runs_used + 1:02d} - {self.challenge.name}"

    def _video_path(self, label: str) -> Path:
        """Zook Entries/<model>/Videos/<label>.mp4 (stills and the .json sidecar go beside it).
        A clash (a failed run's video under the same test number) gets ' (2)', ' (3)', ..."""
        folder = self.zoo.root / self.model / "Videos"
        name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", label).strip(" .")
        path, n = folder / f"{name}.mp4", 2
        while path.exists():
            path, n = folder / f"{name} ({n}).mp4", n + 1
        return path

    def _spend(self, tool: str, detail: str, out: dict, counted: bool) -> None:
        """Count a test run against the challenge budget (failed engine runs are free)."""
        if not self.state:
            return
        if counted:
            self.state.spend(tool, detail)
        out["budget"] = self.state.budget()

    def _sheet(self, video: Path, times: list[float]) -> Path | None:
        from .runner.video import VideoError, contact_sheet

        try:
            return contact_sheet(Path(video), times, Path(video).with_name(Path(video).stem + "_stills.png"))
        except VideoError:
            return None

    def trial(self, ref: str, trial: str = "sprint", duration: float = 20.0, video: bool = True) -> dict:
        """Run a Zook Kit trial (sprint, hurdles, lap, block_push, high_jump) or free play in an
        environment ("free:SlopeEnv") on its own. Quicker feedback than a contest."""
        return self._trial(ref, trial, duration, video, "run_trial", f"Trial {trial.replace(':', ' ')}")

    def test(self, ref: str, duration: float = 10.0, video: bool = True) -> dict:
        """The Zook Kit's Test button: the Zook walks to the Builder's target in BoxEnv, the plain
        test environment, for as long as the model asks (TEST_MAX_S at most). On its own budget
        ("test" in the challenge), so models can test freely."""
        return self._trial(ref, TEST_ENV, min(max(float(duration), 1.0), TEST_MAX_S), video, "test_zook", "Test")

    def _trial(self, ref: str, trial: str, duration: float, video: bool, tool: str, label: str) -> dict:
        self._need_model()
        r = self._ref(ref)
        if self.state and (why := self.state.check(tool)):
            return {"error": why, "budget": self.state.budget()}
        with self.lock:
            res = self.engine.trial(r.path, r.name, trial, duration=duration, video=video, model=self.model,
                                    position=self.position, view_fit=self.view_fit, fullscreen=self.fullscreen,
                                    video_path=self._video_path(f"{self._test_label()} - {label} - {_short(r)}"))
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
            f.write(json.dumps({"kind": "test" if tool == "test_zook" else "trial", **out}) + "\n")
        self._spend(tool, f"{trial}: {r.label}", out, res["status"] in PLAYED)
        return out

    # --- challenge -----------------------------------------------------------------------
    def challenge_info(self) -> dict:
        if self.challenge and not self.model:
            return {**self.challenge.describe(), "registered": False,
                    "next_step": "Call register_entrant with your full model name (e.g. 'anthropic/claude-sonnet-4.5')."}
        if not self.challenge:
            return {"challenge": None, "note": "No challenge configured: test runs are unlimited."}
        return {**self.challenge.describe(), "budget": self.state.budget(),
                "allowed_opponents": {"examples": self.zoo.example_names(),
                                      "your_designs": self.zoo.designs(self.model)},
                "submitted": self.state.data["submissions"]}

    def submit(self, ref: str) -> dict:
        """Play the challenge's single evaluation game with this design (not counted against the budget)."""
        if not self.challenge:
            return {"error": "No challenge configured."}
        self._need_model()
        if why := self.state.can_submit():
            return {"error": why}
        mine = self._ref(ref)
        if mine.owner != self.model:
            return {"error": "Submit one of your own designs."}
        if self._is_copy(Zook.load(mine.path, self.install.zook_key)):
            return {"error": COPY_REFUSAL + " It can't be submitted."}
        g = self.challenge.evaluation
        # The opponent comes from the operator's challenge file, so any Zook may be named there
        # ("Spider" = an example, "<model>/<Name>@n" = a saved design).
        opp = self.zoo.resolve(g.opponent if "/" in g.opponent else f"examples/{g.opponent}")
        info = contests.find(g.contest, self.install)
        green, red = (mine, opp) if g.side == "green" else (opp, mine)
        if info.players == 1:
            green, red = mine, None
        # Played like a knockout game: on screen where the test games go, with the countdown and the music.
        job = Job(info, green.path, red.path if red else None, seed=g.seed, video=True, model=self.model,
                  duration=self.challenge.time_limit, position=self.position, view_fit=self.view_fit,
                  fullscreen=self.fullscreen, countdown=True,
                  music=music.pick_track(music.load_tracks(), self.challenge), live_sound=self.live_sound,
                  video_path=self._video_path(f"Challenge game - {self.challenge.name} - {info.name} - "
                                              f"{_short(mine)} vs {_short(opp)}"))
        with self.lock:
            r = self.engine.run(job, name=f"eval-{info.contest_file}")
        if r.status not in PLAYED:
            return {"error": f"The evaluation game didn't produce a result ({r.status}); your submission was NOT "
                             "used. Call submit_design again.", "status": r.status, "detail": r.error}
        outcome = "win" if r.winner == g.side else ("draw" if r.winner is None else "loss")
        score = {"win": 1.0, "draw": self.challenge.draw_points, "loss": 0.0}[outcome]
        entry = {"design": mine.label, "game": g.public(), "outcome": outcome, "score": score,
                 "status": r.status, "winner": r.winner, "scores": r.scores,
                 "finish_after_go_s": None if r.finish_time is None else round(r.finish_time, 2),
                 "fell": r.verdict.fell if r.verdict else {},
                 "video": str(r.video) if r.video else None, "time": time.strftime("%Y-%m-%d %H:%M:%S")}
        if r.video:
            fin, go = r.finish_time, r.video_lead  # the countdown comes before Go
            sheet = self._sheet(r.video, [go, go + (fin or 10) / 2, go + (fin if fin is not None else 20), 1e9])
            entry["stills"] = str(sheet) if sheet else None
        self.state.record_submission(entry)
        log = self.zoo.root / self.model / "results.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": "submission", **entry, "seed": g.seed}) + "\n")
        return entry

    def results(self, limit: int = 20) -> list[dict]:
        self._need_model()
        log = self.zoo.root / self.model / "results.jsonl"
        if not log.exists():
            return []
        lines = log.read_text(encoding="utf-8").splitlines()
        # Seeds stay in the operator's log; models never see them.
        return [{k: v for k, v in json.loads(x).items() if k != "seed"} for x in lines[-limit:]]


def _short(ref: ZookRef) -> str:
    """A Zook as a video title shows it: 'Spider' (example) or 'Crusher@3'."""
    return ref.name if ref.owner == "examples" else f"{ref.name}@{ref.version}"


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
