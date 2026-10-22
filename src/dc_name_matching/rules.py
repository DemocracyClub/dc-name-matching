"""Rule-based backends, which compare normalised strings."""

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable

from .core import Match, MatchBackend, Suggestion, Suggestions, Target
from .profiles import Profile, collapse_whitespace, unchanged


class NormalisedMatch(MatchBackend):
    """Match when an input normalises to the same string as a target's label or alias.

    `variants` can generate other forms of an input (e.g. with a prefix
    removed), and `target_variants` of a target's names (e.g. an abbreviation in
    brackets). Both must return the name itself first: that's the whole-name
    form, and anything after it counts as a shortened form.

    A match on the whole input and a whole name is stronger than one that
    needed a shortened form of either. For each input, only its strongest
    matches count, and it's matched if they're all to one target. In
    one-to-one matching, if several inputs match one target, the strongest
    match wins; if they're equally strong, none of them is accepted. In
    many-to-one matching, they're all accepted.
    """

    def __init__(
        self,
        name: str,
        normalise: Callable[[str], str],
        variants: Callable[[str], Iterable[str]] = unchanged,
        target_variants: Callable[[str], Iterable[str]] = unchanged,
    ):
        self.name = name
        self.normalise = normalise
        self.variants = variants
        self.target_variants = target_variants

    def rules(self, profile: Profile) -> tuple[Callable, Callable, Callable]:
        """The (normalise, variants, target_variants) functions to use."""
        return self.normalise, self.variants, self.target_variants

    def target_names(self, target: Target, profile: Profile) -> list[str]:
        """The names to compare inputs with; each counts as a whole name."""
        return target.names

    def match(
        self,
        inputs: list[str],
        targets: list[Target],
        suggestions: Suggestions,
        profile: Profile,
        *,
        one_to_one: bool,
    ) -> list[Match]:
        normalise, variants, target_variants = self.rules(profile)

        # normalised form -> {target id: (target, strength)}, where strength 0
        # is a whole label or alias and 1 is a shortened form of one.
        index: dict[str, dict[str, tuple[Target, int]]] = defaultdict(dict)
        for target in targets:
            for name in self.target_names(target, profile):
                for i, form in enumerate(target_variants(name)):
                    key = normalise(form)
                    if not key:
                        continue  # e.g. "-" cleans to nothing, and shouldn't match "?"
                    strength = 0 if i == 0 else 1
                    known = index[key].get(target.id)
                    if known is None or strength < known[1]:
                        index[key][target.id] = (target, strength)

        # Each input's best candidate, as (strength, input, target); lower is stronger.
        candidates = []
        for text in inputs:
            found: dict[str, tuple[Target, int]] = {}
            for i, form in enumerate(variants(text)):
                for tid, (target, target_strength) in index.get(normalise(form), {}).items():
                    strength = (0 if i == 0 else 1) + target_strength
                    if tid not in found or strength < found[tid][1]:
                        found[tid] = (target, strength)
            if not found:
                continue
            best = min(strength for _, strength in found.values())
            top = [target for target, strength in found.values() if strength == best]
            if len(top) == 1:
                candidates.append((best, text, top[0]))
            else:
                suggestions[text].extend(Suggestion(t, f"{self.name} (ambiguous)") for t, _ in found.values())

        if not one_to_one:
            return [Match(text, target, self.name) for _, text, target in candidates]

        # Accept the strongest candidates first, one input per target.
        matches = []
        taken: set[str] = set()
        for strength in sorted({c[0] for c in candidates}):
            level = [c for c in candidates if c[0] == strength]
            claims = Counter(target.id for _, _, target in level)
            for _, text, target in level:
                if target.id in taken:
                    continue  # a stronger match already has it
                if claims[target.id] == 1:
                    matches.append(Match(text, target, self.name))
                else:
                    suggestions[text].append(Suggestion(target, f"{self.name} (ambiguous)"))
            taken |= set(claims)
        return matches


class ExactMatch(NormalisedMatch):
    def __init__(self):
        super().__init__("exact", lambda s: s)


class CaseInsensitiveMatch(NormalisedMatch):
    def __init__(self):
        super().__init__("case-insensitive", lambda s: collapse_whitespace(s).casefold())


class CleanedFirstWordLastWordMatch(NormalisedMatch):
    """
        Match based on the first and last words after applying the matcher profile's cleaning function and variants.
    """

    def __init__(self):
        self.name = "cleaned-first-word-last-word"

    def rules(self, profile: Profile):
        def normalise(text: str) -> str:
            words = profile.clean(text).split()
            return f"{words[0]} {words[-1]}" if words else ""
        return normalise, profile.variants, profile.target_variants


class DehyphenedCleanedMatch(NormalisedMatch):
    """
        Remove hyphens before applying the matcher profile's cleaning function and variants.

        The basic clean replaces hyphens with " ", so "Pen-y-groes" becomes "pen y groes".
        This removes hyphens entirely, so "Pen-y-groes" becomes "penygroes" instead.

        This could possibly lead to false positives so only use with a target set that
        you're sure does not contain conflicting hyphenated/unhyphenated names.
    """

    def __init__(self):
        self.name = "dehyphened-cleaned"

    def rules(self, profile: Profile):
        def normalise(text: str) -> str:
            dehyphened_text = text.replace("-", "")
            cleaned_text = profile.clean(dehyphened_text)
            return cleaned_text if cleaned_text else ""
        return normalise, profile.variants, profile.target_variants


class CleanedMatch(NormalisedMatch):
    """Match after tidying up names with the matcher's profile's cleaning function and variants.

    A target is also matched by every synonym of its label and aliases.
    """

    def __init__(self):
        self.name = "cleaned"

    def rules(self, profile: Profile) -> tuple[Callable, Callable, Callable]:
        return profile.clean, profile.variants, profile.target_variants

    def target_names(self, target: Target, profile: Profile) -> list[str]:
        names = target.names
        return list(dict.fromkeys([*names, *(s for name in names for s in profile.synonyms_of(name))]))
