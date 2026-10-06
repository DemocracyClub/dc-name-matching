"""Backends where a person decides: at the console, or elsewhere (e.g. a web form)."""

import sys
from collections import Counter
from collections.abc import Callable

from .core import LAST_REMAINING, Match, MatchBackend, Suggestions, Target
from .profiles import Profile


class ManualMatch(MatchBackend):
    """Apply decisions a person made elsewhere, e.g. submitted from a web form.

    `decisions` maps an input to a target id, or to None for "no match". Inputs
    not in `decisions` stay unmatched. Raises ValueError if an input isn't
    waiting for a match, a target isn't available, or (in one-to-one matching)
    a target is chosen twice.
    """

    name = "manual"

    def __init__(self, decisions: dict[str, str | None]):
        self.decisions = decisions

    def match(
        self,
        inputs: list[str],
        targets: list[Target],
        suggestions: Suggestions,
        profile: Profile,
        *,
        one_to_one: bool,
    ) -> list[Match]:
        if unknown := sorted(set(self.decisions) - set(inputs)):
            raise ValueError(f"Not waiting for a match: {unknown}")
        by_id = {t.id: t for t in targets}
        chosen = [tid for tid in self.decisions.values() if tid is not None]
        if unavailable := [tid for tid in chosen if tid not in by_id]:
            raise ValueError(f"Not available to match: {unavailable}")
        if one_to_one and (repeated := [tid for tid, n in Counter(chosen).items() if n > 1]):
            raise ValueError(f"Chosen for more than one input: {repeated}")
        return [Match(text, by_id[tid] if tid else None, self.name) for text, tid in self.decisions.items()]


def stdin_is_interactive() -> bool:
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except ValueError:  # stdin has been closed
        return False


class ConsoleMatch(MatchBackend):
    """Ask a person in an interactive terminal to pick a target for each remaining input.

    Does nothing (leaving inputs unmatched) unless stdin is an interactive
    terminal, so it's safe to leave in a list of backends that might run under a web
    server, a cron job or a pipe. Passing `ask` skips that check (for tests).

    Suggestions from earlier backends are listed first. Answers: a number to
    pick that target, "n" for no match, "s" to skip (leave unmatched), "q" to
    skip all remaining inputs. In one-to-one matching, a chosen target isn't
    offered again, and as in BaseMatcher.run, when one input and one target are
    left (and no input has been skipped or matched to "no match"), they're
    paired without asking. In many-to-one matching, every target is offered
    for every input.
    """

    name = "console"

    def __init__(self, ask: Callable[[str], str] | None = None, say: Callable[[str], None] = print):
        self.interactive = ask is not None or stdin_is_interactive()
        self.ask = ask or input
        self.say = say

    def match(
        self,
        inputs: list[str],
        targets: list[Target],
        suggestions: Suggestions,
        profile: Profile,
        *,
        one_to_one: bool,
    ) -> list[Match]:
        if not self.interactive:
            return []
        available = sorted(targets, key=lambda t: t.label)
        matches = []
        balanced = one_to_one and len(inputs) == len(targets)
        for i, text in enumerate(inputs, 1):
            if not available:
                break
            if balanced and i == len(inputs) and len(available) == 1:
                matches.append(Match(text, available[0], LAST_REMAINING))
                break
            numbered = dict(enumerate(available, 1))
            number_of = {t.id: n for n, t in numbered.items()}

            self.say(f"\n[{i}/{len(inputs)}] No automatic match for: {text!r}")
            shown = {s.target.id: s for s in suggestions.get(text, []) if s.target.id in number_of}
            if shown:
                self.say("Suggestions:")
                for s in shown.values():
                    score = f", {s.score}" if s.score is not None else ""
                    self.say(f"  {number_of[s.target.id]:>4}  {s.target.label}  ({s.source}{score})")
            self.say("All remaining options:")
            for n, t in numbered.items():
                self.say(f"  {n:>4}  {t.summary}")

            while True:
                answer = self.ask("Pick a number, (n)o match, (s)kip, (q)uit: ").strip().lower()
                if answer == "q":
                    return matches
                if answer == "s":
                    balanced = False
                    break
                if answer == "n":
                    matches.append(Match(text, None, self.name))
                    balanced = False
                    break
                if answer.isdigit() and int(answer) in numbered:
                    target = numbered[int(answer)]
                    matches.append(Match(text, target, self.name))
                    if one_to_one:
                        available.remove(target)
                    break
                self.say("Didn't understand that.")
        return matches
