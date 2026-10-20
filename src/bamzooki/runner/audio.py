"""Sound for games played live: a beep on each count of the countdown (higher on Go), and match
music looping under the game from Go, fading out as it ends (like the highlights reel).

The engine's own Sound component (Sound.Play / Sound.Stream) plays nothing in this build, so the
sound comes from this computer instead, in step with the game: the driver logs each count
("countdown 3" ... "countdown go"), Go and the end, and MatchAudio, watching the log, plays each cue
as it appears. The beeps are made here (plain sine tones), not taken from the game. Windows only
(winmm's MCI, no extra packages); elsewhere, or with no sound device, the games are silent.
"""

from __future__ import annotations

import math
import struct
import sys
import threading
import wave
from pathlib import Path

RATE = 44100
# The count: three short beeps, then Go an octave higher and longer.
BEEPS = {"3": (660.0, 0.18), "2": (660.0, 0.18), "1": (660.0, 0.18), "go": (1320.0, 0.6)}


def beep_wav(path: Path, hz: float, seconds: float, level: float = 0.3) -> Path:
    """A beeper's tone (a sine with a touch of its 3rd harmonic), with 5 ms edges so it doesn't click."""
    n, edge = int(RATE * seconds), int(RATE * 0.005)
    frames = bytearray()
    for i in range(n):
        env = min(1.0, i / edge, (n - 1 - i) / edge)
        x = math.sin(2 * math.pi * hz * i / RATE) + 0.25 * math.sin(6 * math.pi * hz * i / RATE)
        frames += struct.pack("<h", int(32767 * level * env * x / 1.25))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(bytes(frames))
    return path


def video_sound(mp4: Path, countdown_at: float | None = None, music=None, music_from: float = 0.0,
                music_level: float = 0.6, fade: float = 1.0, ffmpeg: str | None = None) -> Path:
    """Give a game's video the sound heard live: the beeps from `countdown_at` s ("3"; one count a
    second), and `music` (a music.Track) looping from `music_from` s (Go) at music_level x its volume,
    fading out over `fade` s at the end. Rewrites `mp4` in place (the picture is copied)."""
    import subprocess
    import tempfile

    from ..music import loop_wav
    from .video import VideoError, find_ffmpeg, video_duration

    exe = find_ffmpeg(ffmpeg)
    dur = video_duration(mp4, ffmpeg)
    mp4 = Path(mp4)
    fmt = "aformat=sample_rates=48000:channel_layouts=stereo"
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        inputs, chains, mix = ["-i", str(mp4.resolve())], [], []
        for i, (cue, (hz, seconds)) in enumerate(BEEPS.items() if countdown_at is not None else ()):
            at = countdown_at + i
            if 0 <= at < dur:
                inputs += ["-i", str(beep_wav(folder / f"beep_{cue}.wav", hz, seconds))]
                chains.append(f"[{inputs.count('-i') - 1}:a]{fmt},adelay={at * 1000:.0f}:all=1[b{i}]")
                mix.append(f"[b{i}]")
        if music and music_from < dur and (loop := loop_wav(music, folder / "loop.wav", exe)):
            inputs += ["-stream_loop", "-1", "-i", str(loop)]
            length = dur - music_from
            out_at = max(0.0, length - fade)
            chains.append(f"[{inputs.count('-i') - 1}:a]atrim=0:{length:.3f},asetpts=PTS-STARTPTS,"
                          f"volume={music_level * music.volume},afade=t=in:st=0:d=0.03,"
                          f"afade=t=out:st={out_at:.3f}:d={min(fade, length):.3f},{fmt},"
                          f"adelay={music_from * 1000:.0f}:all=1[m]")
            mix.append("[m]")
        if not mix:
            return mp4
        chains.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0:duration=longest,apad,"
                      f"atrim=0:{dur:.3f},{fmt}[a]")
        tmp_out = folder / "sound.mp4"
        res = subprocess.run([exe, "-y", "-loglevel", "error", *inputs, "-filter_complex", ";".join(chains),
                              "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                              "-movflags", "+faststart", str(tmp_out)], capture_output=True, text=True)
        if res.returncode != 0 or not tmp_out.exists():
            raise VideoError(f"couldn't add the sound: {res.stderr.strip()[-300:]}")
        tmp_out.replace(mp4)
    return mp4


def log_events(text: str) -> list[str]:
    """The game's sound cues in a driver log, in order: "3", "2", "1", "go" (the countdown), "start"
    (Go itself), "done". Only whole lines count (the log may be caught mid-write)."""
    out = []
    for line in text.splitlines(keepends=True):
        if not line.endswith("\n"):
            break
        words = line.split()[1:]  # after the timestamp
        if words[:1] == ["countdown"] and len(words) > 1 and words[1] in BEEPS:
            out.append(words[1])
        elif words[:2] == ["go", "at"]:
            out.append("start")
        elif words[:1] == ["done:"]:
            out.append("done")
    return out


class _MCI:
    """Windows' Media Control Interface: each sound is an alias that can play over the others."""

    def __init__(self):
        import ctypes

        self._send = ctypes.windll.winmm.mciSendStringW
        self._buf = ctypes.create_unicode_buffer(256)

    def __call__(self, command: str) -> bool:
        return self._send(command, self._buf, 255, None) == 0

    def open(self, path: Path, alias: str) -> bool:
        return self(f'open "{Path(path).resolve()}" type mpegvideo alias {alias}')


class MatchAudio:
    """Plays a game's sound while the engine runs: start() before launching, stop() after."""

    def __init__(self, log: Path, folder: Path, beeps: bool = True, music=None, music_level: float = 0.6,
                 fade: float = 1.0, poll: float = 0.02):
        """music: a music.Track whose loop plays from Go, at music_level x its volume (under the beeps),
        and fades out over `fade` s when the game ends."""
        self.log, self.folder, self.beeps, self.poll = Path(log), Path(folder), beeps, poll
        self.music, self.music_level, self.fade = music, music_level, fade
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.played: list[str] = []  # the cues handled, in order

    def start(self) -> None:
        if sys.platform == "win32" and (self.beeps or self.music):
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        try:
            mci = _MCI()
        except (AttributeError, OSError):
            return
        loaded = set()
        for cue, (hz, seconds) in BEEPS.items() if self.beeps else ():
            wav = beep_wav(self.folder / f"beep_{cue}.wav", hz, seconds)
            if mci.open(wav, f"beep_{cue}"):
                loaded.add(cue)
        music = None
        if self.music:
            from ..music import loop_wav

            loop = loop_wav(self.music, self.folder / "music_loop.wav")
            if loop and mci.open(loop, "music"):
                music = int(1000 * min(1.0, self.music_level * self.music.volume))
        playing = False
        seen = 0
        try:
            while not self._stop.is_set():
                try:
                    events = log_events(self.log.read_text("latin-1"))
                except OSError:
                    events = []
                for cue in events[seen:]:
                    self.played.append(cue)
                    if cue in loaded:
                        mci(f"play beep_{cue} from 0")
                    elif cue == "start" and music is not None and not playing:
                        playing = mci(f"setaudio music volume to {music}") and mci("play music from 0 repeat")
                    elif cue == "done" and playing:
                        self._fade_out(mci, music)
                        playing = False
                seen = max(seen, len(events))
                self._stop.wait(self.poll)
            if playing:
                self._fade_out(mci, music)
            self._stop.wait(0.6)  # let a last beep finish
        finally:
            for cue in loaded:
                mci(f"close beep_{cue}")
            if music is not None:
                mci("close music")

    def _fade_out(self, mci: _MCI, volume: int, steps: int = 25) -> None:
        for k in range(steps, -1, -1):
            mci(f"setaudio music volume to {volume * k // steps}")
            threading.Event().wait(self.fade / steps)
        mci("stop music")
