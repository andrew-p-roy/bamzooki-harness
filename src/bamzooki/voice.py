"""The announcer's voice (--speak): what it says is read out loud on background threads, one line
after another, so the show never waits for it.

With the optional Kokoro engine (pip install kokoro-onnx; its ~340 MB model is downloaded into the
cache on first use) the default is a natural British male voice, bm_george, a touch faster than
Kokoro's own pace ([arena] voice_speed, default 1.1); [arena] voice picks
another Kokoro voice (bm_lewis, bm_daniel, bm_fable, bf_emma, ...). Each line is read sentence by
sentence, the next one synthesised while the current one plays, so there are no gaps.

Without it, the system's own speech engine reads each line: Windows System.Speech through PowerShell,
macOS `say`, Linux `espeak` (choose a Windows voice with [arena] voice = "Microsoft Hazel Desktop";
see `voices()`).
"""

from __future__ import annotations

import io
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import wave
from pathlib import Path

KOKORO_DEFAULT = "bm_george"
KOKORO_SPEED = 1.1  # a touch quicker than Kokoro's own pace: livelier for an announcer
KOKORO_FILES = {  # the kokoro-onnx project's published model files
    "kokoro-v1.0.onnx": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx",
    "voices-v1.0.bin": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin",
}
KOKORO_VOICE = re.compile(r"^[abefhijpz][fm]_[a-z]+$")  # e.g. bm_george, bf_emma, am_adam
# The contestants' own voices (presentations, trash talk, interviews, answers), by lab; [arena] voices
# overrides by lab or full model id. Other labs get one from POOL, the same one every time.
LAB_VOICES = {"google": "af_heart", "qwen": "bf_emma", "openai": "am_puck", "anthropic": "am_michael"}
POOL = ["am_fenrir", "af_nicole", "bf_isabella", "am_eric", "af_sarah", "bm_fable", "af_bella", "am_liam",
        "bf_lily", "am_adam"]

PS_SPEAK = ("[Console]::InputEncoding = [Text.Encoding]::UTF8; Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; $s.Rate = {rate}; {voice}{output}"
            "$s.Speak([Console]::In.ReadToEnd())")
PS_VOICES = ("Add-Type -AssemblyName System.Speech; (New-Object System.Speech.Synthesis.SpeechSynthesizer)"
             ".GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }")


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def speech_command(voice: str | None = None, rate: int = 1, wav: str | None = None) -> list[str] | None:
    """The command that reads text from stdin out loud (or into `wav`, Windows only); None if this
    system has no speech engine we know."""
    if sys.platform == "win32":
        ps = shutil.which("powershell") or shutil.which("pwsh")
        if not ps:
            return None
        script = PS_SPEAK.format(rate=max(-10, min(10, rate)),
                                 voice=f"$s.SelectVoice({_ps_quote(voice)}); " if voice else "",
                                 output=f"$s.SetOutputToWaveFile({_ps_quote(wav)}); " if wav else "")
        return [ps, "-NoProfile", "-NonInteractive", "-Command", script]
    if sys.platform == "darwin" and shutil.which("say"):
        return ["say"] + (["-v", voice] if voice else []) + ["-f", "-"]
    if exe := shutil.which("espeak-ng") or shutil.which("espeak"):
        return [exe] + (["-v", voice] if voice else []) + ["--stdin"]
    return None


def voices() -> list[str]:
    """Installed Windows voice names (empty elsewhere)."""
    ps = shutil.which("powershell") if sys.platform == "win32" else None
    if not ps:
        return []
    res = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command", PS_VOICES], capture_output=True,
                         text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return [v.strip() for v in res.stdout.splitlines() if v.strip()]


def speakable(text: str) -> str:
    """Text as it should be read: no markdown, emoji or bracketed asides."""
    text = re.sub(r"\[[^\]]*\]", " ", text)
    text = re.sub(r"[*_`#>|~]", " ", text)
    text = "".join(ch for ch in text if ord(ch) < 0x2190 or ch in "‘’“”")
    return re.sub(r"\s+", " ", text).strip()


def sentences(text: str, min_len: int = 40) -> list[str]:
    """Text in sentence-sized pieces for synthesis; short sentences join the next one, so a piece
    keeps its intonation and the first one is still quick to synthesise."""
    out: list[str] = []
    for part in re.split(r"(?<=[.!?])\s+", text.strip()):
        if out and len(out[-1]) < min_len:
            out[-1] += " " + part
        elif part:
            out.append(part)
    return out


def model_voice(model: str, overrides: dict | None = None) -> str:
    """The Kokoro voice a contestant speaks with."""
    import zlib

    lab = model.lstrip("~").split("/")[0]
    overrides = overrides or {}
    return (overrides.get(model) or overrides.get(lab) or LAB_VOICES.get(lab)
            or POOL[zlib.crc32(lab.encode()) % len(POOL)])


def kokoro_installed() -> bool:
    try:
        import kokoro_onnx  # noqa: F401
    except ImportError:
        return False
    return True


def kokoro_model(folder: Path) -> tuple[Path, Path]:
    """The model files in `folder`, downloaded first if missing."""
    folder.mkdir(parents=True, exist_ok=True)
    for name, url in KOKORO_FILES.items():
        path = folder / name
        if not path.exists():
            part = path.with_suffix(path.suffix + ".part")
            urllib.request.urlretrieve(url, part)
            part.replace(path)
    return folder / "kokoro-v1.0.onnx", folder / "voices-v1.0.bin"


def wav_bytes(samples, rate: int) -> bytes:
    import numpy as np

    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def play_wav(data: bytes) -> None:
    """Play WAV bytes to the end (Windows: winsound; elsewhere afplay / aplay)."""
    if sys.platform == "win32":
        import winsound

        winsound.PlaySound(data, winsound.SND_MEMORY)
        return
    player = shutil.which("afplay") or shutil.which("aplay") or shutil.which("paplay")
    if player:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(data)
        subprocess.run([player, f.name], capture_output=True)
        Path(f.name).unlink(missing_ok=True)


class Voice:
    """Kokoro when it's installed and the voice is a Kokoro one (the default), else the system's
    speech engine. `cache` is where the Kokoro model is kept (downloaded on first use)."""

    def __init__(self, voice: str | None = None, rate: int = 1, cache: Path | None = None,
                 speed: float = KOKORO_SPEED):
        self.lines: queue.Queue[tuple[str, str | None]] = queue.Queue()  # (text, Kokoro voice or None)
        self.kokoro = kokoro_installed() and cache is not None and (voice is None or bool(KOKORO_VOICE.match(voice)))
        self.command = None if self.kokoro else speech_command(voice, rate)
        self.name = (voice or KOKORO_DEFAULT) if self.kokoro else voice
        if self.kokoro:
            self.cache, self.speed = cache, speed
            self.audio: queue.Queue[bytes] = queue.Queue(maxsize=2)  # synthesised ahead of playback
            threading.Thread(target=self._synthesise, daemon=True).start()
            threading.Thread(target=self._play, daemon=True).start()
        elif self.command:
            threading.Thread(target=self._run, daemon=True).start()

    @property
    def available(self) -> bool:
        return self.kokoro or self.command is not None

    def say(self, text: str, voice: str | None = None) -> None:
        """Queue a line; voice: a Kokoro voice for this line (a contestant's), else the announcer's."""
        if self.available and (text := speakable(text)):
            self.lines.put((text, voice))

    def say_now(self, text: str) -> None:
        """Live commentary: drop whatever hasn't been spoken yet (the line playing finishes), then say this."""
        for q in (self.lines, getattr(self, "audio", None)):
            while q is not None:
                try:
                    q.get_nowait()
                except Exception:
                    break
        self.say(text)

    def _run(self) -> None:
        while True:
            text, _ = self.lines.get()  # the system engine has the one voice
            try:
                subprocess.run(self.command, input=text.encode("utf-8"), capture_output=True, timeout=180,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, subprocess.SubprocessError):
                pass

    def _synthesise(self) -> None:
        from kokoro_onnx import Kokoro

        try:
            engine = Kokoro(*(str(p) for p in kokoro_model(self.cache)))
        except Exception:  # no model and no download: stay silent rather than break the show
            return
        while True:
            text, voice = self.lines.get()
            name = voice or self.name
            lang = "en-gb" if name[0] == "b" else "en-us"
            for piece in sentences(text):
                try:
                    samples, rate = engine.create(piece, voice=name, speed=self.speed, lang=lang)
                except Exception:
                    continue
                self.audio.put(wav_bytes(samples, rate))

    def _play(self) -> None:
        while True:
            play_wav(self.audio.get())
