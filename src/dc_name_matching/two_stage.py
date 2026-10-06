"""Two-stage matching: match each input to a parent, then to one of that parent's children.

For example, match the party names in a list of candidates to parties, then
work out which of each party's registered descriptions was used. Matching the
parents first keeps the options small: the second stage only chooses between
one parent's name and its own children, rather than every child of every
parent at once.

    matcher = TwoStageMatcher(names, parties, descriptions_by_party, profile="uk-political-parties")
    matcher.run([ExactMatch(), CaseInsensitiveMatch(), CleanedMatch()])
    matcher.match_for("Welsh Labour")   # TwoStageMatch(parent=..., child=...)

Both stages are many-to-one: several inputs can match the same parent, and the
same child.
"""

import dataclasses
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Self

from .core import ManyToOneMatcher, Match, MatchBackend, Target
from .profiles import Profile, get_profile


@dataclass
class TwoStageMatch:
    """An input's match at each stage.

    `parent` is the stage one match, or None if the input isn't matched yet.
    `child` is the stage two match, or None if the input isn't matched to a
    parent with children, or the second stage couldn't settle it. If
    `child.target` is the parent itself, the input used the parent's own name
    rather than one of its children's.
    """

    parent: Match | None
    child: Match | None


class TwoStageMatcher:
    """Match inputs to parents, then each to one of its parent's children.

    `parents` are the targets for the first stage, e.g. parties. `children`
    maps a parent's ID to its children, e.g. the party's registered
    descriptions; parents without children can be left out.

    In the first stage, each parent is matched by its own names and by all of
    its children's names, so an input written as a child's name still finds
    its parent. In the second stage, an input matched to a parent with
    children is matched again, against that parent and its children only.
    """

    def __init__(
        self,
        inputs: Iterable[str],
        parents: Iterable[Target],
        children: Mapping[str, Iterable[Target]],
        profile: str | Profile = "generic",
    ):
        self.profile = get_profile(profile)
        parents = list(parents)
        self.children: dict[str, list[Target]] = {parent_id: list(kids) for parent_id, kids in children.items()}
        self._parents_by_id = {parent.id: parent for parent in parents}
        if unknown := sorted(self.children.keys() - self._parents_by_id.keys()):
            raise ValueError(f"Children given for parents that weren't passed in: {unknown}")

        # The first stage: each parent, known by its children's names as well as its own.
        self.parents = ManyToOneMatcher(inputs, [self._with_childrens_names(p) for p in parents], self.profile)
        # The second stage: one matcher per batch of inputs matched to the same
        # parent, by input. Inputs whose parent has no children have none.
        self._child_matchers: dict[str, ManyToOneMatcher] = {}

    def _with_childrens_names(self, parent: Target) -> Target:
        names = [name for child in self.children.get(parent.id, []) for name in child.names]
        return dataclasses.replace(parent, aliases=(*parent.aliases, *names))

    def run(self, backends: Iterable[MatchBackend], child_backends: Iterable[MatchBackend] | None = None) -> Self:
        """Run `backends` on the first stage, then `child_backends` on the second, and return self.

        `child_backends` defaults to `backends`. Each stage only sees what's
        still unmatched, so `run` can be called again later with more backends,
        e.g. ConsoleMatch to ask a person about what's left at both stages.
        Inputs matched to a parent since the last run go on to the second
        stage.
        """
        backends = list(backends)
        child_backends = backends if child_backends is None else list(child_backends)
        self.parents.run(backends)

        # Start the second stage for inputs newly matched to a parent with children.
        new_inputs: dict[str, list[str]] = {}
        for text, match in self.parents.matches.items():
            if match.target and self.children.get(match.target.id) and text not in self._child_matchers:
                new_inputs.setdefault(match.target.id, []).append(text)
        for parent_id, texts in new_inputs.items():
            options = [self._parents_by_id[parent_id], *self.children[parent_id]]
            matcher = ManyToOneMatcher(texts, options, self.profile)
            for text in texts:
                self._child_matchers[text] = matcher

        for matcher in self.child_matchers:
            matcher.run(child_backends)
        return self

    @property
    def child_matchers(self) -> list[ManyToOneMatcher]:
        """The second stage's matchers, one for each batch of inputs matched to the same parent."""
        return list({id(m): m for m in self._child_matchers.values()}.values())

    def child_matcher_for(self, text: str) -> ManyToOneMatcher | None:
        """The second stage's matcher for an input, or None if it hasn't reached the second stage."""
        return self._child_matchers.get(self.profile.canonical_name(text))

    def match_for(self, text: str) -> TwoStageMatch:
        """An input's matches at both stages, as originally written (any spelling that synonyms combined)."""
        child_matcher = self.child_matcher_for(text)
        return TwoStageMatch(self.parents.match_for(text), child_matcher.match_for(text) if child_matcher else None)

    @property
    def matches(self) -> dict[str, TwoStageMatch]:
        """Every input matched in the first stage, with its matches at both stages."""
        return {text: self.match_for(text) for text in self.parents.matches}
