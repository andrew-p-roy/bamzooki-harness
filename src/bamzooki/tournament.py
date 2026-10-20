"""Knockout tournament between the qualified Zooks of an arena session (the arena's /contest).

Winners play winners until one is left. A bout is one game of the challenge's contest; a draw
(no winner by the time limit) is replayed with sides swapped and a new seed, up to
`games_per_bout` games, then settled by a coin toss. A game the engine failed to finish (stalled
or crashed) is played again once, then counts as a draw. With an odd number left, one entrant
gets a bye. The ranking follows the round each entrant went out in.

The engine is reached through `play(green, red, seed) -> (status, winner colour or None, stats
line)`, so the bracket logic runs without the game. `before(a, b, round)` is called ahead of each
match and `after(bout)` once it's decided: the arena uses them for the models' comments, the
audience's replies and the pause to read each result.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
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
    how: str = ""  # "won", "bye", "coin toss"

    @property
    def loser(self) -> Seat | None:
        return None if self.b is None else (self.b if self.winner is self.a else self.a)


Play = Callable[[Seat, Seat, int], tuple[str, "str | None", str]]
Say = Callable[[str, str], None]


def round_name(players: int, number: int) -> str:
    return {2: "Final", 3: "Semi-finals", 4: "Semi-finals"}.get(players) or (
        "Quarter-finals" if players <= 8 else f"Round {number}")


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


def knockout(seats: list[Seat], play: Play, say: Say, rng: random.Random | None = None,
             games_per_bout: int = 3,
             before: Callable[[Seat, Seat, str], None] = lambda a, b, r: None,
             after: Callable[[Bout], None] = lambda b: None,
             ) -> tuple[list[list[Bout]], list[tuple[int, Seat]]]:
    """Play the whole bracket. Returns the rounds and the ranking [(place, seat)], shared places
    for entrants knocked out in the same round."""
    rng = rng or random.Random()
    alive = list(seats)
    rng.shuffle(alive)
    rounds: list[list[Bout]] = []
    while len(alive) > 1:
        name = round_name(len(alive), len(rounds) + 1)
        say(f"\n  === {name} ===", "b")
        bouts = []
        for i in range(0, len(alive) - 1, 2):
            a, b = alive[i], alive[i + 1]
            say(f"\n  {a.billing()}  v  {b.billing()}", "b")
            before(a, b, name)
            bouts.append(bout(a, b, play, rng, say, games_per_bout))
            say(f"  -> {bouts[-1].winner.billing()} goes through", "m")
            after(bouts[-1])
        if len(alive) % 2:
            bouts.append(Bout(alive[-1], None, winner=alive[-1], how="bye"))
            say(f"\n  {alive[-1].billing()} has a bye", "dim")
        rounds.append(bouts)
        alive = [b.winner for b in bouts]
    return rounds, ranking(seats, rounds)


def ranking(seats: list[Seat], rounds: list[list[Bout]]) -> list[tuple[int, Seat]]:
    out_in: dict[int, int] = {}  # id(seat) -> round index it lost in
    for r, bouts in enumerate(rounds):
        for b in bouts:
            if b.loser:
                out_in[id(b.loser)] = r
    champion = [s for s in seats if id(s) not in out_in]
    places = [(1, s) for s in champion]
    for r in range(len(rounds) - 1, -1, -1):
        group = [s for s in seats if out_in.get(id(s)) == r]
        place = len(places) + 1
        places += [(place, s) for s in group]
    return places
