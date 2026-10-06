"""The core data types, and the symmetric and asymmetric matchers that run backends."""

from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Self

from .profiles import Profile, get_profile

LAST_REMAINING = "last-remaining"


@dataclass(frozen=True)
class Target:
    """Something known, which inputs are matched to.

    `aliases` are other names it's known by, e.g. a Welsh name or a party's
    registered ballot descriptions; inputs are matched against them as well as
    `label`. `description` is extra context shown to Jev and people, e.g.
    "(by-election)".
    """

    id: str
    label: str
    description: str | None = None
    aliases: tuple[str, ...] = ()

    def __post_init__(self):
        # Accept any iterable (e.g. a list from JSON), but store a tuple so targets stay hashable.
        object.__setattr__(self, "aliases", tuple(self.aliases))

    @property
    def names(self) -> list[str]:
        """The label and the aliases."""
        return [self.label, *self.aliases]

    @property
    def summary(self) -> str:
        """The label, aliases and description, for showing to Jev or a person."""
        text = self.label
        if self.aliases:
            text += f" (also known as: {'; '.join(self.aliases)})"
        if self.description:
            text += f" {self.description}"
        return text


@dataclass
class Match:
    """An input paired with the target it refers to."""

    input: str
    target: Target | None  # None means "confirmed no match" (e.g. a person said so)
    backend: str  # the name of the backend that made the match, or "last-remaining"
    confidence: float | None = None


@dataclass
class Suggestion:
    """A possible target for an input that a backend couldn't settle."""

    target: Target
    source: str
    score: float | None = None


# Each unmatched input's suggestions, in the order they were made.
Suggestions = dict[str, list[Suggestion]]


class MatchBackend(ABC):
    """One way of deciding which target an input refers to, e.g. comparing strings or asking a person."""

    name: str

    @abstractmethod
    def match(
        self,
        inputs: list[str],
        targets: list[Target],
        suggestions: Suggestions,
        profile: Profile,
        *,
        one_to_one: bool,
    ) -> list[Match]:
        """Return matches for any of `inputs`. May add to `suggestions` for the rest.

        `profile` is the matcher's profile, for backends that can be tuned to a
        kind of matching; others can ignore it.

        `one_to_one` is True when each target can only be matched to one input,
        and False in a ManyToOneMatcher, where several inputs can share a
        target. The matcher enforces this itself, so most backends can ignore
        it; it matters to backends that make several decisions in one go and
        need to know whether a target is still available afterwards.
        """


def dedupe_suggestions(suggestions: list[Suggestion]) -> list[Suggestion]:
    """`suggestions` without repeats of the same target from the same source, keeping the first."""
    seen = {}
    for s in suggestions:
        seen.setdefault((s.target.id, s.source), s)
    return list(seen.values())


def ordered_choices(text: str, targets: list[Target], suggestions: Suggestions) -> list[Target]:
    """`targets` sorted by label, with any suggested for `text` moved to the front."""
    available = {t.id: t for t in sorted(targets, key=lambda t: t.label)}
    suggested = {s.target.id: available[s.target.id] for s in suggestions.get(text, []) if s.target.id in available}
    return list(suggested.values()) + [t for tid, t in available.items() if tid not in suggested]


class BaseMatcher(ABC):
    """Matches inputs to targets by running backends in order.

    Use SymmetricMatcher or AsymmetricMatcher, which differ in how the inputs
    relate to the targets. `profile` (a built-in profile's name, or a Profile)
    tunes the backends to a kind of matching, e.g. "uk-divisions". Each backend
    only sees the inputs and targets that are still unmatched. Matches
    accumulate on the matcher, so `run` can be called again later with more
    backends, and the state can be saved with `to_dict` and restored with
    `from_partial` (e.g. between web requests).
    """

    mode: str
    # Whether each target can only be matched to one input. When it can, a
    # matched target is no longer available, clashing matches are rejected,
    # and the last input left is paired with the last target left.
    one_to_one = True

    def __init__(self, inputs: Iterable[str], targets: Iterable[Target], profile: str | Profile = "generic"):
        self.profile = get_profile(profile)
        # The profile's synonyms are applied first, so different ways of writing
        # the same thing (e.g. "Independent" and "Annibynnol") become one input.
        self.spellings: dict[str, list[str]] = {}
        for text in inputs:
            originals = self.spellings.setdefault(self.profile.canonical_name(text), [])
            if text not in originals:
                originals.append(text)
        inputs = list(self.spellings)
        targets = list(targets)
        self.check_counts(len(inputs), len(targets))
        self.matches: dict[str, Match] = {}
        self.unmatched_inputs: list[str] = inputs
        self.unmatched_targets: list[Target] = targets
        self.suggestions: Suggestions = {}
        self._pair_last_remaining()

    @abstractmethod
    def check_counts(self, n_inputs: int, n_targets: int) -> None:
        """Raise ValueError if this many distinct inputs and targets can't fit the mode."""

    def match_for(self, text: str) -> Match | None:
        """The match for an input as originally written, or None if it isn't matched yet.

        Inputs are matched after the profile's synonyms are applied, so
        `matches` is keyed by those names; this looks up any original spelling.
        """
        return self.matches.get(self.profile.canonical_name(text))

    @property
    def is_complete(self) -> bool:
        """True when every input has been decided (targets may be left over in asymmetric matching)."""
        return not self.unmatched_inputs

    def choices_for(self, text: str) -> list[Target]:
        """The targets still available for an unmatched input, suggested ones first."""
        return ordered_choices(text, self.unmatched_targets, self.suggestions)

    def run(self, backends: Iterable[MatchBackend]) -> Self:
        """Run `backends` in order on whatever is still unmatched, and return self.

        In one-to-one matching, matched targets are removed from later
        backends' options. If a backend matches several inputs to the same
        target, none of those matches are accepted (they're kept as
        suggestions instead). When one input and one target are left, they're
        matched to each other ("last-remaining"). A ManyToOneMatcher does none
        of these things: any number of inputs can match one target.

        Matches for inputs that aren't waiting for a match, or to targets that
        aren't available, are ignored.

        If a backend raises an exception, matches made by earlier backends in
        this run are kept, and nothing from the failing backend is applied.
        """
        for backend in backends:
            self._pair_last_remaining()
            if not self.unmatched_inputs:
                break
            # The backend gets its own copy of the suggestions, so if it raises,
            # nothing it added is kept.
            suggestions: Suggestions = defaultdict(list, {text: list(s) for text, s in self.suggestions.items()})
            waiting = set(self.unmatched_inputs)
            available = {t.id for t in self.unmatched_targets}
            found = [
                m
                for m in backend.match(
                    list(self.unmatched_inputs),
                    list(self.unmatched_targets),
                    suggestions,
                    self.profile,
                    one_to_one=self.one_to_one,
                )
                if m.input in waiting and (m.target is None or m.target.id in available)
            ]
            # How many inputs this backend matched to each target. Only a clash
            # in one-to-one matching; otherwise several inputs can share one.
            claims = Counter(m.target.id for m in found if m.target) if self.one_to_one else Counter()
            for m in found:
                if m.target and claims[m.target.id] > 1:
                    suggestions[m.input].append(Suggestion(m.target, f"{m.backend} (conflict)", m.confidence))
            self.suggestions = {text: dedupe_suggestions(s) for text, s in suggestions.items() if s}

            for m in found:
                if not m.target or claims[m.target.id] <= 1:
                    self.matches[m.input] = m
            self.unmatched_inputs = [i for i in self.unmatched_inputs if i not in self.matches]
            if self.one_to_one:
                used = {m.target.id for m in self.matches.values() if m.target}
                self.unmatched_targets = [t for t in self.unmatched_targets if t.id not in used]
        self._pair_last_remaining()
        return self

    def _pair_last_remaining(self) -> None:
        # Every remaining input belongs to one of the remaining targets, so one
        # of each must be a pair. In asymmetric matching with extra targets
        # there are always more targets than inputs, so this never happens. It
        # also stops happening once an input is matched to "no match", which
        # uses up an input without a target. In many-to-one matching the last
        # input could be any target, so it's never paired this way.
        if self.one_to_one and len(self.unmatched_inputs) == len(self.unmatched_targets) == 1:
            text = self.unmatched_inputs.pop()
            self.matches[text] = Match(text, self.unmatched_targets.pop(), LAST_REMAINING)

    def to_dict(self) -> dict:
        """The matcher's state as JSON-serialisable data, to restore with `from_partial`.

        Targets are saved by ID only, so the data stays small however many
        targets there are; pass the targets back to `from_partial`.
        """
        return {
            "mode": self.mode,
            "profile": self.profile.name,
            # Only names that combine other spellings; the rest are spelt as given.
            "spellings": {name: originals for name, originals in self.spellings.items() if originals != [name]},
            "matches": {
                text: {"target": m.target and m.target.id, "backend": m.backend, "confidence": m.confidence}
                for text, m in self.matches.items()
            },
            "unmatched_inputs": self.unmatched_inputs,
            "unmatched_targets": [t.id for t in self.unmatched_targets],
            "suggestions": {
                text: [{"target": s.target.id, "source": s.source, "score": s.score} for s in found]
                for text, found in self.suggestions.items()
            },
        }

    @classmethod
    def from_partial(cls, data: dict, targets: Iterable[Target], profile: str | Profile | None = None) -> Self:
        """Restore a matcher saved with `to_dict`, to run more backends on what's left.

        `targets` must include every target the data refers to, usually the
        same list the matcher was created with. The profile is looked up by the
        name saved in `data`; pass `profile` to restore with a custom Profile,
        which can't be saved as data.

        Raises ValueError if `data` was saved from the other kind of matcher,
        refers to targets that aren't in `targets`, or has a profile that isn't
        a built-in one and `profile` isn't passed.
        """
        if data.get("mode") != cls.mode:
            raise ValueError(f"{cls.__name__} can't continue {data.get('mode')!r} matching")
        by_id = {t.id: t for t in targets}
        referenced = {m["target"] for m in data["matches"].values() if m["target"] is not None}
        referenced |= set(data["unmatched_targets"])
        referenced |= {s["target"] for found in data["suggestions"].values() for s in found}
        if missing := sorted(referenced - by_id.keys()):
            raise ValueError(f"Saved data refers to targets that weren't passed in: {missing}")

        matcher = cls.__new__(cls)
        matcher.profile = get_profile(profile or data["profile"])
        matcher.matches = {
            text: Match(text, m["target"] and by_id[m["target"]], m["backend"], m["confidence"])
            for text, m in data["matches"].items()
        }
        matcher.unmatched_inputs = list(data["unmatched_inputs"])
        matcher.spellings = {name: [name] for name in [*matcher.matches, *matcher.unmatched_inputs]}
        matcher.spellings.update(data["spellings"])
        matcher.unmatched_targets = [by_id[tid] for tid in data["unmatched_targets"]]
        matcher.suggestions = {
            text: [Suggestion(by_id[s["target"]], s["source"], s["score"]) for s in found]
            for text, found in data["suggestions"].items()
        }
        return matcher


class SymmetricMatcher(BaseMatcher):
    """Match inputs and targets that are the same set of things, spelt differently.

    There must be exactly as many distinct inputs as targets (ValueError if
    not), and every input is matched to a different target.
    """

    mode = "symmetric"

    def check_counts(self, n_inputs: int, n_targets: int) -> None:
        if n_inputs != n_targets:
            raise ValueError(
                f"Symmetric matching needs the same number of distinct inputs and targets, "
                f"got {n_inputs} inputs and {n_targets} targets"
            )


class AsymmetricMatcher(BaseMatcher):
    """Match inputs that are each one of the targets, where there may be more targets than inputs.

    Targets that no input refers to are left unmatched. By default there must
    be at least as many targets as distinct inputs (ValueError if not). Pass
    `strict=False` to skip that check, e.g. in an import job where a few
    inputs may not match anything: there can then be more inputs than targets,
    and the extra ones are left unmatched instead of stopping the whole run.
    """

    mode = "asymmetric"
    # Only used when the matcher is created; a restored matcher doesn't check counts.
    strict = True

    def __init__(
        self,
        inputs: Iterable[str],
        targets: Iterable[Target],
        profile: str | Profile = "generic",
        *,
        strict: bool = True,
    ):
        self.strict = strict
        super().__init__(inputs, targets, profile)

    def check_counts(self, n_inputs: int, n_targets: int) -> None:
        if self.strict and n_inputs > n_targets:
            raise ValueError(
                f"Asymmetric matching needs at least as many targets as distinct inputs, "
                f"got {n_inputs} inputs and {n_targets} targets (pass strict=False to allow this)"
            )


class ManyToOneMatcher(BaseMatcher):
    """Match inputs where several may refer to the same target, e.g. the parties in a list of candidates.

    Each distinct input is matched to at most one target, but any number of
    inputs can be matched to the same target, so "Labour Party" and "Welsh
    Labour" can both be matched to Labour. Targets are never used up, so
    `unmatched_targets` is always every target. There's no count check, and no
    "last-remaining" pairing, since the last input left could be any target.
    """

    mode = "many-to-one"
    one_to_one = False

    def check_counts(self, n_inputs: int, n_targets: int) -> None:
        pass  # any number of inputs can match any number of targets
