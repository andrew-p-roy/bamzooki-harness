"""The arena's /contest knockout, without the game engine."""

import random

from bamzooki.arena import _clip
from bamzooki.tournament import Seat, knockout, round_name


def seats(n):
    return [Seat(f"lab/model-{i}", f"lab_model-{i}/Zook{i}@1") for i in range(n)]


def quiet(*_):
    pass


def strongest(green, red, seed):
    """The higher-numbered model always wins."""
    g, r = int(green.model[-1]), int(red.model[-1])
    return "finished", ("green" if g > r else "red"), "stats"


def test_seat_names():
    s = Seat("openai/gpt-5", "openai_gpt-5/Crusher@3")
    assert s.zook_name == "Crusher" and s.billing() == "openai/gpt-5 (Crusher)"


def test_winners_play_winners_and_ranking():
    field = seats(4)
    pauses = []
    rounds, places = knockout(field, strongest, quiet, random.Random(1), pause=lambda: pauses.append(1))
    assert [len(r) for r in rounds] == [2, 1]
    assert places[0] == (1, field[3])
    assert places[1][0] == 2 and [p for p, _ in places[2:]] == [3, 3]
    assert len(pauses) == 2  # between the three matches, not before the first


def test_odd_field_gets_a_bye():
    field = seats(3)
    rounds, places = knockout(field, strongest, quiet, random.Random(2))
    assert any(b.how == "bye" for b in rounds[0])
    assert places[0][1] is field[2] and len(places) == 3


def test_draws_are_replayed_with_sides_swapped_then_coin_toss():
    games = []

    def draw(green, red, seed):
        games.append((green, red, seed))
        return "timeout", None, ""

    field = seats(2)
    rounds, places = knockout(field, draw, quiet, random.Random(3), games_per_bout=3)
    b = rounds[0][0]
    assert len(b.games) == 3 and b.how == "coin toss" and b.winner in field
    assert games[0][0] is games[1][1] and games[1][0] is games[2][1]  # sides alternate
    assert [g[2] for g in games] == [games[0][2] + i for i in range(3)]  # new seed each game


def test_failed_game_is_retried_once():
    calls = []

    def flaky(green, red, seed):
        calls.append(seed)
        return ("stalled", None, "") if len(calls) == 1 else ("finished", "green", "")

    rounds, _ = knockout(seats(2), flaky, quiet, random.Random(4))
    assert len(calls) == 2 and len(rounds[0][0].games) == 1 and rounds[0][0].how == "won"


def test_round_names():
    assert [round_name(n, 1) for n in (2, 4, 8, 16)] == ["Final", "Semi-finals", "Quarter-finals", "Round 1"]


def test_clip():
    assert _clip("a b c", 5) == "a b c"
    assert _clip(" ".join(["w"] * 120)) == " ".join(["w"] * 100) + " ... [+20 words]"
