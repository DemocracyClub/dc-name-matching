"""Match two lists of names that refer to the same things but are spelt differently.

Use SymmetricMatcher when the inputs and targets are the same set of things
(so the lists are the same length, once duplicate inputs are removed),
AsymmetricMatcher when every input is one of the targets but there may be more
targets than inputs, or ManyToOneMatcher when several inputs can refer to the
same target. Run backends in order, cheapest first; each only sees what
earlier ones didn't match:

    matcher = SymmetricMatcher(inputs, targets)
    matcher.run([ExactMatch(), CaseInsensitiveMatch(), CleanedMatch(), JevMatch(), ConsoleMatch()])
    matcher.matches, matcher.unmatched_inputs, matcher.is_complete

In a web app, run the automatic backends, save matcher.to_dict() and show a
form for what's left, then apply the person's choices:

    SymmetricMatcher.from_partial(saved, targets).run([ManualMatch(decisions)])

A profile tunes the backends for a kind of matching, including known
alternative names (synonyms), e.g.
SymmetricMatcher(inputs, targets, profile="uk-political-parties").
TwoStageMatcher matches inputs to parents, then to one of each parent's
children, e.g. to parties and then to their registered descriptions.
See the README for more.
"""

from .core import (
    LAST_REMAINING,
    AsymmetricMatcher,
    BaseMatcher,
    ManyToOneMatcher,
    Match,
    MatchBackend,
    Suggestion,
    Suggestions,
    SymmetricMatcher,
    Target,
)
from .human import ConsoleMatch, ManualMatch
from .jev import JevError, JevMatch
from .profiles import PROFILES, Profile, get_profile
from .rules import CaseInsensitiveMatch, CleanedMatch, ExactMatch, NormalisedMatch, CleanedFirstWordLastWordMatch, DehyphenedCleanedMatch
from .two_stage import TwoStageMatch, TwoStageMatcher

__all__ = [
    "LAST_REMAINING",
    "AsymmetricMatcher",
    "BaseMatcher",
    "CaseInsensitiveMatch",
    "CleanedMatch",
    "ConsoleMatch",
    "ExactMatch",
    "JevError",
    "JevMatch",
    "ManualMatch",
    "ManyToOneMatcher",
    "Match",
    "MatchBackend",
    "NormalisedMatch",
    "CleanedFirstWordLastWordMatch",
    "DehyphenedCleanedMatch",
    "PROFILES",
    "Profile",
    "Suggestion",
    "Suggestions",
    "SymmetricMatcher",
    "Target",
    "TwoStageMatch",
    "TwoStageMatcher",
    "get_profile",
]
