"""Match music: loop analysis on a synthetic beat, choosing a track per challenge, the reel's audio."""

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from bamzooki import music, showtime


def ffmpeg():
    from bamzooki.runner.video import VideoError, find_ffmpeg

    try:
        return find_ffmpeg()
    except VideoError:
        pytest.skip("no ffmpeg")


def clicks(path: Path, bpm: float, seconds: float, fade_from: float | None = None) -> Path:
    """A click on every beat (a short 880 Hz blip), optionally fading out at the end."""
    beat = 60 / bpm
    af = f"aevalsrc=exprs='if(lt(mod(t\\,{beat})\\,0.03)\\,0.8*sin(2*PI*880*t)\\,0)':s=44100:d={seconds}"
    fade = ["-af", f"afade=t=out:st={fade_from}:d={seconds - fade_from}"] if fade_from else []
    subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i", af, *fade, "-b:a", "192k", str(path)],
                   check=True)
    return path


def test_analyse_finds_tempo_fade_and_a_loop_of_whole_phrases(tmp_path):
    info = music.analyse(clicks(tmp_path / "beat.mp3", 120, 20, fade_from=17))
    assert abs(info["bpm"] - 120) < 1.5
    assert info["first_beat"] < 0.06  # the beat starts at once
    assert 16.5 <= info["fade_start"] <= 18
    assert info["bars"] == 8 and abs(info["loop_end"] - (info["loop_start"] + 16)) < 0.3  # 8 bars of 2 s
    steady = music.analyse(clicks(tmp_path / "steady.mp3", 100, 15))
    assert abs(steady["bpm"] - 100) < 1.5 and steady["fade_start"] is None
    assert "loop_end = " in music.toml_entry("beat", "beat.mp3", info, ["sumo"], default=True)


def test_tracks_and_pick(tmp_path):
    for name in ("a", "b", "c"):
        (tmp_path / f"{name}.mp3").write_bytes(b"x")
    (tmp_path / "tracks.toml").write_text(
        '[tracks.a]\nfile = "a.mp3"\nloop_end = 20.5\n'
        '[tracks.b]\nfile = "b.mp3"\ntags = ["Zook Sumo"]\n'
        '[tracks.c]\nfile = "c.mp3"\ndefault = true\nvolume = 0.5\n'
        '[tracks.gone]\nfile = "missing.mp3"\n', encoding="utf-8")
    tracks = music.load_tracks(tmp_path / "tracks.toml")
    assert list(tracks) == ["a", "b", "c"] and tracks["a"].loop_end == 20.5 and tracks["c"].volume == 0.5

    def rules(music_name=""):
        return SimpleNamespace(music=music_name)
    assert music.pick_track(tracks, rules("a"), "Zook Sumo").name == "a"  # named by the rules
    assert music.pick_track(tracks, rules(), "Zook Sumo").name == "b"  # tagged with the game's contest
    assert music.pick_track(tracks, rules(), "Zook Tag").name == "c"  # the default
    assert music.pick_track(tracks, rules("none"), "Zook Sumo") is None  # silence asked for
    assert music.pick_track({}, rules(), "Zook Sumo") is None
    assert music.load_tracks(tmp_path / "nothing.toml") == {}


def test_challenge_music_field():
    from bamzooki.challenge import Challenge

    assert Challenge.load().music == "synth_funk"


def test_repo_tracks_are_listed_and_synth_funk_is_the_default():
    tracks = music.load_tracks()
    if not tracks:
        pytest.skip("no Music/ folder")
    assert music.pick_track(tracks, None).name == "synth_funk"
    t = tracks["synth_funk"]
    assert t.loop_end and t.loop_end - t.loop_start > 20


def test_reel_music_under_games_and_silent_cards(tmp_path):
    from bamzooki.runner.video import video_duration

    exe = ffmpeg()
    if not showtime.find_font():
        pytest.skip("no font for titles")
    video = tmp_path / "game.mp4"
    subprocess.run([exe, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x240:r=25:d=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True)
    track = music.Track("beat", clicks(tmp_path / "beat.mp3", 120, 6), 0.0, 4.0)
    games = [showtime.ReelGame(video, "Final - game 1", ("a/x", "X"), ("b/y", "Y"))] * 2
    out = showtime.highlights(games, tmp_path / "reel.mp4", champion=("a/x", "X"), music=track)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                            str(out)], capture_output=True, text=True).stdout.split()
    assert probe == ["video", "audio"] and abs(video_duration(out) - (0.5 + 2 * 3 + 2 * 2 + 4 + 2 * 1)) < 0.3  # + black

    def level(start, seconds):
        r = subprocess.run([exe, "-hide_banner", "-ss", str(start), "-t", str(seconds), "-i", str(out), "-vn", "-af",
                            "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
        return float(r.split("max_volume: ")[1].split(" dB")[0])
    assert level(0.2, 2.8) < -80  # the black and the title card are silent
    assert level(3.6, 0.8) > -20  # the beat plays under the game


def _level(exe, video, start, seconds):
    r = subprocess.run([exe, "-hide_banner", "-ss", str(start), "-t", str(seconds), "-i", str(video), "-vn", "-af",
                        "volumedetect", "-f", "null", "-"], capture_output=True, text=True).stderr
    return float(r.split("max_volume: ")[1].split(" dB")[0])


def _blue(exe, path, seconds):
    subprocess.run([exe, "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=blue:s=320x240:r=25:d={seconds}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def test_game_video_gets_the_beeps_and_the_music_from_go(tmp_path):
    from bamzooki.runner.audio import video_sound
    from bamzooki.runner.video import has_audio, video_duration

    exe = ffmpeg()
    video = _blue(exe, tmp_path / "game.mp4", 8)
    track = music.Track("hum", tmp_path / "hum.wav", 0.0, 1.0)  # a 1 s loop of a steady tone
    subprocess.run([exe, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=220:d=1", str(track.file)], check=True)
    assert not has_audio(video)
    video_sound(video, countdown_at=0.5, music=track, music_from=3.5)  # as with go at 3.5 s of video
    assert has_audio(video) and abs(video_duration(video) - 8) < 0.1
    assert _level(exe, video, 0.0, 0.4) < -80  # quiet before "3"
    assert _level(exe, video, 0.5, 0.2) > -30 and _level(exe, video, 1.5, 0.2) > -30  # the beeps
    assert _level(exe, video, 0.8, 0.6) < -80  # between beeps, before Go: no music yet
    assert _level(exe, video, 5.0, 1.5) > -30  # the music loops on from Go


def test_reel_keeps_a_games_own_sound(tmp_path):
    from bamzooki.runner.audio import video_sound

    exe = ffmpeg()
    if not showtime.find_font():
        pytest.skip("no font for titles")
    video = video_sound(_blue(exe, tmp_path / "game.mp4", 3), countdown_at=0.0)  # beeps at 0, 1, 2 s; no music
    track = music.Track("beat", clicks(tmp_path / "beat.mp3", 120, 6), 0.0, 4.0)
    out = showtime.highlights([showtime.ReelGame(video, "Final", ("a/x", "X"), ("b/y", "Y"))], tmp_path / "reel.mp4",
                              music=track)
    assert _level(exe, out, 3.5, 0.15) > -30  # the game's own first beep, after the black and the 3 s card
    assert _level(exe, out, 3.8, 0.6) < -80  # and no reel music laid over it
