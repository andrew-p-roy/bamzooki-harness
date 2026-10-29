"""OBS control for the arena (--obs): switch scenes as the show moves on, and record the session.

    [arena.obs]                 in bamzooki.toml
    host = "localhost"
    port = 4455
    record = true               start recording with the session, stop when it ends
    scenes = { design = "Design", showcase = "Showcase", game = "Game", talk = "Knockout", crown = "Crown" }

The phases are the overlay's (bamzooki.overlay); without `scenes` the names above are used, and a
phase set to "" leaves OBS where it is. A scene OBS doesn't have is mentioned once and skipped.
The websocket password comes from OBS_WEBSOCKET_PASSWORD, else from OBS's own settings
(%APPDATA%/obs-studio/plugin_config/obs-websocket/config.json); it's never printed or logged.
Needs obsws-python (pip install -e .[obs]). No OBS, no package or a lost connection prints one
line and turns OBS control off: the show goes on without it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

SCENES = {"design": "Design", "showcase": "Showcase", "game": "Game", "talk": "Knockout", "crown": "Crown"}


def obs_password() -> str | None:
    if pw := os.environ.get("OBS_WEBSOCKET_PASSWORD"):
        return pw
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    try:
        raw = json.loads((Path(appdata) / "obs-studio" / "plugin_config" / "obs-websocket" / "config.json")
                         .read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return (raw.get("server_password") or None) if raw.get("auth_required", True) else None


def _client(host: str, port: int, password: str | None):
    import obsws_python

    return obsws_python.ReqClient(host=host, port=port, password=password or "", timeout=3)


class Obs:
    def __init__(self, cfg: dict | None = None, say: Callable[[str], None] = print, client=None):
        cfg = cfg or {}
        self.scenes = {str(k): str(v) for k, v in {**SCENES, **(cfg.get("scenes") or {})}.items() if v}
        self.missing: set[str] = set()  # scenes OBS said it doesn't have
        self.recording_wanted = bool(cfg.get("record", False))
        self.say = say
        self.now: str | None = None  # the scene last switched to
        self.recording = False
        try:
            self.client = client or _client(str(cfg.get("host", "localhost")), int(cfg.get("port", 4455)),
                                            obs_password())
        except ImportError:
            self._off("obsws-python isn't installed (pip install -e .[obs])")
        except Exception as err:
            self._off(f"can't reach OBS ({type(err).__name__}); is it running with its websocket on?")

    @property
    def on(self) -> bool:
        return self.client is not None

    def _off(self, why: str) -> None:
        self.client = None
        self.say(f"(--obs: {why}; OBS control is off)")

    def _call(self, what: str, *args) -> bool:
        if not self.client:
            return False
        try:
            getattr(self.client, what)(*args)
            return True
        except Exception as err:
            if type(err).__name__ == "OBSSDKRequestError":  # OBS is there but said no (e.g. no such scene)
                if what == "set_current_program_scene":
                    self.missing.add(args[0])
                    self.say(f"(--obs: OBS has no scene named {args[0]!r}; skipping it)")
                else:
                    self.say(f"(--obs: OBS refused {what})")
                return False
            self._off(f"lost OBS ({type(err).__name__})")  # never the error's text: keep secrets off screen
            return False

    def scene(self, phase: str) -> None:
        """Switch to the phase's scene, if it has one and OBS isn't showing it already."""
        name = self.scenes.get(phase)
        if name and name != self.now and name not in self.missing and self._call("set_current_program_scene", name):
            self.now = name

    def record(self, on: bool) -> None:
        if not self.recording_wanted or on == self.recording:
            return
        if self._call("start_record" if on else "stop_record"):
            self.recording = on

    def close(self) -> None:
        self.record(False)
        if self.client and hasattr(self.client, "disconnect"):
            try:
                self.client.disconnect()
            except Exception:
                pass
        self.client = None
