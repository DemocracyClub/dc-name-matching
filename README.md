# dc-name-matching

Match two lists of names that refer to the same things but are spelt differently.

You have a list of things you know, each with an ID, like the wards in a council
election. Someone sends you a spreadsheet naming those wards (all of them, or
some of them) in their own way: in capitals, with "and" instead of "&", with
typos, with a Welsh translation attached. `dc-name-matching` pairs each of
their names with one of your records. It tries cheap, predictable rules first,
then an AI model ([Jev](#using-an-ai-model-jevmatch)), and finally asks a
person about anything still unclear, either at the console or through a web
form.

> **Status:** early development. The API may change and the package isn't on
> PyPI yet.

## The problem

Say these are the wards you know about, each with an ID:

| ID | Name |
|---|---|
| `local.stroud.abbey.2026-05-07` | Abbey |
| `local.stroud.st-marys.2026-05-07` | St Mary's |
| `local.stroud.westwood-jacksdale.2026-05-07` | Westwood & Jacksdale |
| `local.stroud.hucknall-north.2026-05-07` | Hucknall North |

And this is the column of ward names in the spreadsheet you've been sent:

```
ABBEY
st marys ward
Westwood and Jacksdale
Hucknal Nrth
```

Each of those names refers to exactly one ward, and each ward is named exactly
once, but no two strings are equal. `dc-name-matching` works out the pairs.

## How it works

You give a **matcher** two lists:

- **inputs:** the messy names, e.g. a column from a spreadsheet;
- **targets:** the records you know, each with an ID and a clean label.

Then you run it with a list of **backends**. Each backend is a different way of
deciding whether an input and a target are the same thing: comparing the
strings exactly, comparing them after tidying them up, asking an AI model, or
asking a person.

The backends run in order. Each one looks at the inputs and targets that are
still unmatched, and any pairs it's sure about are taken out before the next
backend runs. So you put cheap, exact backends first and expensive or
uncertain ones (an AI model, a person) last. The later ones only see what's
left, and have fewer options to choose between.

The matcher keeps its matches, so you can run it again later with more
backends, or save it and pick it up again in another process.

### Kinds of matcher

There are three kinds of matcher, for three ways the lists can relate:

- **`SymmetricMatcher`:** the inputs and the targets are the same set of
  things. Every input matches a target and every target is matched. Use this
  when, say, a spreadsheet lists candidates for every ward in an election.
- **`AsymmetricMatcher`:** every input is one of the targets, but there may be
  more targets than inputs, and the extra targets are left unmatched. Use this
  when, say, a spreadsheet only covers the wards being contested.
- **`ManyToOneMatcher`:** several different inputs can refer to the same
  target. Use this when, say, matching the parties in a list of candidates,
  where some Labour candidates are listed as "Labour Party" and others as
  "Welsh Labour".

The first two are **one-to-one**: each target can only be matched to one
input, and once it's matched, it's no longer an option for the others. That
rules options out as matching goes on, which helps later backends and a person
choosing what's left. A `ManyToOneMatcher` keeps every target available for
every input.

When you create a symmetric or asymmetric matcher, it checks that the counts
make sense, once duplicate inputs are removed. A `SymmetricMatcher` needs
exactly as many inputs as targets. An `AsymmetricMatcher` needs at least as
many targets as inputs, since there can't be more distinct inputs than there
are targets to match them to. If the counts don't fit, it raises `ValueError`.

Real data doesn't always follow these rules. A scraped list might still name a
ward from old boundaries, or spell one ward two ways. To match what you can
and leave the rest, create an `AsymmetricMatcher` with `strict=False`. It then
accepts more inputs than targets, and the extra inputs stay in
`unmatched_inputs` instead of stopping the whole run:

```python
matcher = AsymmetricMatcher(names, wards, profile="uk-divisions", strict=False)
```

A `ManyToOneMatcher` doesn't check the counts: any number of inputs can match
any number of targets.

### Other names: aliases

Some things are known by more than one name: a ward might have an English and
a Welsh name, and a political party has registered descriptions it can use on
ballot papers instead of its name. Give a target its other names as `aliases`,
and inputs are matched against them as well as its label:

```python
Target("local.flintshire.broughton-south.2022-05-05", "Broughton South", aliases=["De Brychdyn"])
Target("party:905", "Newcastle Independents", aliases=["Local Community Candidate"])
```

A target's aliases are also shown to Jev and to people choosing a match.

If two targets share an alias, an input with that name could be either, so the
rules don't match it. For parties, this means you should leave out
descriptions registered jointly by two parties, such as "Labour and
Co-operative Party (Joint Description with Co-operative Party)". Otherwise the
Labour Party and the Co-operative Party would share it, and neither would be
matched.

### Profiles

Different kinds of names need different rules. Ward names often come with
"Ward" on the end or a Welsh translation attached; party names come with
"The" on the front, "Party" on the end, or an abbreviation in brackets. A
**profile** tunes the backends for one kind of name: how names are tidied up
before they're compared, and what an AI model is told about them.

Set it when you create the matcher, and it applies to every backend it runs:

```python
matcher = SymmetricMatcher(names, wards, profile="uk-divisions")
matcher = AsymmetricMatcher(names, parties, profile="uk-political-parties")
```

There are three built-in profiles:

| Profile | For |
|---|---|
| `generic` (the default) | any kind of name; basic tidying up only |
| `uk-divisions` | wards and electoral divisions in UK elections |
| `uk-political-parties` | UK political parties, as named on ballot papers and in results ([see below](#party-names)) |

You can also [write your own](#writing-your-own-profile).

#### Synonyms

A profile can also have **synonyms**: groups of known names for the same
thing, each with a main name. The `uk-political-parties` profile has groups
for common short names and nicknames, such as "Tory" and "Lib Dems", and for
the ways a statement of persons nominated (a council's official list of
candidates) describes independent candidates, such as "Annibynnol", "No
description" or nothing at all:

```python
synonyms = {
    "Conservative and Unionist Party": ["Conservative", "Conservatives", "Tory", "Tories"],
    "Independent": ["", "Annibynnol", "No description", ...],
    ...
}
```

Synonyms are used in two ways:

- **Inputs are combined.** Before duplicates are removed and the counts are
  checked, an input with any name in a group becomes the group's main name. So
  different ways of writing the same thing become one input:

  ```python
  names = ["INDEPENDENT", "Annibynnol", "", "Labour Party"]
  matcher = SymmetricMatcher(names, [independent, labour], profile="uk-political-parties")
  matcher.unmatched_inputs     # ["Independent", "Labour Party"]
  ```

- **Targets match every name in their group.** If a target's label or one of
  its aliases is in a group, `CleanedMatch` matches it to every name in that
  group. So "Tory" matches a party labelled "Conservative and Unionist Party",
  "Conservative Party" or "Conservatives", whichever your data uses.
  `ExactMatch` and `CaseInsensitiveMatch` don't use synonyms, so include
  `CleanedMatch` in your backends.

Names are compared after the profile
[tidies them up](#what-cleanedmatch-tidies-up), so "TORIES" and "Tories." are
the same, and with the `uk-political-parties` profile so are "Conservative"
and "The Conservative Party". Use synonyms for well-known alternative names
that apply to every list of that kind. A name that only one target has, such
as a party's registered ballot description, belongs in that target's
`aliases`.

#### Party names

The `uk-political-parties` profile works best with parties labelled with their
names in the [Electoral Commission's register](https://search.electoralcommission.org.uk/),
as they are in [YNR](https://candidates.democracyclub.org.uk/) (YourNextRepresentative),
e.g. "Conservative and Unionist Party". Labels that use one of the profile's
other names for a party, such as "Conservative Party", work too.

Use a `ManyToOneMatcher` for parties, since one list of candidates often
names the same party in several ways. There are three ways to include a
party's registered ballot descriptions:

- **As aliases of the party**, if you only need to know the party. An input
  that matches a description is matched to its party.
- **With a [`TwoStageMatcher`](#two-stage-matching-parents-then-children)**,
  if you need to know which description was used. It matches the party first,
  then chooses between that party's own descriptions.
- **As targets of their own**, e.g. `Target("PP53__123", "Welsh Labour")`, if
  you need to know which description was used and you're only using the rules.
  A `Match` tells you the target, but not which of its names matched, so this
  is the only way to find the description in one stage.

Check the number of targets if you'll use Jev. It can only choose between
[254 targets at a time](#using-an-ai-model-jevmatch), and a whole party
register is already more than that, before any descriptions are added as
targets. Narrow the list first, e.g. to the parties standing in that election,
or leave Jev out and let a person decide what the rules can't.

Independent candidates need a target of their own, labelled "Independent" or
another name in that group (e.g. "Independent Candidate"). That's the target
the independent synonyms point to, and what Jev is told to choose when a
candidate has no party.

The groups only include names that can only mean one party. For example,
"Green" is in the Green Party's group, so it never matches the Scottish Green
Party or the Green Party in Northern Ireland, which are separate parties. An
input of "Green" in a Scottish election is left for Jev or a person to decide.

### Rules

These rules apply to every kind of matcher:

- **One target per input.** Each input is matched to at most one target. If
  the rule-based backends find an input could equally be two targets, it's
  left for a later backend, with both targets as *suggestions*.
- **No match.** A person can decide that an input has no matching target.

The one-to-one matchers, `SymmetricMatcher` and `AsymmetricMatcher`, also
follow these rules. A `ManyToOneMatcher` doesn't.

- **One input per target.** Each target can be matched to only one input. If
  a backend pairs two inputs with the same target in one go, neither pair is
  accepted, unless one match is stronger (see below). Both inputs go on to the
  next backend, with that target as a suggestion.
- **Closer matches win.** The rule-based backends (`ExactMatch`,
  `CaseInsensitiveMatch` and `CleanedMatch`) prefer a match on a whole
  name to one that needed part of a name, such as the text after a colon. If
  two inputs match the same target and one matched on its whole name, that one
  wins, whichever order the inputs are in. Otherwise a partial match could
  block a better one.
- **The last one left.** Every input belongs to one of the targets, so when
  one input and one target are left, they must belong together. They're paired
  without asking a backend, and marked `last-remaining`. When an
  `AsymmetricMatcher` has extra targets, there are always more targets left
  than inputs, so this doesn't happen. Once a person has decided an input has
  no match, an input has been used up without a target, so the last input
  left might not belong to the last target left, and this rule stops
  applying.

A backend that can't decide can leave suggestions for an input, such as an AI
model's best guess when it isn't confident enough. These are shown first when
a person is asked to choose.

## Installation

The package isn't on PyPI yet. To use it in another project, install it from
a copy of the source:

```sh
uv add path/to/dc-name-matching          # or: pip install path/to/dc-name-matching
```

It needs Python 3.11 or later. Its only dependency is
[httpx](https://www.python-httpx.org/), used to call Jev.

## Quick start

```python
from dc_name_matching import CaseInsensitiveMatch, CleanedMatch, ExactMatch, SymmetricMatcher, Target

wards = [
    Target("local.stroud.abbey.2026-05-07", "Abbey"),
    Target("local.stroud.st-marys.2026-05-07", "St Mary's"),
    Target("local.stroud.westwood-jacksdale.2026-05-07", "Westwood & Jacksdale"),
    Target("local.stroud.hucknall-north.2026-05-07", "Hucknall North"),
]
names = ["ABBEY", "st marys ward", "Westwood and Jacksdale", "Hucknal Nrth"]

matcher = SymmetricMatcher(names, wards, profile="uk-divisions")
matcher.run([ExactMatch(), CaseInsensitiveMatch(), CleanedMatch()])

for name, match in matcher.matches.items():
    print(f"{name!r:26} -> {match.target.label:22} ({match.backend})")
```

```
'ABBEY'                    -> Abbey                  (case-insensitive)
'st marys ward'            -> St Mary's              (cleaned)
'Westwood and Jacksdale'   -> Westwood & Jacksdale   (cleaned)
'Hucknal Nrth'             -> Hucknall North         (last-remaining)
```

None of the three rule-based backends can explain the typo in "Hucknal Nrth".
But once the other three were matched, it was the only name left and "Hucknall
North" was the only ward left, so they were paired.

If the spreadsheet only named some of the wards, you'd use an
`AsymmetricMatcher` in the same way. The wards it didn't name would be left in
`matcher.unmatched_targets`.

Inputs can contain duplicates, e.g. when every candidate's row in a spreadsheet
names their ward. Each distinct name is matched once, so you can pass a whole
column and look up each row's match with `matcher.match_for(row_value)`. That
also works when the profile's synonyms (see above) have combined several
spellings into one name.

## Built-in backends

| Backend | Matches when… | Example |
|---|---|---|
| `ExactMatch()` | the input is identical to a target's label or an alias | `Abbey` → Abbey |
| `CaseInsensitiveMatch()` | they're equal ignoring case and repeated spaces | `HUCKNALL  north` → Hucknall North |
| `CleanedMatch()` | they're equal after tidying up, using the profile's rules (see below) | `[from may 2025] St. Mary’s Ward` → St Mary's |
| `JevMatch()` | an AI model picks the target with enough confidence | `Utotxeter Town` → Uttoxeter Town |
| `ConsoleMatch()` | a person picks the target at the console | |
| `ManualMatch(decisions)` | a person has already picked, e.g. in a web form | |

`ExactMatch`, `CaseInsensitiveMatch` and `CleanedMatch` compare inputs with
each target's label and aliases, and only match when one target fits best.
Matching a whole name counts for more than matching part of one: if an input's
whole name fits one target and part of it fits another, it's matched to the
first. If an input could equally be two targets, it's left for a later
backend.

### What `CleanedMatch` tidies up

With every profile, both sides are lower-cased, and accents, apostrophes and
punctuation are removed, and `&` is treated as `and`. So `St. Mary’s`,
`st marys` and `St Mary's` all compare equal. That's all the `generic` profile
does.

The `uk-divisions` profile also drops a trailing "ward", "division" or
"electoral division", and tries the parts of an input that often wrap the real
name:

| Input | Also tried as |
|---|---|
| `[from may 2025] abbey` | `abbey` |
| `Abbey (2 seats)` | `Abbey` |
| `bramhall north - 2023` | `bramhall north` |
| `cannock chase - chadsmoor` | `cannock chase`, `chadsmoor` |
| `tewkesbury: churchdown` | `churchdown` |
| `bagillt / bagillt` or `bagillt \| bagillt` (English and Welsh) | each side |

The `uk-political-parties` profile ignores a leading "The" and a trailing
"Party", matches the profile's [synonyms](#synonyms), such as "Tory" for the
Conservatives, and tries other forms of both inputs and party names:

| Name | Also tried as |
|---|---|
| `Scottish National Party (SNP)` | `Scottish National Party`, and `SNP` if it's a party's name |
| `Welsh Labour / Llafur Cymru` or `Welsh Labour \| Llafur Cymru` (English and Welsh) | each side |
| `Plaid Cymru - The Party of Wales` | each side |

Only capitalised abbreviations in brackets count, and not country codes like
"(GB)", so "communist" doesn't match "Direct Democracy (Communist) Party".

## Using an AI model: `JevMatch`

`JevMatch` asks [Jev](https://defapi.org/api/model/en/typesafe/jev-1.13), a
model that answers multiple-choice questions, to pick the target each input
refers to. It's given the remaining targets as options, plus "none of these".

```python
from dc_name_matching import CleanedMatch, ExactMatch, JevMatch, SymmetricMatcher

jev = JevMatch(threshold=0.9)
matcher = SymmetricMatcher(names, wards, profile="uk-divisions").run([ExactMatch(), CleanedMatch(), jev])
print(f"Jev cost ${jev.cost:.5f}")
```

- **API key:** set the `DEFAPI_API_KEY` environment variable, or pass
  `JevMatch(api_key=...)`. If there's no key, running the backend raises
  `JevError`.
- **Confidence threshold:** Jev's answer is only accepted if its confidence is
  at least `threshold` (0.9 by default). Below that, the input stays unmatched
  and Jev's top picks are saved as suggestions for a person.
- **Instructions:** Jev is told what kind of names it's looking at by the
  matcher's profile. The `uk-political-parties` profile, for example, tells it
  that names may be a party's registered ballot description, and that "no
  party" means Independent. To use your own instructions instead, pass them to
  the backend. Jev calls the input "the state":

  ```python
  JevMatch(
      instructions="The state is the name of a ward as written in a council's candidate list. "
      "It may be misspelled or include a Welsh translation. Choose the ward it refers to."
  )
  ```
- **More context:** each option shows Jev the target's label, its aliases and
  its `description`.
- **Limits:** Jev accepts at most 255 options, one of which is "none of
  these", so there can be at most 254 targets left when it runs. With more,
  `JevMatch` raises `ValueError`, which stops the run. In one-to-one
  matching, earlier backends take away the targets they match, but that
  doesn't help much when there are far more targets than inputs. In
  many-to-one matching, targets are never taken away. So narrow long lists
  before matching, such as a whole party register or every UK parliamentary
  constituency, or don't use Jev for them. For a party's descriptions, a
  [`TwoStageMatcher`](#two-stage-matching-parents-then-children) keeps each
  question small.
- **Speed and cost:** Jev makes one API call per unmatched input, four at a
  time by default (`workers=4`). With a few dozen short options, each call
  costs a small fraction of a cent. `jev.cost` adds up what's been spent.
- **Errors:** connection errors and server errors are retried. If a call
  still fails, a warning is logged (on the `dc_name_matching.jev` logger) and
  the input is passed on unmatched, rather than stopping the run.

## Asking a person

Whatever the automatic backends can't settle needs a person. There are two
ways to ask one.

### At the console: `ConsoleMatch`

Add `ConsoleMatch()` as the last backend. For each remaining input, it lists
the remaining targets, with any suggestions first, and asks for a number:

```
[1/3] No automatic match for: 'Hillside'
Suggestions:
     2  Hillside South  (jev, 0.55)
All remaining options:
     1  Hillside North
     2  Hillside South
     3  Riverside
Pick a number, (n)o match, (s)kip, (q)uit:
```

- **Suggestions:** each one shows the backend that made it and, if it has one,
  its score. Above, Jev thought "Hillside South" was 55% likely.
- **Answers:** type a number to pick that target, `n` if the input has no
  match, `s` to skip it, or `q` to stop asking. Skipped inputs stay unmatched.
- **Last one left:** in one-to-one matching, a target you've picked isn't
  offered again, and when one input and one target are left, they're paired
  without asking. When an `AsymmetricMatcher` has extra targets, every input
  is asked about. In a `ManyToOneMatcher`, every target is offered every time.
- **Outside a terminal:** `ConsoleMatch` only asks when standard input is an
  interactive terminal. Under a web server, a scheduled job or a pipe it does
  nothing, and the inputs stay unmatched. So it's safe to leave in a list of
  backends that sometimes runs unattended. `ConsoleMatch().interactive` tells
  you whether it will ask.

### In a web app: automatic, then manual

A web request can't stop and wait for someone to type. Instead, split the work
into two requests:

1. **Automatic stage:** run the automatic backends, then save the matcher with
   `to_dict()`.
2. **Manual stage:** show a form for the inputs that are left. When it's
   submitted, restore the matcher with `from_partial()` and run it with a
   `ManualMatch` of the person's choices.

`to_dict()` returns plain, JSON-serialisable data, so it can be kept in a
session or a database between requests. It refers to targets by ID only, so it
stays small however many targets there are. Pass the targets back to
`from_partial()`, usually by loading them again from wherever they came from:

```python
import json

from dc_name_matching import CaseInsensitiveMatch, CleanedMatch, ExactMatch, ManualMatch, SymmetricMatcher, Target

wards = [
    Target("local.demo.abbey.2026-05-07", "Abbey"),
    Target("local.demo.hillside-north.2026-05-07", "Hillside North"),
    Target("local.demo.hillside-south.2026-05-07", "Hillside South"),
    Target("local.demo.riverside.2026-05-07", "Riverside"),
]
names = ["ABBEY", "Hillside", "Hillside (2)", "Ward 6"]

# Request 1: match what can be matched automatically (add JevMatch() here too), and save the matcher.
matcher = SymmetricMatcher(names, wards, profile="uk-divisions")
matcher.run([ExactMatch(), CaseInsensitiveMatch(), CleanedMatch()])
saved = json.dumps(matcher.to_dict())  # store in a session or database

# Show a form: matcher.matches to review, and a dropdown for each unmatched input.
for name in matcher.unmatched_inputs:
    options = matcher.choices_for(name)  # remaining targets, suggestions first
    ...

# Request 2: the person has chosen. Map each input to a target ID, or to None for "no match".
decisions = {"Hillside": "local.demo.hillside-north.2026-05-07"}
matcher = SymmetricMatcher.from_partial(json.loads(saved), wards)
matcher.run([ManualMatch(decisions)])

if matcher.is_complete:
    ...  # save the matches
else:
    ...  # save matcher.to_dict() again and show the form for what's left
```

- **Partial answers:** people can answer some inputs now and the rest later.
  Leave an input out of `decisions` to keep it unmatched.
- **The last one left:** with a `SymmetricMatcher`, if a person decides all
  but one input, the last input is paired with the last target automatically.
- **Invalid choices:** `ManualMatch` raises `ValueError`, and nothing is
  applied, if an input isn't waiting for a match, a target isn't available
  (it's already matched, or the ID is wrong), or, in one-to-one matching, the
  same target is chosen twice. Show the error and the form again.
- **Restoring the right kind:** restore with the same class you saved from.
  `from_partial()` raises `ValueError` if the data came from the other kind of
  matcher. The saved data's `"mode"` is `"symmetric"` or `"asymmetric"`, if you
  need to tell them apart.
- **Targets:** the targets you pass to `from_partial()` must include every
  target the saved data refers to; extra targets are fine. If any are missing,
  it raises `ValueError` and lists their IDs.
- **Profiles:** the saved data records the profile's name, and `from_partial()`
  uses the same built-in profile again. A profile of your own can't be saved as
  data, so pass it back in: `from_partial(data, targets, profile=my_profile)`.
- **Slow backends:** `JevMatch` can take a while with many inputs, so in a web
  app you may want to run the automatic stage as a background task.

## In an import job

Matching often runs as part of a batch job, such as importing a council's CSV
of candidates or a scraper's output, with the results saved as data rather
than shown to someone straight away. A few things help there:

- **Choose the right kind of matcher.** For lists where several names can
  mean the same thing, such as parties, use a `ManyToOneMatcher`. For lists
  of distinct things, such as wards, use an `AsymmetricMatcher` with
  `strict=False`, so a stale or extra name is left unmatched instead of
  raising `ValueError` for the whole job.
- **Check the targets fit Jev.** If the job uses `JevMatch`, keep the target
  list within its limit of 254, or the job stops with `ValueError` when Jev
  runs.
- **Run only on what isn't settled yet.** Pass in only the names that don't
  already have a match, and every target they could match. Then a re-run
  doesn't repeat work, and can't overwrite a decision a person made.
- **Reuse decisions people made before.** Put a backend that looks up past
  decisions first, so nobody is asked the same thing twice. There's an example
  in [Writing your own backend](#writing-your-own-backend).
- **Record how each match was made.** Save `match.backend` and
  `match.confidence` with each match, so you can tell a rule's match from Jev's
  or a person's later. You may not want to save `last-remaining` matches
  automatically: they're inferred from what's left, not from the name itself.
- **Look up rows with `match_for`.** Matches are made once per distinct name,
  after synonyms are applied. `matcher.match_for(row_value)` finds the match
  for any row.
- **Leave the rest for a person.** Save `to_dict()` and show the
  [web form](#in-a-web-app-automatic-then-manual) for whatever's left, then
  carry on with the job once it's complete.

## Two-stage matching: parents, then children

Sometimes the targets come in families: a party and its registered ballot
descriptions, say. You want to know the description that was used, but there
are far too many descriptions to choose between at once, especially for Jev.
A `TwoStageMatcher` splits the question in two:

1. **Parents.** Each input is matched to a parent, e.g. a party. A parent is
   matched by its own names and by all of its children's names, so an input
   written as a description, like "Welsh Labour", still finds its party.
2. **Children.** Each input matched to a parent with children is matched
   again, against that parent and its own children only. That's usually only
   a handful of options, so it's quick for the rules and well within Jev's
   limit.

```python
from dc_name_matching import CaseInsensitiveMatch, CleanedMatch, ExactMatch, Target, TwoStageMatcher

parties = [
    Target("PP53", "Labour Party"),
    Target("PP52", "Conservative and Unionist Party"),
    Target("ynmp-party:2", "Independent"),
]
descriptions = {
    "PP53": [Target("PP53__1", "Welsh Labour", aliases=["Llafur Cymru"]), Target("PP53__2", "Labour Candidate")],
    "PP52": [Target("PP52__1", "Welsh Conservatives")],
}
names = ["Labour Party", "Welsh Labour", "LLAFUR CYMRU", "Tory", ""]

matcher = TwoStageMatcher(names, parties, descriptions, profile="uk-political-parties")
matcher.run([ExactMatch(), CaseInsensitiveMatch(), CleanedMatch()])

for name in names:
    match = matcher.match_for(name)
    print(f"{name!r:16} -> {match.parent.target.id:13} {match.child and match.child.target.id}")
```

```
'Labour Party'   -> PP53          PP53
'Welsh Labour'   -> PP53          PP53__1
'LLAFUR CYMRU'   -> PP53          PP53__1
'Tory'           -> PP52          PP52
''               -> ynmp-party:2  None
```

- **Results:** `match_for(name)` returns a `TwoStageMatch` with a `parent`
  and a `child` match. If the child is the parent itself, the input used the
  party's own name rather than one of its descriptions, as with "Labour Party"
  and "Tory" above. `child` is `None` if the parent has no children, like
  Independent above, or the second stage couldn't decide. Either way, you
  still have the parent. `matches` gives the results for every input with a
  parent.
- **Both stages are many-to-one.** Several inputs can match the same party,
  and the same description.
- **Different backends for each stage:** pass `child_backends` to use
  different ones for the second stage, e.g.
  `matcher.run(rule_backends, child_backends=[*rule_backends, JevMatch()])`
  to ask Jev about descriptions, but not about parties.
- **Running again:** like the other matchers, `run` can be called again with
  more backends. They run on what's still unmatched at both stages, and
  inputs newly matched to a parent go on to the second stage.
- **People's decisions:** apply them to one stage at a time with
  `ManualMatch`: `matcher.parents` is the first stage's `ManyToOneMatcher`, and
  `matcher.child_matcher_for(name)` is the second stage's for that input. Then
  call `matcher.run([])` to send newly matched inputs on to the second stage.
- **Children's names:** leave out any child whose name is the same as its
  parent's, e.g. a description that's just the party's name. In the second
  stage, an input with that name could equally be either, so it wouldn't be
  matched to either.

## Writing your own backend

For a rule that normalises strings before comparing them, use
`NormalisedMatch` with a function. Inputs match a target when they normalise to
the same string, and only one target fits:

```python
import re

from dc_name_matching import NormalisedMatch

# Treat "St" and "Saint" as the same.
saints = NormalisedMatch("saints", lambda s: re.sub(r"\bsaint\b", "st", s.casefold()))
```

`NormalisedMatch` also takes a `variants` function, which returns several
candidate strings for one input (e.g. with a prefix removed), and a
`target_variants` function, which does the same for a target's label and
aliases. Both must return the name itself first: a match on that counts for
more than a match on any of the other forms. The input matches if its
strongest matches all point at one target. Names that normalise to an empty
string are never matched.

For anything else, subclass `MatchBackend` and implement `match`. It receives
the unmatched inputs, the targets still available, the suggestions made so far,
the matcher's profile, and `one_to_one`, which is `False` in a
`ManyToOneMatcher`. It returns a `Match` for each input it's sure about, and
can add `Suggestion`s for the rest.

Most backends can ignore `one_to_one`: the matcher rejects clashing matches in
one-to-one matching itself. It matters to backends that make several decisions
in one go and need to know whether a target they've picked can be picked
again, like `ConsoleMatch`.

For example, this backend reuses decisions people made before, e.g. saved
from an earlier run's `ManualMatch` or `ConsoleMatch`. Unlike `ManualMatch`,
it doesn't raise when a decision no longer applies; it just skips it:

```python
from dc_name_matching import Match, MatchBackend


class LookupMatch(MatchBackend):
    """Match inputs using names people have matched before."""

    name = "lookup"

    def __init__(self, known: dict[str, str | None]):
        self.known = known  # input -> target ID, or None for "no match"

    def match(self, inputs, targets, suggestions, profile, *, one_to_one):
        by_id = {t.id: t for t in targets}
        matches = []
        for text in inputs:
            if text not in self.known:
                continue
            target_id = self.known[text]
            if target_id is None:
                matches.append(Match(text, None, self.name))
            elif target_id in by_id:
                matches.append(Match(text, by_id[target_id], self.name))
        return matches
```

The matcher takes care of the rest: only unmatched inputs and targets are
passed in, conflicting matches are rejected, and matches to inputs or targets
that aren't available are ignored. If your backend raises an exception,
nothing it did is kept, including any suggestions it added.

## Writing your own profile

A `Profile` has a name, a description, instructions for Jev, and the functions
`CleanedMatch` uses:

- `clean`: normalises a name before comparing, e.g. `str.casefold`;
- `variants` (optional): returns other forms of an input to try, starting with
  the input itself;
- `target_variants` (optional): returns other forms of a target's label to try,
  starting with the label itself.

And `synonyms` (optional), groups of known names for the same thing, as a dict
of each group's main name to its other names, e.g.
`{"London King's Cross": ["King's Cross", "KX"]}`. A name can only be in one
group; otherwise creating the profile raises `ValueError`.

The simplest way is to start from a built-in profile and change what you need:

```python
import dataclasses

from dc_name_matching import PROFILES, SymmetricMatcher

stations = dataclasses.replace(
    PROFILES["generic"],
    name="uk-stations",
    description="UK railway stations",
    jev_instructions="The state is the name of a UK railway station. Choose the station it refers to.",
)
matcher = SymmetricMatcher(names, stations_list, profile=stations)
```

Add it to `PROFILES` to use it by name, e.g. `profile="uk-stations"`. Then
`from_partial()` can also find it by name, and you don't need to pass it back
in.

To extend a built-in profile's rules, build on the functions it uses, from
`dc_name_matching.profiles`:

| Function | Does |
|---|---|
| `basic_clean` | lower-cases, removes accents, apostrophes and punctuation, and treats `&` as `and`; the `generic` profile's `clean` |
| `division_clean`, `division_variants`, `division_target_variants` | the `uk-divisions` profile's rules |
| `party_clean`, `party_variants`, `party_target_variants` | the `uk-political-parties` profile's rules |
| `split_variants(text, separators)` | the first and last parts of `text`, split on each separator it contains |
| `dedupe(variants)` | removes blanks and repeats, keeping the order |

For example, to also ignore a trailing "Candidate" in party names, as on many
statements of persons nominated:

```python
import dataclasses
import re

from dc_name_matching import PROFILES
from dc_name_matching.profiles import party_clean

parties = dataclasses.replace(
    PROFILES["uk-political-parties"],
    name="my-parties",
    # Remove "Candidate" first, so party_clean can then remove "Party" before it.
    clean=lambda text: party_clean(re.sub(r"\s+candidate\s*$", "", text, flags=re.I)),
)
```

The new profile keeps the original's synonyms, and compares them using its
own `clean`.

## Reference

### Matchers

`SymmetricMatcher(inputs, targets, profile="generic")`,
`AsymmetricMatcher(inputs, targets, profile="generic", *, strict=True)` and
`ManyToOneMatcher(inputs, targets, profile="generic")` raise `ValueError` if
the profile isn't known. The first two also raise it if the counts don't fit
(see above), unless an `AsymmetricMatcher` has `strict=False`. All three
have:

- `run(backends)`: run the backends in order on what's still unmatched, and
  return the matcher. If a backend raises an exception, matches made by earlier
  backends are kept and nothing from the failing backend is applied.
- `matches`: a dict mapping each matched input to its `Match`;
- `unmatched_inputs` and `unmatched_targets`: what's left. In a
  `ManyToOneMatcher`, `unmatched_targets` is always every target, since
  targets are never used up;
- `suggestions`: a dict mapping inputs to lists of `Suggestion`s;
- `is_complete`: `True` when no inputs are left (an `AsymmetricMatcher` may
  still have targets left);
- `choices_for(input)`: the remaining targets for an input, suggestions first;
- `to_dict()`: the matcher's state as plain, JSON-serialisable data;
- `profile`: the matcher's `Profile`;
- `match_for(text)`: the `Match` for an input as originally written (any
  spelling that the profile's synonyms combined), or `None`;
- `spellings`: a dict mapping each input name to the original spellings it
  came from;
- `from_partial(data, targets, profile=None)` (a class method): restore a
  matcher from `to_dict()` data, to run more backends. `targets` must include
  every target the data refers to. Pass `profile` if it was one of your own.

`TwoStageMatcher(inputs, parents, children, profile="generic")` matches each
input to a parent, then to one of that parent's children (see
[Two-stage matching](#two-stage-matching-parents-then-children)). `children`
maps a parent's ID to its child `Target`s; it raises `ValueError` if it names
a parent that isn't in `parents`. It has:

- `run(backends, child_backends=None)`: run `backends` on the first stage and
  `child_backends` (by default, the same ones) on the second, and return the
  matcher;
- `match_for(text)`: the input's `TwoStageMatch`;
- `matches`: a dict mapping each input matched to a parent to its
  `TwoStageMatch`;
- `parents`: the first stage's `ManyToOneMatcher`;
- `child_matcher_for(text)`: the second stage's `ManyToOneMatcher` for an
  input, or `None` if it hasn't reached the second stage;
- `child_matchers`: all of the second stage's matchers.

### Data

- `Target(id, label, description=None, aliases=())`: something you know
  about. Inputs are compared with `label` and with `aliases`, its other names.
  `description` is extra context shown to Jev and to people, e.g.
  `"(by-election)"`. `summary` is the label, aliases and description as one
  line of text.
- `Match(input, target, backend, confidence=None)`: a pairing. `target` is
  `None` when a person decided there's no match. `backend` is the name of the
  backend that made it, or `"last-remaining"`.
- `Suggestion(target, source, score=None)`: a possible target for an input
  that a backend couldn't settle.
- `TwoStageMatch(parent, child)`: an input's `Match` at each stage of a
  `TwoStageMatcher`. Either can be `None`.
- `Profile(name, description, jev_instructions, clean, variants, target_variants, synonyms)`:
  settings for a kind of matching (see above).
- `PROFILES`: the built-in profiles, by name. `get_profile(name)` looks one up.
- `JevError`: raised when Jev can't be used, e.g. because there's no API key.

## Development

The project is managed with [uv](https://docs.astral.sh/uv/).

```sh
uv sync                 # create .venv with the package and dev dependencies
uv run pytest           # run the tests (no API calls are made)
uv build                # build the wheel and sdist into dist/
```

The repository contains:

- `src/dc_name_matching/`: the package.
- `tests/`: unit tests, plus a test of the web example.
- `examples/divisions/`: a worked example that matches the ward names in a
  spreadsheet of election candidates to the ballots of a UK council election.
  It includes a command-line tool (`match_divisions.py`), a small Flask app
  showing the two-stage web flow (`webdemo.py`) and sample data. Run them with
  `uv run examples/divisions/match_divisions.py --help` and
  `uv run examples/divisions/webdemo.py`. They read `DEFAPI_API_KEY` from a
  `.env` file if there is one.
- `scripts/`: tools for measuring how well Jev matches ward names against a
  set of known matches, and for generating the example data.

## Limitations

- **One spelling per thing, in one-to-one matching.** A `SymmetricMatcher` or
  `AsymmetricMatcher` can only match each target to one input, so the inputs
  should spell each thing the same way throughout (the same name can appear
  many times). If a list spells one thing two ways, e.g. "Stroud Central" and
  "Stroud Central Ward", both inputs claim the same target and neither is
  matched by the rules. If that's expected, as in lists of parties, use a
  `ManyToOneMatcher`.
- **Inputs that aren't one of the targets.** The matchers are built for lists
  where every input is one of the targets. An input that isn't stays unmatched
  until a person decides it has no match. If there could be many of them, use
  an `AsymmetricMatcher` with `strict=False`, so they don't fail the count
  check.
- **Large target lists and Jev.** An `AsymmetricMatcher`'s targets can far
  outnumber its inputs, but `JevMatch` can only choose between 254 remaining
  targets at a time, and raises `ValueError` if there are more. Lists such as
  a party register (especially with its descriptions as separate targets) or
  every UK parliamentary constituency need narrowing first.
- **Matches are final.** A matcher has no way to undo a match, so a review
  form can't yet correct one.
- **Saving two-stage matching.** A `TwoStageMatcher` can't be saved with
  `to_dict()` yet. Its stages are ordinary matchers, which can.
- **Jev is the only built-in AI backend.** Other models can be added by
  subclassing `MatchBackend`.
