"""The announcer's voice (--speak): what it says is read out loud by the system's own speech
engine, one line after another on a background thread, so the show never waits for it.

Windows uses System.Speech (built in) through PowerShell, macOS `say`, Linux `espeak`. No extra
packages. Choose a Windows voice with [arena] voice = "Microsoft Zira Desktop" (see `voices()`).
"""

from __future__ import annotations

import queue
import re
import shutil
import subprocess
import sys
import threading

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


class Voice:
    def __init__(self, voice: str | None = None, rate: int = 1):
        self.command = speech_command(voice, rate)
        self.lines: queue.Queue[str] = queue.Queue()
        if self.command:
            threading.Thread(target=self._run, daemon=True).start()

    @property
    def available(self) -> bool:
        return self.command is not None

    def say(self, text: str) -> None:
        if self.command and (text := speakable(text)):
            self.lines.put(text)

    def _run(self) -> None:
        while True:
            text = self.lines.get()
            try:
                subprocess.run(self.command, input=text.encode("utf-8"), capture_output=True, timeout=180,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except (OSError, subprocess.SubprocessError):
                pass
