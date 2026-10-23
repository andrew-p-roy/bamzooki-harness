"""The show: model fact sheets, Zook cards, the tale of the tape and the announcer (no API, no engine)."""

import asyncio
import json
from pathlib import Path

from bamzooki import showtime
from bamzooki.announcer import Announcer, clean

HAIKU = {
    "id": "anthropic/claude-haiku-5.5", "canonical_slug": "anthropic/claude-haiku-5.5-20261007",
    "name": "Anthropic: Claude Haiku 5.5", "created": 1791397883, "context_length": 1000000,
    "description": "Claude Haiku 5.5 is Anthropic's small, fast model.",
    "architecture": {"input_modalities": ["text", "image", "file"]},
    "pricing": {"prompt": "0.0000001", "completion": "0.0000005"},
    "knowledge_cutoff": "2026-03-01",
    "benchmarks": {"artificial_analysis": {"intelligence_index": 41.2, "note": "x"}},
}


def test_fact_sheet_from_openrouter_info():
    f = showtime.model_facts("anthropic/claude-haiku-5.5", HAIKU, today=(2026, 10, 20))
    assert f["name"] == "Claude Haiku 5.5" and f["maker"] == "Anthropic"
    assert f["home"] == "San Francisco, USA" and f["country"] == "USA"
    assert f["released"] == "October 2026" and f["released_on"] == "2026-10-07" and f["age"] == "13 days old"
    assert f["can_see_images"] and f["price_per_million_tokens"] == "$0.1 in, $0.5 out"
    assert f["context_window_tokens"] == 1_000_000 and f["knowledge_cutoff"] == "2026-03-01"
    assert f["artificial_analysis_indices"] == {"intelligence_index": 41.2}
    line = showtime.card_line(f)
    assert line.startswith("Claude Haiku 5.5 | Anthropic | San Francisco, USA | released October 2026")


def test_unknown_lab_and_listing_date_fallback():
    f = showtime.model_facts("~somelab/new-model", {"name": "New Model", "created": 1767225600,
                                                    "pricing": {"prompt": "0", "completion": "0"}},
                             today=(2027, 6, 1))
    assert "maker" not in f and "country" not in f  # no guessing
    assert f["released"] == "January 2026" and f["age"] == "about 17 months old"
    assert f["price_per_million_tokens"] == "free"
    assert showtime.model_facts("mistralai/x", {})["country"] == "France"
    assert showtime.model_facts("google/x", {})["country"] == "UK and USA"


def test_zook_card_and_tale_of_the_tape(tmp_path):
    folder = tmp_path / "lab_alpha" / "Crusher"
    folder.mkdir(parents=True)
    png = folder / "3.0_three_quarter.png"
    png.write_bytes(b"png")
    spec = {"name": "Crusher", "body": {
        "gait": {"cycle_speed": 0.6}, "look": {"texture": "metal", "rgb": [0.5, 0.5, 0.55]},
        "children": [
            {"name": "leg", "movement": {"type": "single"}, "mirror": True, "look": {"rgb": [0.9, 0.1, 0.1]}},
            {"name": "hip", "children": [{"name": "shin", "movement": {"type": "double"}}]},
            {"name": "horn"}]}}
    (folder / "3.0.json").write_text(json.dumps({
        "spec": spec, "stats": {"Height": "12.0 cm", "Length": "14.8 cm", "Width": "18.5 cm",
                                "Weight": "0.667 kg", "Components": "13"},
        "previews": {"three_quarter": str(png), "front": str(folder / "missing.png")}}))
    card = showtime.zook_card(tmp_path, "lab_alpha/Crusher@3")
    assert card.name == "Crusher" and card.limbs == 3 and card.look == "metal, grey" and card.cycle_speed == 0.6
    assert card.picture() == [str(png)]  # missing previews are skipped
    assert card.summary() == "Crusher: 0.667 kg, 13 parts, 3 limbs, 12.0 x 18.5 x 14.8 cm (h x w x l), metal, grey"
    other = showtime.zook_card(tmp_path, "lab_beta/Ghost@1")  # no files: just the name
    assert other.name == "Ghost" and other.stats == {} and other.picture() == []
    assert showtime.tell_apart(card, other) == "Crusher: grey metal body, 3 red limbs."
    tape = showtime.tale_of_the_tape(card, other, "lab/alpha", "lab/beta")
    assert "Crusher" in tape[1] and "Ghost" in tape[1] and any("0.667 kg" in t and "?" in t for t in tape)


def test_end_of_game_times():
    assert showtime.end_of_game_times(5.0, 12.0) == [1.0, 2.0, 5.0, 7.5]  # finish at 6 s of video
    assert showtime.end_of_game_times(None, 121.0) == [1.0, 117.0, 120.0, 121.0]  # time limit: the end
    assert showtime.end_of_game_times(0.5, 4.0)[1:3] == [1.0, 1.0]  # never before Go
    assert showtime.end_of_game_times(5.0, 14.5, lead=3.5) == [3.5, 4.5, 7.5, 10.0]  # with the countdown


def test_image_part(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(b"\x89PNG")
    part = showtime.image_part(p)
    assert part["type"] == "image_url" and part["image_url"]["url"].startswith("data:image/png;base64,")
    assert showtime.image_part(tmp_path / "none.png") is None


class Host:
    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    async def chat(self, model, messages, tools, **kw):
        self.seen.append(messages)
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return {"choices": [{"message": {"content": r}}], "usage": {"total_tokens": 10, "cost": 0.001}}


def test_announcer_introduces_with_facts_and_remembers():
    host = Host(['"**Ladies and gentlemen**, from San Francisco..."', "And now the second!"])
    a = Announcer(host, "lab/host", "documentary", memory=4)
    f = showtime.model_facts("anthropic/claude-haiku-5.5", HAIKU)
    text = asyncio.run(a.introduce(1, "Beat Spider at Sumo", f))
    assert text == "Ladies and gentlemen, from San Francisco..."  # markdown and wrapping quotes gone
    sent = host.seen[0]
    assert "wildlife documentary" in sent[0]["content"] and "October 2026" in sent[-1]["content"]
    asyncio.run(a.introduce(2, "Beat Spider at Sumo", f))
    assert len(host.seen[1]) == 4  # system + the first exchange + this request
    assert a.tokens == 20 and abs(a.cost - 0.002) < 1e-9


def test_announcer_pictures_only_for_vision_and_goes_quiet_after_failures():
    pic = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}}
    host = Host(["Big hit!"])
    a = Announcer(host, "lab/host")
    asyncio.run(a.game_call("Zook Sumo", "Final", "a (A)", "b (B)", "A wins.", "finished", pic))
    assert isinstance(host.seen[0][-1]["content"], list) and "picture" in a.history[0]["content"]
    assert not isinstance(a.history[0]["content"], list)  # pictures aren't kept in its memory
    blind = Announcer(Host(["ok"]), "lab/host", vision=False)
    asyncio.run(blind.game_call("Zook Sumo", "Final", "a", "b", "A wins.", "x", pic))
    assert isinstance(blind.router.seen[0][-1]["content"], str)
    flaky = Announcer(Host([RuntimeError("hiccup"), "Back on air!"]), "lab/host")
    assert asyncio.run(flaky.closing("story")) == "Back on air!" and not flaky.last_error  # one retry
    broken = Announcer(Host([RuntimeError("down")] * 6 + ["never asked"]), "lab/host")
    for _ in range(4):
        assert asyncio.run(broken.closing("story")) == ""
    assert broken.quiet and len(broken.router.seen) == 6 and broken.last_error == "down"


def test_clean():
    assert clean("# Title\n- **Big** news\n<think>hm</think>") == "Title\nBig news"


def test_speakable_and_speech_command():
    from bamzooki import voice

    assert voice.speakable("**Champion!** 🏆 [applause] it's over") == "Champion! it's over"
    cmd = voice.speech_command("Microsoft Hazel Desktop", rate=3, wav="C:/x/it's.wav")
    if cmd:  # a speech engine on this machine
        assert cmd[-1].count("Hazel") <= 1 and ("it''s.wav" in cmd[-1] or cmd[0].endswith("say"))


def test_voice_without_an_engine_is_silent(monkeypatch):
    from bamzooki import voice

    monkeypatch.setattr(voice, "speech_command", lambda *a, **k: None)
    monkeypatch.setattr(voice, "kokoro_installed", lambda: False)
    v = voice.Voice(cache=Path("unused"))
    v.say("hello")
    assert not v.available and v.lines.empty()


def test_sentences_join_short_ones():
    from bamzooki.voice import sentences

    assert sentences("And they're off! Spider charges straight for the middle of the table. What a shove! "
                     "It's over!") == ["And they're off! Spider charges straight for the middle of the table.",
                                       "What a shove! It's over!"]
    assert sentences("") == []


def test_each_contestant_has_its_own_voice():
    from bamzooki.voice import model_voice

    assert model_voice("google/gemini-3.5-flash") == "af_heart"
    assert model_voice("qwen/qwen3.8-max-prime") == "bf_emma"
    assert model_voice("~openai/gpt-luna-latest") == "am_puck"
    assert model_voice("anthropic/claude-opus-5.5", {"anthropic": "bm_lewis"}) == "bm_lewis"
    other = model_voice("mistralai/mistral-large")
    assert other == model_voice("mistralai/mistral-small") and other != "bm_george"


def test_voice_picks_kokoro_for_kokoro_voices_only(monkeypatch):
    from bamzooki import voice

    monkeypatch.setattr(voice, "kokoro_installed", lambda: True)
    monkeypatch.setattr(voice.threading, "Thread", lambda *a, **k: type("T", (), {"start": lambda self: None})())
    assert voice.Voice(cache=Path("c")).name == "bm_george"
    assert voice.Voice("bf_emma", cache=Path("c")).kokoro
    assert not voice.Voice("Microsoft Hazel Desktop", cache=Path("c")).kokoro
    assert not voice.Voice(cache=None).kokoro  # no model folder: the system's engine


def test_hall_of_fame_and_record(tmp_path):
    assert showtime.hall_of_fame(tmp_path) == [] and showtime.arena_record([], "lab/a") is None
    showtime.record_champion(tmp_path, {"champion": "lab/a", "zook_name": "Crusher", "runner_up": "lab/b"})
    showtime.record_champion(tmp_path, {"champion": "lab/a", "zook_name": "Crusher2", "runner_up": "lab/c"})
    (tmp_path / showtime.HALL_OF_FAME).open("a").write("not json\n")
    hall = showtime.hall_of_fame(tmp_path)
    assert len(hall) == 2 and hall[0]["date"]
    rec = showtime.arena_record(hall, "lab/a")
    assert rec.startswith("won the arena knockout twice before") and "with Crusher2" in rec
    assert showtime.arena_record(hall, "lab/b") == "runner-up once"


def test_hall_command_prints(tmp_path, capsys):
    from bamzooki.arena import Arena

    a = Arena.__new__(Arena)
    a.arena_dir = tmp_path
    a.hall()
    assert "no champions yet" in capsys.readouterr().out
    showtime.record_champion(tmp_path, {"champion": "lab/a", "zook_name": "Crusher", "contest": "Zook Sumo",
                                        "field": 3, "runner_up": "lab/b"})
    a.hall()
    out = capsys.readouterr().out
    assert "Crusher" in out and "Most titles: lab/a (1)" in out


def test_highlights_without_videos(tmp_path):
    assert showtime.highlights([tmp_path / "missing.mp4", None], tmp_path / "out.mp4") is None


def _clip(path, colour, seconds=1):
    import subprocess

    from bamzooki.runner.video import find_ffmpeg

    subprocess.run([find_ffmpeg(), "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"color=c={colour}:s=320x240:r=25:d={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(path)], check=True)
    return path


def test_titled_reel(tmp_path):
    import pytest

    from bamzooki.runner.video import VideoError, find_ffmpeg, video_duration

    try:
        find_ffmpeg()
    except VideoError:
        pytest.skip("no ffmpeg")
    if not showtime.find_font():
        pytest.skip("no font for titles")
    games = [showtime.ReelGame(_clip(tmp_path / "a.mp4", "blue"), "Zook Sumo knockout - Final - game 1",
                               ("lab/alpha-100%", "Crusher"), ("x/beta's", "Stomper")),
             showtime.ReelGame(_clip(tmp_path / "b.mp4", "red"), "Zook Sumo knockout - Final - game 2",
                               ("x/beta's", "Stomper"), ("lab/alpha-100%", "Crusher"))]
    out = showtime.highlights(games, tmp_path / "reel.mp4", champion=("lab/alpha-100%", "Crusher"))
    assert out == (tmp_path / "reel.mp4").resolve()
    # two cards, two games, the champion card; half a second of black first, and a second before the
    # second card and the champion's
    assert abs(video_duration(out) - (2 * 3 + 2 * 1 + 4 + 0.5 + 2 * 1)) < 0.3


def test_reel_without_a_font_is_just_joined(tmp_path):
    import pytest

    from bamzooki.runner.video import VideoError, find_ffmpeg, video_duration

    try:
        find_ffmpeg()
    except VideoError:
        pytest.skip("no ffmpeg")
    g = showtime.ReelGame(_clip(tmp_path / "a.mp4", "blue", 2), "t", ("a/b", "A"), ("c/d", "C"))
    assert showtime.titled_reel([g], tmp_path / "x.mp4", font=tmp_path / "none.ttf") is None
    out = showtime.join_videos([g.video, g.video], tmp_path / "plain.mp4")
    assert abs(video_duration(out) - 4) < 0.3
