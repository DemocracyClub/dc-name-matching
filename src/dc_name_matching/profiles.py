"""Profiles: settings that tune backends for a particular kind of matching.

A profile holds the instructions JevMatch gives Jev, the cleaning function and
variants CleanedMatch uses, and synonyms: known alternative names, which the
matcher uses to combine inputs and CleanedMatch uses to match targets. It's set
on the matcher, which passes it to every backend it runs, as the name of a
built-in profile or a Profile of your own:

    SymmetricMatcher(names, wards, profile="uk-divisions")
    AsymmetricMatcher(names, parties, profile="uk-political-parties")
"""

import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field


def unchanged(text: str) -> list[str]:
    """The default variants: just the name itself."""
    return [text]


@dataclass(frozen=True)
class Profile:
    name: str
    description: str
    jev_instructions: str
    # How CleanedMatch normalises inputs and target labels before comparing them.
    clean: Callable[[str], str]
    # Other forms of an input for CleanedMatch to try, e.g. with a prefix removed.
    # Must return the input itself first; later forms count as weaker matches.
    variants: Callable[[str], list[str]] = unchanged
    # Other forms of a target label for CleanedMatch to try, e.g. an abbreviation in brackets.
    # Must return the label itself first, as with `variants`.
    target_variants: Callable[[str], list[str]] = unchanged
    # Known alternative names, as {main name: [other names]}, e.g.
    # {"Conservative and Unionist Party": ["Conservatives", "Tory"]}. Inputs with
    # any name in a group are combined under the main name, and CleanedMatch
    # matches a target with any name in a group to every name in it. Names are
    # compared after `clean`, so case and punctuation don't matter.
    synonyms: Mapping[str, Iterable[str]] = field(default_factory=dict)
    # Cleaned name -> the main name of its group.
    _main_names: dict[str, str] = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        synonyms = {main: tuple(others) for main, others in self.synonyms.items()}
        main_names = {}
        for main, others in synonyms.items():
            for text in (main, *others):
                if main_names.setdefault(self.clean(text), main) != main:
                    raise ValueError(f"{text!r} is in more than one group of synonyms")
        object.__setattr__(self, "synonyms", synonyms)
        object.__setattr__(self, "_main_names", main_names)

    def canonical_name(self, text: str) -> str:
        """The main name of `text`'s group of synonyms, or `text` if it isn't in one."""
        return self._main_names.get(self.clean(text), text)

    def synonyms_of(self, text: str) -> list[str]:
        """Every name in `text`'s group of synonyms, or [] if it isn't in one."""
        main = self._main_names.get(self.clean(text))
        return [main, *self.synonyms[main]] if main is not None else []


# --- Shared helpers ----------------------------------------------------------


def collapse_whitespace(text: str) -> str:
    return " ".join(text.split())


def basic_clean(text: str) -> str:
    """Lower-case, and remove accents, apostrophes and punctuation; '&' becomes 'and'."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    text = re.sub(r"['’‘`´]", "", text)  # "St Mary's" / "St Mary’s" / "st marys"
    text = text.replace("&", " and ")
    text = re.sub(r"[^\w\s]", " ", text)
    return collapse_whitespace(text)


def split_variants(text: str, separators: tuple[str, ...]) -> list[str]:
    """The first and last parts of `text` split on each separator it contains."""
    variants = []
    for sep in separators:
        if sep in text:
            first, *_, last = text.split(sep)
            variants += [first, last]
    return variants


def dedupe(variants: list[str]) -> list[str]:
    """`variants` stripped of surrounding spaces, without blanks or repeats, in order."""
    return [v.strip() for v in dict.fromkeys(variants) if v.strip()]


# --- UK divisions (wards and electoral divisions) ----------------------------


def division_clean(text: str) -> str:
    """basic_clean, and drop a trailing 'ward', 'division' or 'electoral division'."""
    return re.sub(r" (electoral division|division|ward)$", "", basic_clean(text))


def division_variants(text: str) -> list[str]:
    """Candidate names hidden in a messy ward name.

    Handles e.g. "[from may 2025] abbey", "bagillt / bagillt" and
    "bagillt | bagillt" (English and Welsh), "bramhall north - 2023",
    "cannock chase - chadsmoor", "tewkesbury: churchdown", "abbey (2 seats)".
    """
    text = collapse_whitespace(text)
    stripped = re.sub(r"^\[[^\]]*\]\s*", "", text)  # "[from may 2025] ..."
    stripped = re.sub(r"\s*\((\d+ seats?|\d+ vacanc(y|ies))\)$", "", stripped, flags=re.I)
    stripped = re.sub(r"\s*-\s*(19|20)\d\d$", "", stripped)  # "... - 2023"
    variants = [text, stripped, stripped.split(":")[-1], stripped.split("(")[0]]
    return dedupe(variants + split_variants(stripped, (" | ", " / ", " - ", " – ")))


def division_target_variants(label: str) -> list[str]:
    """A ward's label, and each side of an "English | Welsh" label."""
    label = collapse_whitespace(label)
    return dedupe([label, *split_variants(label, (" | ",))])


# --- UK political parties ----------------------------------------------------


def party_clean(text: str) -> str:
    """basic_clean, and drop a leading 'the' and a trailing 'party'."""
    text = re.sub(r"^the ", "", basic_clean(text))
    return re.sub(r" party$", "", text)


# Bracketed text that qualifies a name rather than abbreviating it.
NOT_ABBREVIATIONS = {"GB", "UK", "NI"}


def party_variants(text: str) -> list[str]:
    """Other forms of a party name.

    Handles e.g. "Scottish National Party (SNP)" (the name before the brackets),
    "Welsh Labour / Llafur Cymru" and "Welsh Labour | Llafur Cymru" (English
    and Welsh), and "Plaid Cymru - The Party of Wales".
    """
    text = collapse_whitespace(text)
    variants = [text, text.split("(")[0]]
    return dedupe(variants + split_variants(text, (" | ", "|", " / ", "/", " - ", " – ")))


def party_target_variants(label: str) -> list[str]:
    """party_variants, plus an abbreviation in brackets, e.g. "UKIP" from "UK Independence Party (UKIP)".

    Only capitalised abbreviations count, so "Direct Democracy (Communist) Party"
    doesn't match "communist", and qualifiers like "(GB)" are ignored.
    """
    abbreviations = [
        a for a in re.findall(r"\(([A-Z][A-Z0-9.&-]+)\)", label) if a.replace(".", "") not in NOT_ABBREVIATIONS
    ]
    return dedupe(party_variants(label) + abbreviations)


# --- Built-in profiles -------------------------------------------------------

GENERIC = Profile(
    name="generic",
    description="Any kind of name; basic cleaning only",
    jev_instructions=(
        "The state is a name as written in an external source. It may be abbreviated, "
        "misspelled, punctuated differently, or have extra text such as a prefix, suffix or "
        "translation. Choose the option it refers to."
    ),
    clean=basic_clean,
)

UK_DIVISIONS = Profile(
    name="uk-divisions",
    description="Wards and electoral divisions in UK elections",
    jev_instructions=(
        "The state is the name of a ward or electoral division as written in an election "
        "results source or candidate list. It may be abbreviated, misspelled, punctuated "
        "differently, use 'and' instead of '&', or include extra text such as a council name, "
        "a year or a Welsh translation. Choose the ballot for that ward or division."
    ),
    clean=division_clean,
    variants=division_variants,
    target_variants=division_target_variants,
)

# Works best with parties labelled as in the Electoral Commission's register,
# as YNR (YourNextRepresentative) does, and needs a target for independent
# candidates labelled with one of the names in the "Independent" group.
UK_POLITICAL_PARTIES = Profile(
    name="uk-political-parties",
    description="UK political parties, as named on ballot papers and in results",
    jev_instructions=(
        "The state is a candidate's party affiliation as written on a ballot paper or results "
        "sheet. It may be abbreviated, misspelled, informal, or one of the party's registered "
        "ballot descriptions. Choose the party it refers to. If the state says or implies the "
        "candidate has no party (for example it is empty, 'Independent', 'no description', "
        "'none specified' or 'no party listed'), choose Independent. A named group of "
        "independents (for example 'Ashford Independents') is a party, not Independent."
    ),
    clean=party_clean,
    variants=party_variants,
    target_variants=party_target_variants,
    synonyms={
        # A statement of persons nominated (SOPN), the official list of
        # candidates, can describe independent candidates in several ways.
        "Independent": [
            "",
            "Annibynnol",
            "Independent / Annibynnol",
            "Annibynnol / Independent",
            "Independent Candidate",
            "No description",
            "No description given",
            "No party description",
            "No party listed",
            "None",
            "None specified",
            "Blank",
        ],
        # Common short names and nicknames. party_clean already ignores "The"
        # and "Party", so "The Conservative Party" is covered by "Conservative".
        "Conservative and Unionist Party": ["Conservative", "Conservatives", "Tory", "Tories"],
        "Liberal Democrats": ["Liberal Democrat", "Lib Dem", "Lib Dems", "LibDem", "LibDems"],
        "Green Party": ["Green", "Greens", "Green Party of England and Wales"],
        "Reform UK": ["Reform", "Reform Party UK"],
    },
)

PROFILES: dict[str, Profile] = {p.name: p for p in (GENERIC, UK_DIVISIONS, UK_POLITICAL_PARTIES)}


def get_profile(profile: str | Profile) -> Profile:
    """A Profile, given one or the name of a built-in profile."""
    if isinstance(profile, Profile):
        return profile
    try:
        return PROFILES[profile]
    except KeyError:
        raise ValueError(f"Unknown profile {profile!r}; built-in profiles are {', '.join(PROFILES)}") from None
