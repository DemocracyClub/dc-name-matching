import copy
import io
import json

import pytest

import dc_name_matching.jev
from dc_name_matching import (
    LAST_REMAINING,
    AsymmetricMatcher,
    CaseInsensitiveMatch,
    CleanedFirstWordLastWordMatch,
    DehyphenedCleanedMatch,
    CleanedMatch,
    ConsoleMatch,
    ExactMatch,
    JevMatch,
    ManualMatch,
    ManyToOneMatcher,
    Match,
    PROFILES,
    Profile,
    Suggestion,
    SymmetricMatcher,
    Target,
    TwoStageMatcher,
)
from dc_name_matching.profiles import division_clean, division_variants, name_variants

ABBEY = Target("local.x.abbey.2023-05-04", "Abbey")
ST_MARYS = Target("local.x.st-marys.2023-05-04", "St Mary's")
WESTWOOD = Target("local.x.westwood-jacksdale.2023-05-04", "Westwood & Jacksdale")
HUCKNALL_N = Target("local.x.hucknall-north.2023-05-04", "Hucknall North")
HUCKNALL_S = Target("local.x.hucknall-south.2023-05-04", "Hucknall South")
FIRST_MIDDLE_LAST = Target("fml_id", "First Middle Last")
UNHYPHENATED = Target("hyp_id", "Penygroes")


def sym(inputs, targets, backends, profile="generic"):
    return SymmetricMatcher(inputs, targets, profile=profile).run(backends)


def asym(inputs, targets, backends, profile="generic", strict=True):
    return AsymmetricMatcher(inputs, targets, profile=profile, strict=strict).run(backends)


def matched(matcher):
    return {text: (m.target.id if m.target else None, m.backend) for text, m in matcher.matches.items()}


def all_targets(matcher):
    """Every target a matcher knows about: matched and unmatched."""
    return [m.target for m in matcher.matches.values() if m.target] + matcher.unmatched_targets


def restore(matcher):
    """Save and restore a matcher through JSON, as a web app would between requests."""
    return type(matcher).from_partial(json.loads(json.dumps(matcher.to_dict())), all_targets(matcher))


class Spy(ExactMatch):
    """An exact-match backend that records what it was given."""

    def __init__(self):
        super().__init__()
        self.calls = []

    def match(self, inputs, targets, suggestions, profile, *, one_to_one):
        self.calls.append((list(inputs), list(targets)))
        return super().match(inputs, targets, suggestions, profile, one_to_one=one_to_one)


# --- Symmetric matching ------------------------------------------------------


def test_symmetric_unequal_lengths_raise():
    with pytest.raises(ValueError, match="2 inputs and 3 targets"):
        SymmetricMatcher(["Abbey", "Hucknall North"], [ABBEY, HUCKNALL_N, HUCKNALL_S])


def test_lengths_compared_after_deduping_inputs():
    matcher = sym(["Abbey", "Abbey"], [ABBEY], [ExactMatch()])
    assert list(matcher.matches) == ["Abbey"]


def test_backends_only_see_unmatched_inputs_and_targets():
    spy = Spy()
    sym(
        ["Abbey", "hucknall south", "nowhere", "elsewhere"],
        [ABBEY, HUCKNALL_S, HUCKNALL_N, ST_MARYS],
        [ExactMatch(), CaseInsensitiveMatch(), spy],
    )
    assert spy.calls == [(["nowhere", "elsewhere"], [HUCKNALL_N, ST_MARYS])]


def test_run_can_be_called_again_with_more_backends():
    matcher = SymmetricMatcher(["Abbey", "HUCKNALL NORTH", "x"], [ABBEY, HUCKNALL_N, HUCKNALL_S])
    matcher.run([ExactMatch()])
    assert matcher.unmatched_inputs == ["HUCKNALL NORTH", "x"]
    matcher.run([CaseInsensitiveMatch()])
    assert matched(matcher)["HUCKNALL NORTH"] == (HUCKNALL_N.id, "case-insensitive")
    assert matched(matcher)["x"] == (HUCKNALL_S.id, LAST_REMAINING)


def test_last_remaining_input_and_target_are_paired():
    spy = Spy()
    matcher = sym(["Abbey", "Hucknal N"], [ABBEY, HUCKNALL_N], [ExactMatch(), spy])
    assert matched(matcher) == {"Abbey": (ABBEY.id, "exact"), "Hucknal N": (HUCKNALL_N.id, LAST_REMAINING)}
    assert spy.calls == []  # paired before the next backend was asked


def test_single_input_and_target_are_paired_on_creation():
    matcher = SymmetricMatcher(["anything"], [ABBEY])
    assert matched(matcher) == {"anything": (ABBEY.id, LAST_REMAINING)}
    assert matcher.is_complete


def test_last_remaining_pairing_after_final_backend():
    matcher = sym(["Abbey", "Hucknal N"], [ABBEY, HUCKNALL_N], [ExactMatch()])
    assert matched(matcher)["Hucknal N"] == (HUCKNALL_N.id, LAST_REMAINING)
    assert matcher.unmatched_inputs == [] and matcher.unmatched_targets == []


def test_no_last_remaining_pairing_after_a_no_match():
    # At the console, "a" is given no match, so one input is left against two targets.
    console, _ = human(["n", "s"])
    spy = Spy()
    matcher = sym(["a", "b"], [ABBEY, ST_MARYS], [console, spy])
    assert matched(matcher) == {"a": (None, "console")}
    assert spy.calls == [(["b"], [ABBEY, ST_MARYS])]


def test_conflicting_matches_in_one_backend_are_rejected():
    # Both normalise to "Abbey" case-insensitively, so neither is accepted.
    matcher = sym(["ABBEY", "abbey", "x"], [ABBEY, HUCKNALL_N, HUCKNALL_S], [CaseInsensitiveMatch()])
    assert matcher.matches == {}
    assert [s.target for s in matcher.suggestions["ABBEY"]] == [ABBEY]


# --- Asymmetric matching -----------------------------------------------------


def test_asymmetric_allows_extra_targets():
    matcher = asym(
        ["Abbey", "hucknall north"], [ABBEY, HUCKNALL_N, HUCKNALL_S, ST_MARYS], [ExactMatch(), CaseInsensitiveMatch()]
    )
    assert matcher.is_complete
    assert matcher.unmatched_targets == [HUCKNALL_S, ST_MARYS]


def test_asymmetric_raises_when_targets_are_fewer_than_inputs():
    with pytest.raises(ValueError, match="at least as many targets as distinct inputs, got 3 inputs and 2 targets"):
        AsymmetricMatcher(["a", "b", "c"], [ABBEY, HUCKNALL_N])


def test_asymmetric_non_strict_allows_more_inputs_than_targets():
    # e.g. a scrape that still names a ward from old boundaries.
    matcher = asym(["Abbey", "Old Ward", "Hucknall North"], [ABBEY, HUCKNALL_N], [ExactMatch()], strict=False)
    assert matched(matcher) == {"Abbey": (ABBEY.id, "exact"), "Hucknall North": (HUCKNALL_N.id, "exact")}
    assert matcher.unmatched_inputs == ["Old Ward"]
    assert not matcher.is_complete


def test_asymmetric_non_strict_round_trips():
    matcher = asym(["Abbey", "x", "y"], [ABBEY, HUCKNALL_N], [ExactMatch()], strict=False)
    restored = restore(matcher)
    restored.run([ManualMatch({"x": HUCKNALL_N.id})])
    assert restored.unmatched_inputs == ["y"] and restored.unmatched_targets == []


def test_asymmetric_does_not_pair_the_last_input_while_there_are_extra_targets():
    spy = Spy()
    matcher = asym(["Abbey", "Hucknal N"], [ABBEY, HUCKNALL_N, HUCKNALL_S], [ExactMatch(), spy])
    assert matched(matcher) == {"Abbey": (ABBEY.id, "exact")}
    assert spy.calls == [(["Hucknal N"], [HUCKNALL_N, HUCKNALL_S])]
    assert matcher.unmatched_inputs == ["Hucknal N"]


def test_asymmetric_with_equal_counts_pairs_the_last_input():
    # Every input is one of the targets and there are as many of each, so it's the same set.
    matcher = asym(["Abbey", "Hucknal N"], [ABBEY, HUCKNALL_N], [ExactMatch()])
    assert matched(matcher)["Hucknal N"] == (HUCKNALL_N.id, LAST_REMAINING)


# --- Rule-based backends -----------------------------------------------------


def test_exact_match():
    matcher = sym(["Abbey", "abbey north", "x"], [ABBEY, HUCKNALL_N, HUCKNALL_S], [ExactMatch()])
    assert matched(matcher) == {"Abbey": (ABBEY.id, "exact")}
    assert matcher.unmatched_inputs == ["abbey north", "x"]


def test_case_insensitive_match():
    matcher = sym(["HUCKNALL  north", "x", "y"], [HUCKNALL_N, ABBEY, ST_MARYS], [CaseInsensitiveMatch()])
    assert matched(matcher) == {"HUCKNALL  north": (HUCKNALL_N.id, "case-insensitive")}


def test_first_word_last_word_match():
    matcher = sym(
        ["First Last", "Last First", "x",],
        [FIRST_MIDDLE_LAST, ABBEY, ST_MARYS],
        [CleanedFirstWordLastWordMatch()],
        )
    assert matched(matcher) == {
        "First Last": (FIRST_MIDDLE_LAST.id, "cleaned-first-word-last-word"),
        }

def test_cleaned_dehyphenated_match():
    matcher = sym(
        ["Pen-y-groes", "x", "y",],
        [UNHYPHENATED, ABBEY, ST_MARYS],
        [DehyphenedCleanedMatch()],
        )
    assert matched(matcher) == {
        "Pen-y-groes": (UNHYPHENATED.id, "dehyphened-cleaned"),
        }


@pytest.mark.parametrize(
    "text, target",
    [
        ("westwood and jacksdale", WESTWOOD),
        ("Westwood & Jacksdale Ward", WESTWOOD),
        ("st marys", ST_MARYS),
        ("St. Mary’s", ST_MARYS),
        ("sutton: st mary`s", ST_MARYS),
        ("[from may 2025] abbey", ABBEY),
        ("abbey - 2023", ABBEY),
        ("Abbey (2 seats)", ABBEY),
        ("abbey / abaty", ABBEY),
        ("ashfield - hucknall north", HUCKNALL_N),
    ],
)
def test_cleaned_match(text, target):
    others = [t for t in (ABBEY, ST_MARYS, WESTWOOD, HUCKNALL_N) if t != target][:2]
    matcher = sym([text, "x", "y"], [target, *others], [CleanedMatch()], profile="uk-divisions")
    assert matched(matcher) == {text: (target.id, "cleaned")}


def test_cleaned_does_not_guess_partial_names():
    matcher = sym(["hucknall", "x"], [HUCKNALL_N, HUCKNALL_S], [CleanedMatch()], profile="uk-divisions")
    assert matcher.matches == {}


def test_division_clean_and_variants():
    assert division_clean("Westwood & Jacksdale Ward") == "westwood and jacksdale"
    assert "bramhall north" in division_variants("bramhall north - 2023")

def test_name_variants():
    results = name_variants("Baynham, Beverley")
    assert "Beverley Baynham" in name_variants("Baynham, Beverley")


# --- Aliases -----------------------------------------------------------------

BROUGHTON_S = Target("local.flintshire.broughton-south", "Broughton South", aliases=("De Brychdyn",))
NEWCASTLE_IND = Target("party:905", "Newcastle Independents", aliases=("Local Community Candidate",))
LABOUR_ALIASED = Target("party:53", "Labour Party", aliases=("Welsh Labour", "Llafur Cymru"))


@pytest.mark.parametrize(
    "backend, text, target",
    [
        (ExactMatch(), "De Brychdyn", BROUGHTON_S),
        (CaseInsensitiveMatch(), "DE BRYCHDYN", BROUGHTON_S),
        (CleanedMatch(), "local community candidate", NEWCASTLE_IND),
        (CleanedMatch(), "Llafur Cymru", LABOUR_ALIASED),
    ],
)
def test_inputs_match_aliases(backend, text, target):
    matcher = asym([text], [BROUGHTON_S, NEWCASTLE_IND, LABOUR_ALIASED, ABBEY], [backend])
    assert matched(matcher) == {text: (target.id, backend.name)}


def test_aliases_can_be_given_as_a_list_and_survive_saving():
    target = Target("t", "Broughton South", aliases=["De Brychdyn"])
    assert target.aliases == ("De Brychdyn",)
    matcher = SymmetricMatcher(["a", "b"], [target, ABBEY])
    assert restore(matcher).unmatched_targets[0] == target


def test_an_alias_shared_by_two_targets_is_ambiguous():
    a = Target("a", "Alpha Party", aliases=("Local Candidate",))
    b = Target("b", "Beta Party", aliases=("Local Candidate",))
    matcher = asym(["Local Candidate"], [a, b, ABBEY], [ExactMatch()])
    assert matcher.matches == {}
    assert {s.target for s in matcher.suggestions["Local Candidate"]} == {a, b}


def test_summary_shows_aliases_and_description():
    target = Target("t", "Broughton South", description="(by-election)", aliases=("De Brychdyn",))
    assert target.summary == "Broughton South (also known as: De Brychdyn) (by-election)"


def test_jev_and_console_see_aliases(monkeypatch):
    offered = {}

    def send(body, api_key):
        offered.update(body["questions"]["match"]["criteria"])
        return {"answers": {"match": {"choice": "no_match", "confidence": 1}}}

    monkeypatch.setattr(dc_name_matching.jev, "send", send)
    console, output = human(["s", "s"])
    sym(["x", "y"], [BROUGHTON_S, ABBEY], [JevMatch(), console])
    assert offered[BROUGHTON_S.id] == "Broughton South (also known as: De Brychdyn)"
    assert any("Broughton South (also known as: De Brychdyn)" in line for line in output)


# --- Match strength ----------------------------------------------------------

FLINT_OAKENHOLT = Target("flint-oakenholt", "Flint: Oakenholt")
OAKENHOLT = Target("oakenholt", "Oakenholt")


def test_whole_name_beats_a_shortened_form_for_one_input():
    # The whole input fits "Flint: Oakenholt"; the part after ":" fits "Oakenholt".
    matcher = asym(["Flint: Oakenholt Ward"], [FLINT_OAKENHOLT, OAKENHOLT, ABBEY], [CleanedMatch()],
                   profile="uk-divisions")
    assert matched(matcher) == {"Flint: Oakenholt Ward": (FLINT_OAKENHOLT.id, "cleaned")}


def test_whole_name_match_beats_a_shortened_match_from_another_input():
    # "oakenholt" matches Oakenholt on its whole name; "Castle: Oakenholt" only
    # after dropping "Castle:". The whole-name match wins instead of both being rejected.
    matcher = asym(["Castle: Oakenholt", "oakenholt"], [OAKENHOLT, FLINT_OAKENHOLT, ABBEY], [CleanedMatch()],
                   profile="uk-divisions")
    assert matched(matcher) == {"oakenholt": (OAKENHOLT.id, "cleaned")}
    assert "Castle: Oakenholt" in matcher.unmatched_inputs


def test_input_order_does_not_matter():
    targets = [OAKENHOLT, FLINT_OAKENHOLT, ABBEY]
    first = asym(["Castle: Oakenholt", "oakenholt"], targets, [CleanedMatch()], profile="uk-divisions")
    second = asym(["oakenholt", "Castle: Oakenholt"], targets, [CleanedMatch()], profile="uk-divisions")
    assert matched(first) == matched(second)


def test_shortened_forms_that_fit_two_targets_are_ambiguous():
    # "cannock chase" and "chadsmoor" are both shortened forms, so neither is preferred.
    targets = [Target("cc", "Cannock Chase"), Target("ch", "Chadsmoor"), ABBEY]
    matcher = asym(["cannock chase - chadsmoor"], targets, [CleanedMatch()], profile="uk-divisions")
    assert matcher.matches == {}


# --- Profiles ----------------------------------------------------------------

LABOUR = Target("party:53", "Labour Party")
UKIP = Target("party:85", "UK Independence Party (UKIP)")
LIBERAL = Target("party:54", "The Liberal Party")
LLAFUR = Target("party:53w", "Welsh Labour / Llafur Cymru")


def test_generic_profile_does_not_use_division_rules():
    # "ward" is only dropped by the uk-divisions profile.
    matcher = sym(["St Marys Ward", "x"], [ST_MARYS, ABBEY], [CleanedMatch()], profile="generic")
    assert matcher.matches == {}
    matcher = sym(["St Marys Ward", "x"], [ST_MARYS, ABBEY], [CleanedMatch()], profile="uk-divisions")
    assert matched(matcher)["St Marys Ward"] == (ST_MARYS.id, "cleaned")


@pytest.mark.parametrize(
    "text, target",
    [
        ("Labour Party", LABOUR),
        ("UKIP", UKIP),  # an abbreviation in the target's brackets
        ("uk independence party", UKIP),
        ("Liberal", LIBERAL),  # "The" and "Party" are ignored
        ("Llafur Cymru", LLAFUR),  # one side of an English / Welsh label
    ],
)
def test_party_profile_cleaned_match(text, target):
    matcher = asym([text], [LABOUR, UKIP, LIBERAL, LLAFUR], [CleanedMatch()], profile="uk-political-parties")
    assert matched(matcher) == {text: (target.id, "cleaned")}


@pytest.mark.parametrize(
    "profile, text, labels, expected",
    [
        # YNR stores bilingual names as "English | Welsh".
        ("uk-political-parties", "Llafur Cymru", ["Welsh Labour | Llafur Cymru", "Abbey Party"], 0),
        ("uk-political-parties", "Welsh Labour|Llafur Cymru", ["Welsh Labour", "Abbey Party"], 0),
        ("uk-divisions", "De Brychdyn", ["Broughton South | De Brychdyn", "Abbey"], 0),
        ("uk-divisions", "broughton south | de brychdyn", ["Broughton South", "Abbey"], 0),
    ],
)
def test_english_and_welsh_names_split_on_a_pipe(profile, text, labels, expected):
    targets = [Target(str(i), label) for i, label in enumerate(labels)]
    matcher = asym([text], targets, [CleanedMatch()], profile=profile)
    assert matched(matcher) == {text: (str(expected), "cleaned")}


INDEPENDENT = Target("ynmp-party:2", "Independent")


def test_synonyms_combine_spellings_before_counting():
    # One statement of persons nominated: three ways of saying Independent, plus a party.
    inputs = ["INDEPENDENT", "Annibynnol", "", "Labour Party", "Annibynnol"]
    matcher = SymmetricMatcher(inputs, [INDEPENDENT, LABOUR], profile="uk-political-parties")
    assert matcher.unmatched_inputs == ["Independent", "Labour Party"]
    assert matcher.spellings["Independent"] == ["INDEPENDENT", "Annibynnol", ""]
    matcher.run([ExactMatch()])
    assert matched(matcher) == {"Independent": (INDEPENDENT.id, "exact"), "Labour Party": (LABOUR.id, "exact")}
    for text in inputs:
        assert matcher.match_for(text).target in (INDEPENDENT, LABOUR)
    assert matcher.match_for("").target == INDEPENDENT


def test_synonym_keys_ignore_case_and_punctuation():
    matcher = SymmetricMatcher(["No  Description.", "Labour Party"], [INDEPENDENT, LABOUR],
                               profile="uk-political-parties")
    matcher.run([ExactMatch()])
    assert matcher.match_for("No  Description.").target == INDEPENDENT


def test_synonyms_only_replace_whole_inputs():
    matcher = AsymmetricMatcher(["The Independent Group", "Labour Party"],
                                [INDEPENDENT, LABOUR, Target("party:9", "The Independent Group")],
                                profile="uk-political-parties")
    assert matcher.unmatched_inputs == ["The Independent Group", "Labour Party"]


@pytest.mark.parametrize("label", ["Conservative and Unionist Party", "Conservative Party", "The Conservatives"])
@pytest.mark.parametrize("text", ["Tory", "TORIES", "Conservative", "The Conservative Party",
                                  "Conservative and Unionist Party"])
def test_known_alternative_names_match_whichever_name_the_target_uses(label, text):
    conservative = Target("party:52", label)
    targets = [conservative, LABOUR, Target("party:90", "Liberal Democrats")]
    matcher = asym([text], targets, [ExactMatch(), CaseInsensitiveMatch(), CleanedMatch()],
                   profile="uk-political-parties")
    assert matcher.match_for(text).target == conservative


def test_synonyms_of_a_target_alias_match():
    target = Target("party:52", "Conservative and Unionist Party (GB)", aliases=["Conservatives"])
    matcher = asym(["Tory"], [target, LABOUR], [CleanedMatch()], profile="uk-political-parties")
    assert matcher.match_for("Tory").target == target


def test_synonyms_combine_spellings_of_a_party():
    inputs = ["Tory", "Conservative Party", "Labour Party"]
    matcher = SymmetricMatcher(inputs, [Target("party:52", "Conservative Party"), LABOUR],
                               profile="uk-political-parties")
    assert matcher.unmatched_inputs == ["Conservative and Unionist Party", "Labour Party"]
    matcher.run([CleanedMatch()])
    assert matcher.is_complete


def test_scottish_greens_are_not_matched_as_the_green_party():
    scottish_greens = Target("party:130", "Scottish Green Party")
    matcher = asym(["Greens"], [scottish_greens, LABOUR, ABBEY], [CleanedMatch()], profile="uk-political-parties")
    assert matcher.matches == {}  # left for Jev or a person


def test_a_name_in_two_synonym_groups_raises():
    with pytest.raises(ValueError, match="'kx' is in more than one group"):
        Profile("p", "P", "Pick.", clean=str.casefold, synonyms={"King's Cross": ["KX"], "Kings X": ["kx"]})


def test_without_synonyms_each_spelling_is_its_own_input():
    with pytest.raises(ValueError, match="3 inputs and 2 targets"):
        SymmetricMatcher(["INDEPENDENT", "Annibynnol", "Labour Party"], [INDEPENDENT, LABOUR])


def test_spellings_survive_saving():
    matcher = sym(["Annibynnol", "independent", "Labour Party"], [INDEPENDENT, LABOUR], [], profile="uk-political-parties")
    saved = matcher.to_dict()
    assert saved["spellings"] == {"Independent": ["Annibynnol", "independent"]}
    restored = restore(matcher)
    assert restored.spellings == matcher.spellings
    restored.run([ExactMatch()])
    assert restored.match_for("Annibynnol").target == INDEPENDENT


def test_custom_profile_synonyms():
    profile = Profile("stations", "Stations", "Pick the station.", clean=str.casefold,
                      synonyms={"London King's Cross": ["KX", "King's Cross"]})
    matcher = sym(["kx", "Euston"], [Target("KGX", "King's Cross"), Target("EUS", "Euston")], [CleanedMatch()],
                  profile=profile)
    assert matcher.match_for("kx").target.id == "KGX"


def test_profile_is_passed_to_backends():
    seen = []

    class Recorder(ExactMatch):
        def match(self, inputs, targets, suggestions, profile, *, one_to_one):
            seen.append(profile.name)
            return []

    sym(["a", "b"], [ABBEY, ST_MARYS], [Recorder()], profile="uk-divisions")
    assert seen == ["uk-divisions"]


def test_unknown_profile_raises():
    with pytest.raises(ValueError, match="Unknown profile 'uk-divisons'; built-in profiles are generic"):
        SymmetricMatcher(["a"], [ABBEY], profile="uk-divisons")


def test_custom_profile():
    shouty = Profile("shouty", "Names in capitals", "Pick the option.", clean=str.upper)
    matcher = sym(["abbey", "x"], [ABBEY, ST_MARYS], [CleanedMatch()], profile=shouty)
    assert matched(matcher)["abbey"] == (ABBEY.id, "cleaned")


# --- Jev ---------------------------------------------------------------------


@pytest.fixture(autouse=True)
def jev_api_key(monkeypatch):
    monkeypatch.setenv("DEFAPI_API_KEY", "test-key")


def fake_jev(answers):
    """Replace dc_name_matching.jev.send with a fake returning {state: (choice, confidence)}."""

    def send(body, api_key):
        options = body["questions"]["match"]["criteria"]
        picked, confidence = answers[body["state"]]
        assert picked in options
        return {
            "answers": {"match": {"choice": picked, "confidence": confidence, "probabilities": {picked: confidence}}},
            "consumed": "0.0001",
        }

    return send


def test_jev_match_uses_threshold_and_leaves_suggestions(monkeypatch):
    monkeypatch.setattr(
        dc_name_matching.jev,
        "send",
        fake_jev({"Hucknal Nrth": (HUCKNALL_N.id, 0.97), "Hucknal": (HUCKNALL_S.id, 0.6), "Zzz": ("no_match", 0.99)}),
    )
    jev = JevMatch(threshold=0.9)
    matcher = sym(["Hucknal Nrth", "Hucknal", "Zzz"], [HUCKNALL_N, HUCKNALL_S, ABBEY], [jev])
    assert matched(matcher) == {"Hucknal Nrth": (HUCKNALL_N.id, "jev")}
    assert [s.target for s in matcher.suggestions["Hucknal"]] == [HUCKNALL_S]
    assert matcher.unmatched_inputs == ["Hucknal", "Zzz"]
    assert jev.cost == pytest.approx(0.0003)


def test_jev_only_offered_remaining_targets(monkeypatch):
    offered = {}

    def send(body, api_key):
        offered.update(body["questions"]["match"]["criteria"])
        return {"answers": {"match": {"choice": "no_match", "confidence": 1}}}

    monkeypatch.setattr(dc_name_matching.jev, "send", send)
    sym(["Abbey", "Somewhere", "Else"], [ABBEY, HUCKNALL_N, HUCKNALL_S], [ExactMatch(), JevMatch()])
    assert ABBEY.id not in offered and HUCKNALL_N.id in offered


@pytest.mark.parametrize(
    "profile, instructions, expected",
    [
        ("generic", None, PROFILES["generic"].jev_instructions),
        ("uk-political-parties", None, PROFILES["uk-political-parties"].jev_instructions),
        ("uk-political-parties", "My own prompt", "My own prompt"),
    ],
)
def test_jev_instructions_come_from_the_profile(monkeypatch, profile, instructions, expected):
    sent = []

    def send(body, api_key):
        sent.append(body["questions"]["match"]["instructions"])
        return {"answers": {"match": {"choice": "no_match", "confidence": 1}}}

    monkeypatch.setattr(dc_name_matching.jev, "send", send)
    sym(["a", "b"], [ABBEY, ST_MARYS], [JevMatch(instructions=instructions)], profile=profile)
    assert set(sent) == {expected}


def test_jev_errors_pass_input_on(monkeypatch):
    def send(body, api_key):
        raise RuntimeError("HTTP 500")

    monkeypatch.setattr(dc_name_matching.jev, "send", send)
    matcher = sym(["a", "b"], [HUCKNALL_N, HUCKNALL_S], [JevMatch()])
    assert matcher.unmatched_inputs == ["a", "b"]


# --- Console -----------------------------------------------------------------


def human(answers):
    answers = iter(answers)
    output = []
    return ConsoleMatch(ask=lambda prompt: next(answers), say=output.append), output


def test_console_match_picks_numbers_and_removes_chosen_targets():
    # Options are listed sorted by label: 1 Abbey, 2 Hucknall North, 3 Hucknall South
    console, output = human(["n", "x", "2", "2"])
    matcher = sym(["nowhere", "hn", "hs"], [HUCKNALL_N, HUCKNALL_S, ABBEY], [console])
    assert matched(matcher) == {
        "nowhere": (None, "console"),
        "hn": (HUCKNALL_N.id, "console"),
        # "2" again, as Hucknall North was removed. Asked rather than paired
        # automatically, because "nowhere" had no match.
        "hs": (HUCKNALL_S.id, "console"),
    }
    assert "Didn't understand that." in output


def test_console_is_not_asked_about_the_last_remaining_input():
    console, _ = human(["1"])  # 1 Hucknall North, 2 Hucknall South
    matcher = sym(["hn", "hs"], [HUCKNALL_N, HUCKNALL_S], [console])
    assert matched(matcher) == {"hn": (HUCKNALL_N.id, "console"), "hs": (HUCKNALL_S.id, LAST_REMAINING)}


def test_console_skip_and_quit():
    console, _ = human(["s", "q"])
    matcher = sym(["a", "b", "c"], [ABBEY, HUCKNALL_N, HUCKNALL_S], [console])
    assert matcher.matches == {}
    assert matcher.unmatched_inputs == ["a", "b", "c"]


def test_console_sees_earlier_suggestions(monkeypatch):
    monkeypatch.setattr(dc_name_matching.jev, "send", fake_jev({"Hucknal": (HUCKNALL_S.id, 0.6), "x": ("no_match", 0.9)}))
    console, output = human(["s", "s"])
    sym(["Hucknal", "x"], [HUCKNALL_S, HUCKNALL_N], [JevMatch(), console])
    assert any("Hucknall South  (jev, 0.6)" in line for line in output)


def test_console_asks_about_every_input_when_there_are_extra_targets():
    console, _ = human(["1", "1"])  # 1 Hucknall North, then 1 Hucknall South
    matcher = asym(["hn", "hs"], [HUCKNALL_N, HUCKNALL_S, ST_MARYS], [console])
    assert matched(matcher) == {"hn": (HUCKNALL_N.id, "console"), "hs": (HUCKNALL_S.id, "console")}
    assert matcher.unmatched_targets == [ST_MARYS]


def test_console_is_a_no_op_when_stdin_is_not_a_terminal(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    console = ConsoleMatch()
    assert not console.interactive
    matcher = sym(["a", "b"], [HUCKNALL_N, HUCKNALL_S], [console])
    assert matcher.matches == {} and matcher.unmatched_inputs == ["a", "b"]


# --- Saving and continuing (e.g. in a web app) -------------------------------


def test_state_round_trips_through_json(monkeypatch):
    monkeypatch.setattr(dc_name_matching.jev, "send", fake_jev({"Hucknal": (HUCKNALL_S.id, 0.6), "x": ("no_match", 0.9)}))
    matcher = sym(["Abbey", "Hucknal", "x"], [ABBEY, HUCKNALL_N, HUCKNALL_S], [ExactMatch(), JevMatch()])
    restored = restore(matcher)
    assert type(restored) is SymmetricMatcher
    assert vars(restored) == vars(matcher)


def test_asymmetric_state_round_trips_and_continues():
    matcher = asym(["Abbey", "x"], [ABBEY, HUCKNALL_N, HUCKNALL_S], [ExactMatch()])
    restored = restore(matcher)
    assert type(restored) is AsymmetricMatcher
    restored.run([ManualMatch({"x": HUCKNALL_S.id})])
    assert restored.is_complete
    assert restored.unmatched_targets == [HUCKNALL_N]


def test_profile_is_saved_and_restored():
    matcher = sym(["Abbey", "x", "y"], [ABBEY, HUCKNALL_N, HUCKNALL_S], [ExactMatch()], profile="uk-divisions")
    assert matcher.to_dict()["profile"] == "uk-divisions"
    assert restore(matcher).profile is PROFILES["uk-divisions"]


def test_custom_profile_must_be_passed_back_to_from_partial():
    shouty = Profile("shouty", "Names in capitals", "Pick the option.", clean=str.upper)
    saved = SymmetricMatcher(["a", "b"], [ABBEY, ST_MARYS], profile=shouty).to_dict()
    with pytest.raises(ValueError, match="Unknown profile 'shouty'"):
        SymmetricMatcher.from_partial(saved, [ABBEY, ST_MARYS])
    assert SymmetricMatcher.from_partial(saved, [ABBEY, ST_MARYS], profile=shouty).profile is shouty


def test_from_partial_refuses_the_other_mode():
    saved = SymmetricMatcher(["Abbey", "x"], [ABBEY, HUCKNALL_N]).to_dict()
    with pytest.raises(ValueError, match="AsymmetricMatcher can't continue 'symmetric' matching"):
        AsymmetricMatcher.from_partial(saved, [ABBEY, HUCKNALL_N])


def test_saved_data_has_target_ids_not_targets():
    big = Target("big", "Big Party", description="A description", aliases=[f"Alias {i}" for i in range(50)])
    matcher = asym(["Abbey", "x"], [ABBEY, big, HUCKNALL_N], [ExactMatch()])
    saved = matcher.to_dict()
    assert saved["unmatched_targets"] == ["big", HUCKNALL_N.id]
    assert saved["matches"]["Abbey"]["target"] == ABBEY.id
    assert "Alias" not in json.dumps(saved)


def test_from_partial_needs_every_saved_target():
    saved = asym(["Abbey", "x"], [ABBEY, HUCKNALL_N, HUCKNALL_S], [ExactMatch()]).to_dict()
    with pytest.raises(ValueError, match=r"targets that weren't passed in: \['local.x.hucknall-south.2023-05-04'\]"):
        AsymmetricMatcher.from_partial(saved, [ABBEY, HUCKNALL_N])
    # Extra targets are fine, e.g. passing the whole list again.
    assert AsymmetricMatcher.from_partial(saved, [ABBEY, HUCKNALL_N, HUCKNALL_S, ST_MARYS]).unmatched_targets == [
        HUCKNALL_N, HUCKNALL_S
    ]


def test_choices_for_puts_suggestions_first(monkeypatch):
    monkeypatch.setattr(dc_name_matching.jev, "send", fake_jev({"Hucknal": (HUCKNALL_S.id, 0.6), "x": ("no_match", 0.9)}))
    matcher = sym(["Hucknal", "x", "Abbey"], [ABBEY, HUCKNALL_N, HUCKNALL_S], [ExactMatch(), JevMatch()])
    assert matcher.choices_for("Hucknal") == [HUCKNALL_S, HUCKNALL_N]
    assert matcher.choices_for("x") == [HUCKNALL_N, HUCKNALL_S]


def test_manual_decisions_complete_a_saved_matcher():
    targets = [ABBEY, HUCKNALL_N, HUCKNALL_S, ST_MARYS]
    saved = sym(["Abbey", "hn?", "hs?", "sm?"], targets, [ExactMatch()]).to_dict()
    original = copy.deepcopy(saved)

    # One decision leaves two pairs; a second request decides one, and the last is paired.
    step1 = SymmetricMatcher.from_partial(saved, targets).run([ManualMatch({"hn?": HUCKNALL_N.id})])
    assert step1.unmatched_inputs == ["hs?", "sm?"]
    step2 = restore(step1).run([ManualMatch({"hs?": HUCKNALL_S.id})])
    assert step2.is_complete
    assert matched(step2) == {
        "Abbey": (ABBEY.id, "exact"),
        "hn?": (HUCKNALL_N.id, "manual"),
        "hs?": (HUCKNALL_S.id, "manual"),
        "sm?": (ST_MARYS.id, LAST_REMAINING),
    }
    assert saved == original  # from_partial doesn't change the saved data


class AddsThenFails(ExactMatch):
    """A backend that adds suggestions, then raises."""

    def match(self, inputs, targets, suggestions, profile, *, one_to_one):
        for text in inputs:
            suggestions[text].append(Suggestion(targets[0], "fails"))
        raise RuntimeError("backend failed")


def test_a_failing_backend_leaves_no_suggestions_behind():
    # "ABBEY" and "abbey" both claim Abbey, so both already have suggestions.
    matcher = sym(["ABBEY", "abbey", "x"], [ABBEY, HUCKNALL_N, HUCKNALL_S], [CaseInsensitiveMatch()])
    before = matcher.to_dict()
    with pytest.raises(RuntimeError):
        matcher.run([AddsThenFails()])
    assert matcher.to_dict() == before


class Claims(ExactMatch):
    """A backend that returns the given matches, whatever it's offered."""

    def __init__(self, *matches):
        super().__init__()
        self.claims = matches

    def match(self, inputs, targets, suggestions, profile, *, one_to_one):
        return [Match(text, target, "claims") for text, target in self.claims]


def test_matches_to_unavailable_targets_are_ignored():
    matcher = sym(["Abbey", "Hillside", "River"], [ABBEY, HUCKNALL_N, HUCKNALL_S], [ExactMatch()])
    made_up = Target("made-up", "Made up")
    matcher.run([Claims(("Hillside", ABBEY), ("River", made_up))])  # Abbey is already matched
    assert matched(matcher) == {"Abbey": (ABBEY.id, "exact")}
    assert matcher.unmatched_inputs == ["Hillside", "River"]


def test_running_a_backend_again_does_not_repeat_suggestions():
    matcher = sym(["ABBEY", "abbey", "x"], [ABBEY, HUCKNALL_N, HUCKNALL_S], [CaseInsensitiveMatch()])
    first = list(matcher.suggestions["ABBEY"])
    matcher.run([CaseInsensitiveMatch(), CaseInsensitiveMatch()])
    assert matcher.suggestions["ABBEY"] == first


def test_manual_no_match_decision():
    matcher = SymmetricMatcher(["a", "b", "c"], [ABBEY, HUCKNALL_N, HUCKNALL_S])
    matcher.run([ManualMatch({"a": None, "b": ABBEY.id})])
    assert matched(matcher) == {"a": (None, "manual"), "b": (ABBEY.id, "manual")}
    assert matcher.unmatched_inputs == ["c"]  # not paired: "a" had no match, so 1 input vs 2 targets


@pytest.mark.parametrize(
    "decisions, error",
    [
        ({"Abbey": HUCKNALL_N.id}, "Not waiting for a match"),  # already matched
        ({"hn?": ABBEY.id}, "Not available"),  # target already used
        ({"hn?": "made-up"}, "Not available"),
        ({"hn?": HUCKNALL_N.id, "hs?": HUCKNALL_N.id}, "more than one input"),
    ],
)
def test_manual_decisions_are_validated(decisions, error):
    matcher = sym(["Abbey", "hn?", "hs?"], [ABBEY, HUCKNALL_N, HUCKNALL_S], [ExactMatch()])
    before = matcher.to_dict()
    with pytest.raises(ValueError, match=error):
        matcher.run([ManualMatch(decisions)])
    assert matcher.to_dict() == before  # nothing was applied


def test_names_that_clean_to_nothing_do_not_match():
    matcher = asym(["?"], [Target("dash", "-"), ABBEY, ST_MARYS], [CleanedMatch()])
    assert matcher.matches == {}


def test_jev_malformed_response_passes_input_on(monkeypatch):
    monkeypatch.setattr(dc_name_matching.jev, "send", lambda body, api_key: {"error": "unexpected"})
    matcher = sym(["a", "b"], [HUCKNALL_N, HUCKNALL_S], [JevMatch()])
    assert matcher.unmatched_inputs == ["a", "b"]


# --- Many-to-one matching ----------------------------------------------------

LABOUR_WITH_ALIAS = Target("PP53", "Labour Party", aliases=["Labour Movement"])
CONSERVATIVES = Target("PP52", "Conservative and Unionist Party")


def many(inputs, targets, backends, profile="uk-political-parties"):
    return ManyToOneMatcher(inputs, targets, profile=profile).run(backends)


def test_many_to_one_matches_several_inputs_to_one_target():
    matcher = many(["Labour Party", "Labour Movement", "Tory"], [LABOUR_WITH_ALIAS, CONSERVATIVES],
                   [ExactMatch(), CaseInsensitiveMatch(), CleanedMatch()])
    assert {text: m.target.id for text, m in matcher.matches.items()} == {
        "Labour Party": "PP53", "Labour Movement": "PP53", "Conservative and Unionist Party": "PP52",
    }
    assert matcher.unmatched_targets == [LABOUR_WITH_ALIAS, CONSERVATIVES]  # never used up


def test_one_to_one_matchers_reject_the_same_clash():
    matcher = AsymmetricMatcher(["Labour Party", "Labour Movement"], [LABOUR_WITH_ALIAS, CONSERVATIVES, ABBEY],
                                profile="uk-political-parties").run([ExactMatch()])
    assert matcher.matches == {}


def test_many_to_one_has_no_count_check_or_last_remaining_pairing():
    spy = Spy()
    matcher = many(["Labour Party", "Lab", "Grn", "LD"], [LABOUR_WITH_ALIAS], [ExactMatch(), spy])
    assert matched(matcher) == {"Labour Party": ("PP53", "exact")}
    # "Lab" etc. were left for the next backend, not paired with the only target.
    assert spy.calls == [(["Lab", "Grn", "LD"], [LABOUR_WITH_ALIAS])]


def test_many_to_one_still_leaves_an_input_that_fits_two_targets():
    a = Target("a", "Alpha Party", aliases=["Local Candidate"])
    b = Target("b", "Beta Party", aliases=["Local Candidate"])
    matcher = many(["Local Candidate"], [a, b], [ExactMatch()])
    assert matcher.matches == {}


def test_many_to_one_manual_decisions_can_share_a_target():
    matcher = ManyToOneMatcher(["Lab", "Labr"], [LABOUR_WITH_ALIAS, CONSERVATIVES])
    matcher.run([ManualMatch({"Lab": "PP53", "Labr": "PP53"})])
    assert matched(matcher) == {"Lab": ("PP53", "manual"), "Labr": ("PP53", "manual")}


def test_many_to_one_console_offers_every_target_each_time():
    console, output = human(["1", "1"])  # 1 Conservative..., 2 Labour Party, both times
    matcher = many(["Tories?", "Cons?"], [LABOUR_WITH_ALIAS, CONSERVATIVES], [console], profile="generic")
    assert matched(matcher) == {"Tories?": ("PP52", "console"), "Cons?": ("PP52", "console")}


def test_many_to_one_state_round_trips():
    matcher = many(["Labour Party", "Labour Movement", "x"], [LABOUR_WITH_ALIAS, CONSERVATIVES], [ExactMatch()])
    restored = ManyToOneMatcher.from_partial(json.loads(json.dumps(matcher.to_dict())),
                                             [LABOUR_WITH_ALIAS, CONSERVATIVES])
    assert vars(restored) == vars(matcher)
    with pytest.raises(ValueError, match="can't continue 'many-to-one' matching"):
        AsymmetricMatcher.from_partial(matcher.to_dict(), [LABOUR_WITH_ALIAS, CONSERVATIVES])


# --- Two-stage matching ------------------------------------------------------

LABOUR_PARTY = Target("PP53", "Labour Party")
DESCRIPTIONS = {
    "PP53": [Target("PP53__1", "Labour in Wales", aliases=["Llafur Cymru"]), Target("PP53__2", "Labour Candidate")],
    "PP52": [Target("PP52__1", "Conservative in Wales")],
}
PARTIES = [LABOUR_PARTY, CONSERVATIVES, INDEPENDENT]
RULES = [ExactMatch(), CaseInsensitiveMatch(), CleanedMatch()]


def two_stage(inputs, backends=RULES, child_backends=None):
    matcher = TwoStageMatcher(inputs, PARTIES, DESCRIPTIONS, profile="uk-political-parties")
    return matcher.run(backends, child_backends)


def ids(result):
    return (result.parent and result.parent.target.id, result.child and result.child.target.id)


def test_two_stage_matches_the_parent_then_the_child():
    matcher = two_stage(["Labour Party", "Labour in Wales", "LLAFUR CYMRU", "Tory", "Conservative in Wales", ""])
    assert ids(matcher.match_for("Labour Party")) == ("PP53", "PP53")  # the party's own name
    assert ids(matcher.match_for("Labour in Wales")) == ("PP53", "PP53__1")
    assert ids(matcher.match_for("LLAFUR CYMRU")) == ("PP53", "PP53__1")
    assert ids(matcher.match_for("Tory")) == ("PP52", "PP52")
    assert ids(matcher.match_for("Conservative in Wales")) == ("PP52", "PP52__1")
    assert ids(matcher.match_for("")) == ("ynmp-party:2", None)  # Independent has no children


def test_two_stage_second_stage_only_offers_the_parents_own_children():
    spy = Spy()
    two_stage(["Welsh Lab"], backends=[ManualMatch({"Welsh Lab": "PP53"})], child_backends=[spy])
    assert spy.calls == [(["Welsh Lab"], [LABOUR_PARTY, *DESCRIPTIONS["PP53"]])]


def test_two_stage_keeps_the_parent_when_the_child_is_unclear():
    matcher = two_stage(["Welsh Lab"], backends=[ManualMatch({"Welsh Lab": "PP53"})], child_backends=RULES)
    assert ids(matcher.match_for("Welsh Lab")) == ("PP53", None)
    assert matcher.child_matcher_for("Welsh Lab").unmatched_inputs == ["Welsh Lab"]


def test_two_stage_run_again_reaches_newly_matched_inputs():
    matcher = two_stage(["Labour in Wales", "Welsh Lab"])
    assert matcher.match_for("Welsh Lab").parent is None
    matcher.run([ManualMatch({"Welsh Lab": "PP53"})], child_backends=[ManualMatch({"Welsh Lab": "PP53__1"})])
    assert ids(matcher.match_for("Welsh Lab")) == ("PP53", "PP53__1")
    assert ids(matcher.match_for("Labour in Wales")) == ("PP53", "PP53__1")


def test_two_stage_matches_lists_every_parent_match():
    matcher = two_stage(["Labour Party", "nobody knows"])
    assert {text: ids(m) for text, m in matcher.matches.items()} == {"Labour Party": ("PP53", "PP53")}


def test_two_stage_children_must_belong_to_a_given_parent():
    with pytest.raises(ValueError, match=r"parents that weren't passed in: \['PP99'\]"):
        TwoStageMatcher(["x"], PARTIES, {"PP99": [Target("PP99__1", "Nobody")]})


def test_two_stage_decisions_applied_to_each_stage_as_the_readme_says():
    matcher = two_stage(["Welsh Lab"])
    matcher.parents.run([ManualMatch({"Welsh Lab": "PP53"})])
    matcher.run([])  # sends it on to the second stage
    matcher.child_matcher_for("Welsh Lab").run([ManualMatch({"Welsh Lab": "PP53__1"})])
    assert ids(matcher.match_for("Welsh Lab")) == ("PP53", "PP53__1")
