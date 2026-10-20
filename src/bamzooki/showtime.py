"""The show around the games: who the models are, what their Zooks look like, and stills of how
a game ended.

    model_facts(model_id, openrouter_info)   a fact sheet (maker, home country, release month...)
    zook_card(zoo_dir, "lab_model/Crusher@3")  a Zook's stats, limbs and preview pictures
    tale_of_the_tape(card_a, card_b)          the two side by side, as text
    tell_apart(card_a, card_b)                how to pick each Zook out in a still (colours, limbs)
    end_of_game_stills(video, finish, out)    a 2x2 sheet: Go, 4 s before the end, just before, the end
    versus_card(png_a, png_b, out)            two preview pictures side by side
    image_part(png)                           a picture as a chat-completions content part
    record_champion / hall_of_fame / arena_record   the Hall of Fame, kept across sessions
    highlights(videos, out)                   a knockout's games joined into one MP4

Facts come from OpenRouter's model list (release date from the dated canonical slug, else the
date it was listed) and the LABS table below, so the announcer has true things to say.
"""

from __future__ import annotations

import base64
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

# OpenRouter id prefix -> (maker, home). Only labs whose home is well known; others get no country
# rather than a guess.
LABS: dict[str, tuple[str, str]] = {
    "openai": ("OpenAI", "San Francisco, USA"),
    "anthropic": ("Anthropic", "San Francisco, USA"),
    "google": ("Google DeepMind", "London, UK and Mountain View, USA"),
    "qwen": ("Alibaba's Qwen team", "Hangzhou, China"),
    "mistralai": ("Mistral AI", "Paris, France"),
    "z-ai": ("Z.ai (Zhipu AI)", "Beijing, China"),
    "thudm": ("Tsinghua University's THUDM", "Beijing, China"),
    "deepseek": ("DeepSeek", "Hangzhou, China"),
    "nvidia": ("NVIDIA", "Santa Clara, USA"),
    "minimax": ("MiniMax", "Shanghai, China"),
    "x-ai": ("xAI", "USA"),
    "moonshotai": ("Moonshot AI", "Beijing, China"),
    "meta": ("Meta", "Menlo Park, USA"),
    "meta-llama": ("Meta", "Menlo Park, USA"),
    "tencent": ("Tencent", "Shenzhen, China"),
    "cohere": ("Cohere", "Toronto, Canada"),
    "bytedance": ("ByteDance", "Beijing, China"),
    "bytedance-seed": ("ByteDance Seed", "Beijing, China"),
    "xiaomi": ("Xiaomi", "Beijing, China"),
    "amazon": ("Amazon", "Seattle, USA"),
    "perplexity": ("Perplexity", "San Francisco, USA"),
    "sakana": ("Sakana AI", "Tokyo, Japan"),
    "thinkingmachines": ("Thinking Machines Lab", "San Francisco, USA"),
    "inception": ("Inception Labs", "Palo Alto, USA"),
    "stepfun": ("StepFun", "Shanghai, China"),
    "inclusionai": ("inclusionAI (Ant Group)", "Hangzhou, China"),
    "upstage": ("Upstage", "South Korea"),
    "nousresearch": ("Nous Research", "USA"),
    "liquid": ("Liquid AI", "Cambridge, Massachusetts, USA"),
    "ibm-granite": ("IBM", "Armonk, USA"),
    "microsoft": ("Microsoft", "Redmond, USA"),
    "arcee-ai": ("Arcee AI", "USA"),
    "writer": ("Writer", "San Francisco, USA"),
    "meituan": ("Meituan", "Beijing, China"),
    "ai21": ("AI21 Labs", "Tel Aviv, Israel"),
    "01-ai": ("01.AI", "Beijing, China"),
    "baidu": ("Baidu", "Beijing, China"),
    "allenai": ("Ai2 (Allen Institute for AI)", "Seattle, USA"),
    "moondream": ("Moondream", "Seattle, USA"),
}

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December"]


def _lab_prefix(model_id: str) -> str:
    return model_id.lstrip("~").split("/", 1)[0].lower()


def release_date(info: dict) -> tuple[int, int, int] | None:
    """(year, month, day) the model came out: the date in OpenRouter's canonical slug
    ("anthropic/claude-haiku-5.5-20261007"), else the day it was listed there."""
    m = re.search(r"(20\d\d)-?(0[1-9]|1[0-2])-?([0-3]\d)$", str(info.get("canonical_slug") or ""))
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))
    created = info.get("created")
    if isinstance(created, (int, float)) and created > 0:
        t = time.gmtime(created)
        return t.tm_year, t.tm_mon, t.tm_mday
    return None


def _per_million(price) -> float | None:
    try:
        return round(float(price) * 1e6, 4)
    except (TypeError, ValueError):
        return None


def model_facts(model_id: str, info: dict, today: tuple[int, int, int] | None = None) -> dict:
    """What the announcer may say about a model: only facts from OpenRouter and LABS."""
    maker, home = LABS.get(_lab_prefix(model_id), (None, None))
    name = re.sub(r"^[^:]{1,40}:\s*", "", str(info.get("name") or model_id.split("/")[-1]))
    facts: dict = {"id": model_id, "name": name}
    if maker:
        facts["maker"] = maker
    if home:
        facts["home"] = home
        facts["country"] = " and ".join(part.rsplit(", ", 1)[-1] for part in home.split(" and "))
    if rel := release_date(info):
        facts["released"] = f"{MONTHS[rel[1] - 1]} {rel[0]}"
        facts["released_on"] = f"{rel[0]}-{rel[1]:02d}-{rel[2]:02d}"
        today = today or time.localtime()[:3]
        days = _days_between(rel, today)
        if days is not None and days >= 0:
            facts["age"] = _age(days)
    if info.get("knowledge_cutoff"):
        facts["knowledge_cutoff"] = info["knowledge_cutoff"]
    if info.get("context_length"):
        facts["context_window_tokens"] = int(info["context_length"])
    mods = (info.get("architecture") or {}).get("input_modalities") or []
    if mods:
        facts["can_see_images"] = "image" in mods
        facts["inputs"] = mods
    p = info.get("pricing") or {}
    pin, pout = _per_million(p.get("prompt")), _per_million(p.get("completion"))
    if pin is not None and pout is not None and pin >= 0 and pout >= 0:
        facts["price_per_million_tokens"] = "free" if pin == pout == 0 else f"${pin:g} in, ${pout:g} out"
    if (r := info.get("reasoning")) and isinstance(r, dict):
        facts["thinks_before_answering"] = bool(r.get("default_enabled") or r.get("mandatory"))
    if aa := ((info.get("benchmarks") or {}).get("artificial_analysis") or {}):
        facts["artificial_analysis_indices"] = {k: v for k, v in aa.items() if isinstance(v, (int, float))}
    if desc := str(info.get("description") or "").strip():
        facts["description"] = desc[:700]
    return facts


def _days_between(a: tuple[int, int, int], b: tuple[int, int, int]) -> int | None:
    import datetime as dt

    try:
        return (dt.date(*b) - dt.date(*a)).days
    except ValueError:
        return None


def _age(days: int) -> str:
    if days < 2:
        return "brand new (released in the last day)"
    if days < 14:
        return f"{days} days old"
    if days < 60:
        return f"{days // 7} weeks old"
    if days < 730:
        return f"about {round(days / 30.44)} months old"
    return f"about {days / 365.25:.0f} years old"


def card_line(facts: dict) -> str:
    """One line for the screen: 'Claude Haiku 5.5 | Anthropic | San Francisco, USA | released October 2026'."""
    parts = [facts.get("name", facts.get("id", "?"))]
    parts += [facts[k] for k in ("maker", "home") if facts.get(k)]
    if facts.get("released"):
        parts.append(f"released {facts['released']}" + (f" ({facts['age']})" if facts.get("age") else ""))
    if "can_see_images" in facts:
        parts.append("sees images" if facts["can_see_images"] else "text only")
    return " | ".join(parts)


# --- the Hall of Fame (every knockout champion, kept across sessions) -----------------------

HALL_OF_FAME = "Hall of Fame.jsonl"


def record_champion(arena_dir: Path, entry: dict) -> None:
    """Add a knockout champion to <arena>/Hall of Fame.jsonl (it isn't archived with Zook Entries)."""
    path = Path(arena_dir) / HALL_OF_FAME
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"date": time.strftime("%Y-%m-%d %H:%M"), **entry}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def hall_of_fame(arena_dir: Path) -> list[dict]:
    path = Path(arena_dir) / HALL_OF_FAME
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def arena_record(hall: list[dict], model: str) -> str | None:
    """'won the arena knockout twice before (last on 2026-10-20 with Sumobot); runner-up once' or None."""
    won = [h for h in hall if h.get("champion") == model]
    second = sum(1 for h in hall if h.get("runner_up") == model)
    bits = []
    if won:
        times = {1: "once", 2: "twice"}.get(len(won), f"{len(won)} times")
        last = won[-1]
        bits.append(f"won the arena knockout {times} before (last on {str(last.get('date', '?'))[:10]} "
                    f"with {last.get('zook_name', '?')})")
    if second:
        bits.append(f"runner-up {({1: 'once', 2: 'twice'}.get(second, f'{second} times'))}")
    return "; ".join(bits) or None


# --- Zooks --------------------------------------------------------------------------------

@dataclass
class ZookCard:
    ref: str  # "lab_model/Crusher@3" or "examples/Spider"
    name: str
    stats: dict = field(default_factory=dict)  # Height, Length, Width, Weight, Components (game's strings)
    limbs: int | None = None  # moving limbs on the body (legs, and arms or jaws that move), mirrored ones twice
    texture: str = ""  # the body's
    colour: str = ""  # the body's, as the nearest named colour
    cycle_speed: float | None = None
    previews: dict[str, str] = field(default_factory=dict)  # view -> png
    notes: str = ""
    limb_colour: str = ""  # the commonest limb colour (else texture)

    @property
    def look(self) -> str:
        """'metal, grey'"""
        return ", ".join(x for x in (self.texture, self.colour) if x)

    def looks(self) -> str:
        """'grey metal body, 6 red limbs': enough to pick it out in a still."""
        body = " ".join(x for x in (self.colour, self.texture, "body") if x) if self.look else ""
        limbs = (" ".join(x for x in (str(self.limbs or ""), self.limb_colour, "limbs") if x)
                 if self.limbs or self.limb_colour else "")
        return ", ".join(x for x in (body, limbs) if x)

    def picture(self, *views: str) -> list[str]:
        """Existing preview files for these views (default: three_quarter, front)."""
        return [self.previews[v] for v in (views or ("three_quarter", "front"))
                if v in self.previews and Path(self.previews[v]).exists()]

    def summary(self) -> str:
        """One line: 'Crusher: 0.667 kg, 13 parts, 6 limbs, 12.0 x 18.5 x 14.8 cm (h x w x l), metal grey'."""
        s = self.stats
        bits = [s.get("Weight"), f"{s['Components']} parts" if s.get("Components") else None,
                f"{self.limbs} limbs" if self.limbs is not None else None]
        if all(s.get(k) for k in ("Height", "Width", "Length")):
            bits.append(f"{_num(s['Height'])} x {_num(s['Width'])} x {s['Length']} (h x w x l)")
        if self.look:
            bits.append(self.look)
        return f"{self.name}: " + ", ".join(b for b in bits if b)


def _num(v: str) -> str:
    return str(v).split()[0]


def zook_card(zoo_dir: Path, ref: str) -> ZookCard:
    """The card for a submitted design "<folder>/<Name>@<n>" (from its saved .json). Missing
    files give a card with just the name."""
    owner, _, rest = ref.partition("/")
    name, _, version = rest.partition("@")
    card = ZookCard(ref, name or ref)
    info_path = Path(zoo_dir) / owner / name / f"{version}.0.json"
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return card
    card.stats = dict(info.get("stats") or {})
    card.previews = {k: str(v) for k, v in (info.get("previews") or {}).items()}
    card.notes = str(info.get("notes") or "")
    spec = info.get("spec") or {}
    body = spec.get("body") or {}
    card.limbs = count_limbs(body)
    card.texture, card.colour = look_parts(body.get("look") or {})
    card.limb_colour = commonest_limb_colour(body)
    card.cycle_speed = (body.get("gait") or {}).get("cycle_speed")
    return card


def commonest_limb_colour(body: dict) -> str:
    """The most common colour among the body's limbs (else their texture; "" if neither is set)."""
    from collections import Counter

    seen: Counter = Counter()

    def walk(part):
        for c in part.get("children") or []:
            if isinstance(c, dict):
                texture, colour = look_parts(c.get("look") or {})
                if colour := colour or texture:
                    seen[colour] += 2 if c.get("mirror") else 1
                walk(c)
    walk(body)
    return seen.most_common(1)[0][0] if seen else ""


def tell_apart(*cards: "ZookCard") -> str:
    """'Sumobot: grey metal body, 6 metal limbs. BulldozerSpider: tiger body, 10 red limbs.' so a
    model looking at stills knows which Zook is which (the green/red sides aren't their colours)."""
    return " ".join(f"{c.name}: {c.looks()}." for c in cards if c.looks())


def count_limbs(body: dict) -> int:
    """Limbs attached to the body that move (a mirrored one is two)."""
    n = 0
    for c in body.get("children") or []:
        if not isinstance(c, dict):
            continue
        moves = bool(c.get("movement")) or any(isinstance(g, dict) and g.get("movement")
                                                for g in c.get("children") or [])
        if moves:
            n += 2 if c.get("mirror") else 1
    return n


COLOURS = {"red": (1, 0, 0), "orange": (1, .55, 0), "yellow": (1, 1, 0), "green": (0, .7, 0),
           "blue": (0, .3, 1), "purple": (.55, 0, .8), "pink": (1, .5, .75), "brown": (.5, .3, .1),
           "black": (0, 0, 0), "white": (1, 1, 1), "grey": (.5, .5, .5), "teal": (0, .6, .6)}


def look_parts(look: dict) -> tuple[str, str]:
    """(texture, nearest named colour) of a part's look; "" where it has none."""
    texture = str(look.get("texture") or "").rsplit(".", 1)[0]
    colour = ""
    rgb = look.get("rgb")
    if isinstance(rgb, (list, tuple)) and len(rgb) == 3:
        try:
            r, g, b = (float(x) for x in rgb)
            colour = min(COLOURS, key=lambda c: sum((x - y) ** 2 for x, y in zip(COLOURS[c], (r, g, b))))
        except (TypeError, ValueError):
            pass
    return texture, colour


def tale_of_the_tape(a: ZookCard, b: ZookCard, a_label: str, b_label: str) -> list[str]:
    """Two Zooks side by side, a boxing-style tale of the tape (text lines)."""
    rows = [("", a_label, b_label), ("Zook", a.name, b.name)]
    for key in ("Weight", "Components", "Height", "Width", "Length"):
        if a.stats.get(key) or b.stats.get(key):
            rows.append((key if key != "Components" else "Parts", a.stats.get(key, "?"), b.stats.get(key, "?")))
    if a.limbs is not None or b.limbs is not None:
        rows.append(("Limbs", str(a.limbs if a.limbs is not None else "?"),
                     str(b.limbs if b.limbs is not None else "?")))
    if a.cycle_speed is not None or b.cycle_speed is not None:
        rows.append(("Stride speed", f"{a.cycle_speed:g}" if a.cycle_speed is not None else "?",
                     f"{b.cycle_speed:g}" if b.cycle_speed is not None else "?"))
    if a.look or b.look:
        rows.append(("Look", a.look or "?", b.look or "?"))
    w0 = max(len(r[0]) for r in rows)
    w1 = max(len(str(r[1])) for r in rows)
    w2 = max(len(str(r[2])) for r in rows)
    return [f"{str(r[1]).rjust(w1)}  {r[0].center(w0)}  {str(r[2]).ljust(w2)}".rstrip() for r in rows]


# --- pictures -----------------------------------------------------------------------------

def image_part(png: str | Path, detail: str | None = None) -> dict | None:
    """A PNG as a chat-completions image part (data URL); None if the file isn't there."""
    try:
        data = Path(png).read_bytes()
    except OSError:
        return None
    url = {"url": f"data:image/png;base64,{base64.b64encode(data).decode('ascii')}"}
    if detail:
        url["detail"] = detail
    return {"type": "image_url", "image_url": url}


END_LABELS = "Go | 4 s before the end / just before the end | the end"


def end_of_game_times(finish_after_go: float | None, duration: float) -> list[float]:
    """Video times (s) for the end-of-game sheet. Videos start 1 s before Go. Without a finish
    (a draw at the time limit) the end of the video is the end of the game."""
    end = duration if finish_after_go is None else min(duration, 1.0 + finish_after_go)
    return [1.0, max(1.0, end - 4.0), max(1.0, end - 1.0), min(duration, end + 1.5) if finish_after_go is not None
            else duration]


def end_of_game_stills(video: Path, finish_after_go: float | None, out: Path) -> Path | None:
    """A 2x2 sheet of how a game ended (END_LABELS), or None if ffmpeg can't make one."""
    from .runner.video import VideoError, contact_sheet, video_duration

    try:
        dur = video_duration(Path(video))
        out.parent.mkdir(parents=True, exist_ok=True)
        return contact_sheet(Path(video), end_of_game_times(finish_after_go, dur), out)
    except (VideoError, OSError, ValueError):
        return None


@dataclass
class ReelGame:
    """One game in the highlights: its video and who played it."""
    video: Path
    title: str  # "Zook Sumo knockout - Final - game 1"
    green: tuple[str, str]  # (model, Zook name)
    red: tuple[str, str]


# Fonts for the reel's titles (bold first); the first that exists is used.
FONTS = ["C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arial.ttf",
         "/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/Library/Fonts/Arial Bold.ttf",
         "/System/Library/Fonts/Supplemental/Arial.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
GREEN, RED, GOLD, GREY, WHITE = "0x5BE37D", "0xFF6B6B", "0xFFD54A", "0xC8CDD2", "white"
CARD_BG = "0x11161C"


def find_font() -> Path | None:
    import os

    windir = os.environ.get("WINDIR")
    candidates = ([f"{windir}/Fonts/arialbd.ttf"] if windir else []) + FONTS
    return next((Path(f) for f in candidates if Path(f).is_file()), None)


def _fit(text: str, size: int, width: float) -> int:
    """A font size at most `size` at which `text` fits in `width` pixels (about 0.6 em a character)."""
    return max(12, min(size, int(width / (0.6 * max(1, len(text))))))


class _Titles:
    """drawtext filters whose text and font come from files in the working folder, so nothing
    (model names, Windows paths) needs ffmpeg's filter quoting."""

    def __init__(self, folder: Path):
        self.folder, self.n = folder, 0

    def text(self, text: str, size: int, colour: str, x: str, y: str) -> str:
        self.n += 1
        (self.folder / f"t{self.n}.txt").write_text(text, encoding="utf-8")
        return (f"drawtext=fontfile=font.ttf:textfile=t{self.n}.txt:expansion=none:fontsize={size}:"
                f"fontcolor={colour}:shadowcolor=black@0.7:shadowx=2:shadowy=2:x={x}:y={y}")


def _short_model(model: str) -> str:
    return model.split("/")[-1]


def _card(t: _Titles, w: int, h: int, fps: str, seconds: float, lines: list[tuple[str, int, str, float]]) -> str:
    """A title card: (text, size, colour, top as a fraction of the height) per line, centred, fading in and out."""
    draws = [t.text(text, _fit(text, size, w * 0.9), colour, "(w-text_w)/2", str(int(h * top)))
             for text, size, colour, top in lines if text]
    fade = min(0.4, seconds / 4)
    return ",".join([f"color=c={CARD_BG}:s={w}x{h}:r={fps}:d={seconds}", "format=yuv420p", "setsar=1", *draws,
                     f"fade=t=in:st=0:d={fade}", f"fade=t=out:st={seconds - fade:.2f}:d={fade}"])


def _caption(t: _Titles, w: int, h: int, game: ReelGame) -> list[str]:
    """A bar along the top during the game: green Zook (model) on the left, red on the right."""
    bar = max(28, h // 14)
    left = f"{game.green[1]} ({_short_model(game.green[0])})"
    right = f"{game.red[1]} ({_short_model(game.red[0])})"
    size = min(_fit(left, bar * 55 // 100, w * 0.45), _fit(right, bar * 55 // 100, w * 0.45))
    y = f"({bar}-text_h)/2"
    return [f"drawbox=x=0:y=0:w=iw:h={bar}:color=black@0.55:t=fill",
            t.text(left, size, GREEN, str(w // 50), y),
            t.text("v", size, WHITE, "(w-text_w)/2", y),
            t.text(right, size, RED, f"w-text_w-{w // 50}", y)]


def _probe(video: Path) -> tuple[int, int, str] | None:
    """(width, height, frame rate as ffmpeg writes it, e.g. '25/1') of a video."""
    import subprocess

    from .runner.video import find_ffmpeg

    exe = Path(find_ffmpeg())
    probe = exe.with_name("ffprobe" + exe.suffix)
    res = subprocess.run([str(probe if probe.exists() else "ffprobe"), "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height,r_frame_rate", "-of", "csv=p=0", str(video)],
                         capture_output=True, text=True)
    parts = res.stdout.strip().split(",")
    if res.returncode != 0 or len(parts) != 3:
        return None
    return int(parts[0]), int(parts[1]), parts[2]


def titled_reel(games: list[ReelGame], out: Path, champion: tuple[str, str] | None = None,
                card_seconds: float = 3.0, font: Path | None = None, music=None,
                music_fade: float = 1.0) -> Path | None:
    """The games one after another, each after a title card (the round and game, then each Zook in
    its arena colour with its model's name) and with a caption bar naming both while it plays,
    then a champion card. With `music` (a music.Track) its loop plays under each game from the top
    and fades out over `music_fade` seconds as the game ends; the cards are silent. Made by ffmpeg
    alone (no model calls). None if it can't be made."""
    import shutil
    import subprocess
    import tempfile

    from .runner.video import VideoError, find_ffmpeg, video_duration

    games = [g for g in games if g.video and Path(g.video).exists()]
    font = font or find_font()
    if not games or not font or not Path(font).is_file():
        return None
    try:
        exe = find_ffmpeg()
        size = _probe(Path(games[0].video))
    except (VideoError, OSError):
        return None
    if not size:
        return None
    w, h, fps = size
    out = Path(out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        shutil.copyfile(font, folder / "font.ttf")
        loop = _music_loop(exe, music, folder) if music else None
        t = _Titles(folder)
        inputs, chains, parts = [], [], []
        silence = "anullsrc=channel_layout=stereo:sample_rate=48000,atrim=0:{:.3f}"

        def add_input(*args: str) -> int:
            """Add an input (its options, then '-i', path); its index for the filter graph."""
            inputs.extend(args)
            return inputs.count("-i") - 1

        for i, g in enumerate(games):
            v = add_input("-i", str(Path(g.video).resolve()))
            chains.append(_card(t, w, h, fps, card_seconds, [
                (g.title, h // 14, WHITE, 0.16),
                (g.green[1], h // 9, GREEN, 0.32), (g.green[0], h // 24, GREY, 0.45),
                ("v", h // 14, WHITE, 0.53),
                (g.red[1], h // 9, RED, 0.64), (g.red[0], h // 24, GREY, 0.77)]) + f"[c{i}]")
            chains.append(",".join([f"[{v}:v]scale={w}:{h}", "setsar=1", f"fps={fps}", "format=yuv420p",
                                    *_caption(t, w, h, g)]) + f"[g{i}]")
            parts += [f"[c{i}]", f"[g{i}]"]
            if loop:
                # the loop again for every game, from its top, cut to the game's length
                k = add_input("-stream_loop", "-1", "-i", loop.name)
                seconds = video_duration(Path(g.video))
                fade = min(music_fade, seconds / 2)
                chains.append(silence.format(card_seconds) + f"[ca{i}]")
                chains.append(f"[{k}:a]atrim=0:{seconds:.3f},asetpts=PTS-STARTPTS,volume={music.volume},"
                              f"afade=t=in:st=0:d=0.03,afade=t=out:st={seconds - fade:.3f}:d={fade:.3f},"
                              f"aformat=sample_rates=48000:channel_layouts=stereo[ga{i}]")
                parts[-2:] = [f"[c{i}][ca{i}]", f"[g{i}][ga{i}]"]
        if champion:
            chains.append(_card(t, w, h, fps, card_seconds + 1, [
                ("CHAMPION", h // 9, GOLD, 0.25), (champion[1], h // 8, WHITE, 0.43),
                (champion[0], h // 20, GREY, 0.6)]) + "[champ]")
            parts.append("[champ]")
            if loop:
                chains.append(silence.format(card_seconds + 1) + "[champa]")
                parts[-1] = "[champ][champa]"
        streams = "[out][aout]" if loop else "[out]"
        chains.append("".join(parts) + f"concat=n={len(parts)}:v=1:a={1 if loop else 0}{streams}")
        cmd = [exe, "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(chains), "-map", "[out]",
               *(["-map", "[aout]", "-c:a", "aac", "-b:a", "192k"] if loop else []),
               "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-movflags", "+faststart", str(out)]
        try:
            res = subprocess.run(cmd, cwd=folder, capture_output=True)
        except OSError:
            return None
    return out if res.returncode == 0 and out.exists() else None


def _music_loop(exe: str, track, folder: Path) -> Path | None:
    """The track's loop (loop_start to loop_end) as loop.wav in `folder`, or None."""
    import subprocess

    end = f":end={track.loop_end}" if track.loop_end else ""
    out = folder / "loop.wav"
    res = subprocess.run([exe, "-y", "-loglevel", "error", "-i", str(Path(track.file).resolve()), "-af",
                          f"atrim=start={track.loop_start}{end},asetpts=PTS-STARTPTS", "-ar", "48000", "-ac", "2",
                          str(out)], capture_output=True)
    return out if res.returncode == 0 and out.exists() else None


def highlights(games: list[ReelGame | Path], out: Path, champion: tuple[str, str] | None = None,
               music=None) -> Path | None:
    """The games one after another in a single MP4: with title cards, captions and `music` (a
    music.Track) when they're ReelGames and a font is found (titled_reel), else simply joined.
    None if neither works."""
    if games and all(isinstance(g, ReelGame) for g in games):
        if reel := titled_reel(games, out, champion, music=music):
            return reel
    return join_videos([g.video if isinstance(g, ReelGame) else g for g in games], out)


def join_videos(videos: list[Path], out: Path) -> Path | None:
    """Videos from the same capture one after another, joined without re-encoding, or None."""
    import subprocess
    import tempfile

    from .runner.video import VideoError, find_ffmpeg

    videos = [Path(v) for v in videos if v and Path(v).exists()]
    if not videos:
        return None
    try:
        exe = find_ffmpeg()
    except VideoError:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        listing = Path(tmp) / "games.txt"
        quoted = (v.resolve().as_posix().replace("'", "'\\''") for v in videos)  # ffmpeg concat quoting
        listing.write_text("".join(f"file '{q}'\n" for q in quoted), encoding="utf-8")
        cmd = [exe, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy",
               "-movflags", "+faststart", str(out)]
        try:
            res = subprocess.run(cmd, capture_output=True)
        except OSError:
            return None
    return out if res.returncode == 0 and out.exists() else None


def versus_card(left: str | Path, right: str | Path, out: Path) -> Path | None:
    """Two preview pictures side by side (left: green, right: red), or None."""
    import subprocess

    from .runner.video import VideoError, find_ffmpeg

    try:
        exe = find_ffmpeg()
    except VideoError:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [exe, "-y", "-loglevel", "error", "-i", str(left), "-i", str(right), "-filter_complex",
           "[0:v]scale=512:512[a];[1:v]scale=512:512[b];[a][b]hstack=inputs=2[out]", "-map", "[out]",
           "-frames:v", "1", "-update", "1", str(out)]
    try:
        res = subprocess.run(cmd, capture_output=True)
    except OSError:
        return None
    return out if res.returncode == 0 and out.exists() else None
