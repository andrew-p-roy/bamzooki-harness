"""The arena's announcer: a cheap, expressive model that runs the show.

It introduces each challenger from a fact sheet (maker, release month and year, home country,
trivia), calls the challenge games from their stills, builds up each knockout match from both
Zooks' pictures and the tale of the tape, calls each result from the end-of-game stills, asks the
post-match interview questions, crowns the champion and signs off.

It keeps a short memory of the evening, so it can call back to earlier moments. Its tokens and
cost are counted like a challenger's. Choose it with --announcer / [arena] announcer, and its
style with --announcer-style / [arena] announcer_style (a STYLES name or your own description).
"""

from __future__ import annotations

import json
import re

DEFAULT_MODEL = "anthropic/claude-haiku-5.5"  # expressive, sees images, $0.10 / $0.50 per million tokens

STYLES = {
    "ringside": ("Your style: a booming ring announcer crossed with a Saturday-morning kids' TV presenter. "
                 "Big build-ups, playful nicknames for the Zooks, genuine delight at chaos, warm to losers."),
    "documentary": ("Your style: a hushed, awe-struck wildlife documentary narrator, observing these strange "
                    "creatures in their natural habitat with scientific wonder and the occasional dry joke."),
    "football": ("Your style: a breathless football commentator: rising excitement, vivid play-by-play, "
                 "a touch of poetry when something remarkable happens."),
    "noir": ("Your style: a hard-boiled 1940s detective narrating the case: rain, neon, and creatures "
             "with something to prove. Short sentences. Dry wit."),
}

SYSTEM = """You are the announcer of the BAMZOOKi Arena, a live show. AI models design BAMZOOKi creatures \
("Zooks": jointed, walking creatures made of blob-shaped parts) and test them in the real engine of the \
2006 BBC game, then fight each other's Zooks in a knockout, in front of an audience. {style}

You speak out loud to the audience. Write plain spoken sentences: no markdown, no lists, no headings, no \
emoji, no stage directions, no quotation marks around your whole answer. Facts must come from what you are \
told (fact sheets, results, pictures) or be things you are certain of. Never invent dates, places, \
numbers or results. When a fact isn't given, leave it out and have fun with what you do know. Keep to \
the word limit you're given."""

INTRO = """Introduce the next challenger as it steps into the arena. It's challenger number {n} tonight, \
taking on the challenge "{challenge}".

Fact sheet (true facts; use these):
{facts}

Mention who made it, the month and year it was released and its country of origin (if the fact sheet \
has them), plus a bit of trivia from the fact sheet. Then send it to work. Open and close differently \
from your earlier introductions. At most {words} words."""

CHALLENGE_CALL = """{model}'s Zook {zook} has just played its challenge game: {contest} against \
{opponent}. Result for {zook}: {outcome}. Details: {details}.
{apart}{pictures}Call the result for the audience in at most {words} words."""

MATCH = """The {contest}, {round}. Introduce this match.
Green corner: {a_model}{a_from}, with its Zook {a_zook}.
Red corner: {b_model}{b_from}, with its Zook {b_zook}.
Tale of the tape:
{tape}
{pictures}Build up the match in at most {words} words."""

GAME_CALL = """The {contest}, {round}: {green} (green) v {red} (red). {result}
Game details: {details}.
{apart}{pictures}Tell the audience how it was decided, in at most {words} words."""

QUESTION = """Post-match interview after the {round}: {result} You're about to talk to {who}, \
the model that designed {zook}, which {fate}. Ask it ONE short, specific interview question (one \
sentence, addressing it by name) about what just happened. Only the question."""

CHAMPION = """{model} has WON the {contest} knockout with its Zook {zook}! Its path: {path}. Final \
ranking: {ranking}. Crown the champion in at most {words} words, then hand over to it for its speech."""

CLOSING = """The show is over. How tonight went: {story}
Sign off for the audience in at most {words} words."""

PICTURES = "Pictures attached: {what}. Use what you see in them.\n"
APART = "How to tell them apart (green and red are only their sides of the arena, not their colours): {}\n"


class Announcer:
    def __init__(self, router, model: str = DEFAULT_MODEL, style: str = "ringside", vision: bool = True,
                 memory: int = 16):
        self.router = router
        self.model = model
        self.vision = vision
        self.memory = memory  # messages of the evening it keeps (besides the system prompt)
        self.style = STYLES.get(style, style)
        self.system = {"role": "system", "content": SYSTEM.format(style=self.style)}
        self.history: list[dict] = []
        self.cost = 0.0
        self.tokens = 0
        self.failed = 0  # consecutive failed calls (it goes quiet after three)
        self.last_error = ""  # why the last call failed, for the screen

    @property
    def quiet(self) -> bool:
        return self.failed >= 3

    async def speak(self, prompt: str, images: list[dict] | None = None) -> str:
        """One announcement ("" if the call failed). Pictures are only sent this once."""
        if self.quiet:
            return ""
        images = [i for i in (images or []) if i] if self.vision else []
        content = [{"type": "text", "text": prompt}, *images] if images else prompt
        messages = [self.system, *self.history, {"role": "user", "content": content}]
        text = ""
        for _ in range(2):  # one retry: a provider hiccup shouldn't leave a gap in the show
            try:
                data = await self.router.chat(self.model, messages, [])
            except Exception as err:  # noqa: BLE001 (the show goes on without it)
                self.last_error = str(err)[:200]
                continue
            usage = data.get("usage") or {}
            self.cost += float(usage.get("cost") or 0)
            self.tokens += int(usage.get("total_tokens") or 0)
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            if text := clean(msg.get("content") or ""):
                break
            self.last_error = f"empty reply (finish: {(data.get('choices') or [{}])[0].get('finish_reason')})"
        if not text:
            self.failed += 1
            return ""
        self.failed, self.last_error = 0, ""
        note = f" [{len(images)} picture(s) shown]" if images else ""
        self.history += [{"role": "user", "content": prompt + note}, {"role": "assistant", "content": text}]
        self.history = self.history[-self.memory:]
        return text

    # --- the show's moments ---
    async def introduce(self, n: int, challenge: str, facts: dict, words: int = 90) -> str:
        return await self.speak(INTRO.format(n=n, challenge=challenge, words=words,
                                             facts=json.dumps(facts, indent=1, ensure_ascii=False)))

    async def challenge_call(self, model: str, zook: str, contest: str, opponent: str, outcome: str,
                             details: str, stills: dict | None = None, apart: str = "", words: int = 60) -> str:
        pictures = PICTURES.format(what="the game's stills, Go | halfway / the finish | the end") if stills else ""
        return await self.speak(CHALLENGE_CALL.format(model=model, zook=zook, contest=contest, opponent=opponent,
                                                      outcome=outcome, details=details, pictures=pictures,
                                                      apart=APART.format(apart) if apart else "", words=words),
                                [stills] if stills else None)

    async def match(self, contest: str, round_name: str, a: tuple[str, str, str], b: tuple[str, str, str],
                    tape: list[str], card: dict | None = None, words: int = 80) -> str:
        """a, b: (model, where it's from, Zook name)."""
        pictures = PICTURES.format(what=f"{a[2]} (left, green) and {b[2]} (right, red)") if card else ""
        return await self.speak(MATCH.format(
            contest=contest, round=round_name, a_model=a[0], a_from=f" from {a[1]}" if a[1] else "", a_zook=a[2],
            b_model=b[0], b_from=f" from {b[1]}" if b[1] else "", b_zook=b[2], tape="\n".join(tape),
            pictures=pictures, words=words), [card] if card else None)

    async def game_call(self, contest: str, round_name: str, green: str, red: str, result: str, details: str,
                        stills: dict | None = None, apart: str = "", words: int = 60) -> str:
        from .showtime import END_LABELS

        pictures = PICTURES.format(what=f"the deciding game's stills, {END_LABELS}") if stills else ""
        return await self.speak(GAME_CALL.format(contest=contest, round=round_name, green=green, red=red,
                                                 result=result, details=details, pictures=pictures, words=words,
                                                 apart=APART.format(apart) if apart else ""),
                                [stills] if stills else None)

    async def question(self, round_name: str, result: str, who: str, zook: str, won: bool) -> str:
        q = await self.speak(QUESTION.format(round=round_name, result=result, who=who, zook=zook,
                                             fate="won" if won else "lost"))
        return _one_line(q)

    async def champion(self, model: str, contest: str, zook: str, path: str, ranking: str, words: int = 70) -> str:
        return await self.speak(CHAMPION.format(model=model, contest=contest, zook=zook, path=path,
                                                ranking=ranking, words=words))

    async def closing(self, story: str, words: int = 70) -> str:
        return await self.speak(CLOSING.format(story=story, words=words))


def clean(text: str) -> str:
    """Spoken text: markdown emphasis, headings, bullets and wrapping quotes taken out."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    text = re.sub(r"(\*\*|__|\*|`)", "", text)
    text = re.sub(r"^\s*(#+|[-•]|\d+\.)\s+", "", text, flags=re.M)
    text = text.strip()
    if len(text) > 1 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1].strip()
    return re.sub(r"\n{3,}", "\n\n", text)


def _one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
