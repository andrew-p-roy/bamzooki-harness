"""Knockout tournament between the qualified Zooks of an arena session (the arena's /contest).

Winners play winners until one is left. A bout is one game of the challenge's contest; a draw
(no winner by the time limit) is replayed with sides swapped and a new seed, up to
`games_per_bout` games, then settled by a coin toss. A game the engine failed to finish (stalled
or crashed) is played again once, then counts as a draw. When the field isn't a power of two, the
first round has just enough byes to leave one after it (6 entrants: 2 byes and 2 matches, then 4),
so every later round is even; the byes go to the best seeds (`seeded`: the seats ranked best first)
or to random entrants. The ranking follows the round each entrant went out in.

The engine is reached through `play(green, red, seed) -> (status, winner colour or None, stats
line)`, so the bracket logic runs without the game. `start_round(index, total, name, field)` is
called as each round begins, with the entrants still in (the arena announces its contest, and
under short rules has them design their Zooks for it), `pick(a, b, round)` gives each match's Zooks
(a model may play a different Zook each round; None = it has no Zook for the round and forfeits:
the other goes through without a game, or a coin toss settles it if neither has one),
`before(a, b, round)` is called ahead of each match that is played and `after(bout)` once it's
decided: the arena uses them for the models' comments, the audience's replies and the pause to read
each result.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field, replace
from typing import Callable

PLAYED = ("finished", "timeout")  # real results; "timeout" = the game's time limit (a draw)


@dataclass
class Seat:
    model: str  # full model name
    zook: str  # submitted design, e.g. "openai_gpt-5/Crusher@3"

    @property
    def zook_name(self) -> str:
        return self.zook.split("/", 1)[-1].split("@", 1)[0]

    @property
    def folder(self) -> str:
        """The entrant's folder in Zook Entries (the part of `zook` before the '/')."""
        return self.zook.split("/", 1)[0]

    def billing(self) -> str:
        return f"{self.model} ({self.zook_name})"


@dataclass
class Game:
    green: Seat
    red: Seat
    seed: int
    status: str
    winner: Seat | None  # None = draw or no result
    stats: str = ""


@dataclass
class Bout:
    a: Seat
    b: Seat | None  # None = a bye
    games: list[Game] = field(default_factory=list)
    winner: Seat | None = None
    how: str = ""  # "won", "bye", "coin toss", "forfeit", "both forfeit" (the last two: no games)

    @property
    def loser(self) -> Seat | None:
        return None if self.b is None else (self.b if self.winner is self.a else self.a)


Play = Callable[[Seat, Seat, int], tuple[str, "str | None", str]]
Say = Callable[[str, str], None]


def round_name(players: int, number: int) -> str:
    return {2: "Final", 3: "Semi-finals", 4: "Semi-finals"}.get(players) or (
        "Quarter-finals" if players <= 8 else f"Round {number}")


def rounds_needed(players: int) -> int:
    """Rounds in a knockout of `players` (with byes for odd numbers)."""
    n = 0
    while players > 1:
        players, n = (players + 1) // 2, n + 1
    return n


def bout(a: Seat, b: Seat, play: Play, rng: random.Random, say: Say, games_per_bout: int = 3) -> Bout:
    out = Bout(a, b)
    seed = rng.randint(1, 9999)
    for n in range(games_per_bout):
        green, red = (a, b) if n % 2 == 0 else (b, a)
        say(f"    game {n + 1}: green {green.zook_name} v red {red.zook_name}, seed {seed + n} ...", "dim")
        status, colour, stats = play(green, red, seed + n)
        if status not in PLAYED:
            say(f"    game didn't finish ({status}); playing it again", "y")
            status, colour, stats = play(green, red, seed + n)
        winner = {"green": green, "red": red}.get(colour) if status in PLAYED else None
        out.games.append(Game(green, red, seed + n, status, winner, stats))
        if stats:
            say(f"      {stats}", "dim")
        if winner:
            out.winner, out.how = winner, "won"
            say(f"    game {n + 1}: {winner.zook_name} wins", "g")
            return out
        why = "draw (time limit)" if status == "timeout" else (
            "draw" if status in PLAYED else f"no result ({status}), counted as a draw")
        say(f"    game {n + 1}: {why}", "y")
    out.winner, out.how = rng.choice((a, b)), "coin toss"
    say(f"    still level after {games_per_bout} games: coin toss -> {out.winner.zook_name}", "y")
    return out


def forfeit(a: Seat, b: Seat, a_out: bool, b_out: bool, rng: random.Random, say: Say) -> Bout:
    """A match a side can't play (no Zook for the round): the other goes through without a game;
    if neither can play, a coin toss."""
    if a_out and b_out:
        out = Bout(a, b, winner=rng.choice((a, b)), how="both forfeit")
        say(f"\n  {a.model} v {b.model}: neither can play this round; coin toss -> {out.winner.model}", "y")
    else:
        out = Bout(a, b, winner=b if a_out else a, how="forfeit")
        say(f"\n  {out.loser.model} can't play this round and forfeits to {out.winner.model}", "y")
    return out


def knockout(seats: list[Seat], play: Play, say: Say, rng: random.Random | None = None,
             games_per_bout: int = 3,
             before: Callable[[Seat, Seat, str], None] = lambda a, b, r: None,
             after: Callable[[Bout], None] = lambda b: None,
             round_title: str | None = None,
             start_round: Callable[[int, int, str, list[Seat]], None] = lambda i, n, r, f: None,
             pick: Callable[[Seat, Seat, str], tuple["str | None", "str | None"]] | None = None,
             seeded: bool = False,
             on_draw: Callable[[int, str, list[tuple[Seat, "Seat | None"]]], None] = lambda i, r, d: None) -> tuple[list[list[Bout]], list[tuple[int, Seat]]]:
    """Play the whole bracket. Returns the rounds and the ranking [(place, seat)], shared places
    for entrants knocked out in the same round. round_title names every round (e.g. "Exhibition
    match" for a single match) instead of "Final", "Semi-finals", ... A bout's seats carry the
    Zooks `pick` chose for it; the ranking has the entrants' own seats. seeded: the seats are
    ranked best first, and the first round's byes go to the top ones (else to random ones).
    on_draw(index, name, draw) is told each round's pairs (None: a bye) before it starts."""
    rng = rng or random.Random()
    rounds: list[list[Bout]] = []
    total = rounds_needed(len(seats))
    own = {s.model: s for s in seats}
    order = list(seats)
    if not seeded:
        rng.shuffle(order)
    n_byes = 2 ** total - len(seats) if len(seats) > 1 else 0
    byes, rest = order[:n_byes], order[n_byes:]
    rng.shuffle(rest)
    pairs = [(rest[i], rest[i + 1]) for i in range(0, len(rest) - 1, 2)]
    draw: list[tuple[Seat, Seat | None]] = []  # round 1: each bye beside a match, to meet its winner next
    for i in range(max(len(byes), len(pairs))):
        draw += ([(byes[i], None)] if i < len(byes) else []) + ([pairs[i]] if i < len(pairs) else [])
    while draw:
        field = [s for pair in draw for s in pair if s]
        name = round_title or round_name(len(field), len(rounds) + 1)
        say(f"\n  === {name} ===", "b")
        on_draw(len(rounds), name, list(draw))
        start_round(len(rounds), total, name, field)
        bouts = []
        for a, b in draw:
            if b is None:
                bouts.append(Bout(a, None, winner=a, how="bye"))
                say(f"\n  {a.billing()} has a bye", "dim")
                continue
            if pick:
                za, zb = pick(a, b, name)
                a, b = replace(a, zook=za or a.zook), replace(b, zook=zb or b.zook)
            if pick and (za is None or zb is None):
                bouts.append(forfeit(a, b, za is None, zb is None, rng, say))
            else:
                say(f"\n  {a.billing()}  v  {b.billing()}", "b")
                before(a, b, name)
                bouts.append(bout(a, b, play, rng, say, games_per_bout))
            say(f"  -> {bouts[-1].winner.billing()} goes through", "m")
            after(bouts[-1])
        rounds.append(bouts)
        alive = [own[b.winner.model] for b in bouts]
        draw = [(alive[i], alive[i + 1]) for i in range(0, len(alive) - 1, 2)]
    return rounds, ranking(seats, rounds)


def ranking(seats: list[Seat], rounds: list[list[Bout]]) -> list[tuple[int, Seat]]:
    out_in: dict[str, int] = {}  # model -> round index it lost in
    for r, bouts in enumerate(rounds):
        for b in bouts:
            if b.loser:
                out_in[b.loser.model] = r
    champion = [s for s in seats if s.model not in out_in]
    places = [(1, s) for s in champion]
    for r in range(len(rounds) - 1, -1, -1):
        group = [s for s in seats if out_in.get(s.model) == r]
        place = len(places) + 1
        places += [(place, s) for s in group]
    return places
