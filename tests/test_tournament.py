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
    hooks = []
    rounds, places = knockout(field, strongest, quiet, random.Random(1),
                              before=lambda a, b, r: hooks.append(("before", r)),
                              after=lambda bout: hooks.append(("after", bout.winner.model)))
    assert [len(r) for r in rounds] == [2, 1]
    assert places[0] == (1, field[3])
    assert places[1][0] == 2 and [p for p, _ in places[2:]] == [3, 3]
    assert [h[0] for h in hooks] == ["before", "after"] * 3  # around every match
    assert [h[1] for h in hooks[::2]] == ["Semi-finals", "Semi-finals", "Final"]
    assert hooks[-1] == ("after", field[3].model)


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


def test_rounds_announced_and_zooks_picked_per_match():
    from bamzooki.tournament import rounds_needed

    assert [rounds_needed(n) for n in (2, 3, 4, 5, 8, 9)] == [1, 2, 2, 3, 3, 4]
    field = seats(4)
    started, played = [], []

    def pick(a, b, round_name):
        return f"{a.folder}/{round_name}@1", f"{b.folder}/{round_name}@1"

    def play(green, red, seed):
        played.append((green.zook, red.zook))
        return strongest(green, red, seed)

    rounds, places = knockout(field, play, quiet, random.Random(1), pick=pick,
                              start_round=lambda i, n, name, f: started.append((i, n, name)))
    assert started == [(0, 2, "Semi-finals"), (1, 2, "Final")]
    assert all(z.endswith("/Semi-finals@1") for game in played[:2] for z in game)
    assert all(z.endswith("/Final@1") for z in played[2])  # a different Zook in the next round
    assert places[0] == (1, field[3]) and places[0][1].zook == field[3].zook  # the ranking has the entrants' seats
    assert rounds[-1][0].winner.zook_name == "Final"  # the bout keeps the Zook that played


def test_byes_only_in_the_first_round_for_the_best_seeds():
    for n in (3, 5, 6, 7):
        field = seats(n)
        sizes = []
        rounds, places = knockout(field, strongest, quiet, random.Random(n), seeded=True,
                                  start_round=lambda i, t, name, f: sizes.append(len(f)))
        byes = [b.a for b in rounds[0] if b.how == "bye"]
        assert byes == field[:2 ** len(rounds) - n]  # the top seeds
        assert all(b.how != "bye" for r in rounds[1:] for b in r) and all(s % 2 == 0 for s in sizes[1:])
        assert sizes[0] == n and len(rounds) == {3: 2, 5: 3, 6: 3, 7: 3}[n]
        assert places[0][1] == field[-1]  # the strongest still wins
    field = seats(6)
    rounds, _ = knockout(field, strongest, quiet, random.Random(2))  # not seeded: random byes
    assert sum(b.how == "bye" for b in rounds[0]) == 2


def test_each_rounds_draw_is_told_before_it_starts():
    field = seats(6)
    draws, order = [], []
    rounds, _ = knockout(field, strongest, quiet, random.Random(5), seeded=True,
                         on_draw=lambda i, name, d: (draws.append((i, name, d)), order.append("draw")),
                         start_round=lambda i, t, name, f: order.append("start"))
    assert order == ["draw", "start"] * 3
    assert [(i, name) for i, name, _ in draws] == [(0, "Quarter-finals"), (1, "Semi-finals"), (2, "Final")]
    first = draws[0][2]
    assert [b is None for _, b in first].count(True) == 2 and len(first) == 4  # 2 byes beside 2 matches
    assert [[(b.a.model, b.b and b.b.model) for b in r] for r in rounds] ==         [[(a.model, b and b.model) for a, b in d] for _, _, d in draws]  # the bouts follow the draw


def test_a_side_without_a_zook_forfeits():
    field = seats(4)
    played, before = [], []

    def pick(a, b, round_name):  # field[0] has no Zook for any round; in the final nobody has one
        return tuple(None if s is field[0] or round_name == "Final" else s.zook for s in (a, b))

    def play(green, red, seed):
        played.append({green.model, red.model})
        return strongest(green, red, seed)

    rounds, places = knockout(field, play, quiet, random.Random(1), pick=pick,
                              before=lambda a, b, r: before.append({a.model, b.model}))
    lost = next(b for b in rounds[0] if field[0].model in (b.a.model, b.b.model))
    assert lost.how == "forfeit" and lost.loser.model == field[0].model and not lost.games
    assert field[0].model not in set().union(*played, *before)  # no game, no build-up
    assert rounds[1][0].how == "both forfeit" and not rounds[1][0].games  # a coin toss
    assert len(played) == 1 and {s.model: p for p, s in places}[field[0].model] == 3
