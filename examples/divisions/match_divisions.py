"""Match the ward/division names in a CSV to the ballots of an election.

Uses the dc_name_matching package to run a list of backends: exact, case-insensitive,
cleaned, Jev, then a person at the console (only when run in an interactive
terminal). Each only sees the names that earlier ones didn't match, and only
ballots not already matched.

By default (--mode symmetric) the CSV must name every ballot in the election,
so it has exactly as many distinct names as the election has ballots, and when
one name and one ballot are left they're matched to each other. With --mode
asymmetric the CSV may name only some of the ballots (e.g. only the contested
wards), and the others are left unmatched.

Writes the input CSV with ballot_paper_id, ballot_label, matched_by and
confidence columns added. If the CSV has an expected_ballot column (see
scripts/make_division_testdata.py), the matches are scored against it.

Jev needs an API key in DEFAPI_API_KEY, which can be set in a .env file.

Usage:
    uv run examples/divisions/match_divisions.py local.stockport.2023-05-04 candidates.csv
    uv run examples/divisions/match_divisions.py local.stockport.2023-05-04 candidates.csv --column "Ward name"

Ballots are loaded from a local copy of the WhoCanIVoteFor (WCIVF) database,
or from a CSV with --ballots-csv (columns ballot_paper_id, label, cancelled; if
there's an election_id column, only rows for the election are used). The example data in data/ comes with its own ballot lists:

    uv run examples/divisions/match_divisions.py local.stockport.2023-05-04 \
        examples/divisions/data/local.stockport.2023-05-04.csv \
        --ballots-csv examples/divisions/data/local.stockport.2023-05-04.ballots.csv --no-console

--state-json saves the matcher's state (matches, unmatched names and ballots,
and suggestions) as JSON, in the form a web app would store between requests;
see BaseMatcher.to_dict().
"""

import argparse
import csv
import json
import subprocess
import sys
from collections import Counter

from dotenv import load_dotenv

from dc_name_matching import (
    AsymmetricMatcher,
    BaseMatcher,
    CaseInsensitiveMatch,
    CleanedMatch,
    ConsoleMatch,
    ExactMatch,
    JevMatch,
    SymmetricMatcher,
    Target,
)


def load_ballots_from_db(election: str, db: str) -> list[Target]:
    query = f"""
        SELECT pe.ballot_paper_id, p.label, pe.cancelled
        FROM elections_postelection pe
        JOIN elections_election e ON e.id = pe.election_id
        JOIN elections_post p ON p.ynr_id = pe.post_id
        WHERE e.slug = '{election.replace("'", "''")}'
        ORDER BY p.label
    """
    out = subprocess.run(
        ["psql", "-d", db, "-c", f"\\copy ({' '.join(query.split())}) TO STDOUT WITH CSV HEADER"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [to_target(row) for row in csv.DictReader(out.splitlines())]


def load_ballots_from_csv(election: str, path: str) -> list[Target]:
    with open(path, newline="") as f:
        return [to_target(row) for row in csv.DictReader(f) if row.get("election_id", election) == election]


def to_target(row: dict) -> Target:
    notes = []
    if ".by." in row["ballot_paper_id"]:
        notes.append("by-election")
    if row["cancelled"] == "t":
        notes.append("cancelled")
    return Target(row["ballot_paper_id"], row["label"], f"({', '.join(notes)})" if notes else None)


def describe_matches(matcher: BaseMatcher) -> str:
    counts = Counter(m.backend for m in matcher.matches.values())
    lines = [f"  {name:<18} {n}" for name, n in counts.items()]
    lines.append(f"  {'unmatched':<18} {len(matcher.unmatched_inputs)}")
    return "\n".join(lines)


def score(rows: list[dict], column: str) -> None:
    by_name = {row[column]: row["expected_ballot"] for row in rows}
    results = {row[column]: row["ballot_paper_id"] for row in rows}
    wrong = [(n, results[n], e) for n, e in by_name.items() if results[n] and results[n] != e]
    correct = sum(results[n] == e for n, e in by_name.items())
    print(f"\nScore vs expected_ballot: {correct}/{len(by_name)} names correct, {len(wrong)} wrong")
    for name, got, expected in wrong:
        print(f"  {name!r}: got {got}, expected {expected}")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("election", help="election id, e.g. local.stockport.2023-05-04")
    parser.add_argument("csv", help="CSV with a column of ward names")
    parser.add_argument("--column", default="ward", help="column holding the ward names (default: ward)")
    parser.add_argument("--out", help="output CSV (default: <csv>.matched.csv)")
    parser.add_argument("--db", default="wcivf", help="WCIVF database to load ballots from")
    parser.add_argument("--ballots-csv", help="load ballots from a CSV export instead of the database")
    parser.add_argument("--jev-threshold", type=float, default=0.9, help="minimum Jev confidence to accept")
    parser.add_argument("--no-jev", action="store_true", help="skip the Jev matcher")
    parser.add_argument(
        "--mode", choices=["symmetric", "asymmetric"], default="symmetric",
        help="symmetric: the CSV names every ballot; asymmetric: it names some of them (default: symmetric)",
    )
    parser.add_argument("--no-console", action="store_true", help="don't ask at the console; leave the rest unmatched")
    parser.add_argument("--state-json", help="also save the matcher's state as JSON to this path")
    args = parser.parse_args()

    with open(args.csv, newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    if args.column not in fieldnames:
        sys.exit(f"No {args.column!r} column in {args.csv}; columns are {fieldnames}")

    if args.ballots_csv:
        targets = load_ballots_from_csv(args.election, args.ballots_csv)
    else:
        targets = load_ballots_from_db(args.election, args.db)
    if not targets:
        sys.exit(f"No ballots found for {args.election}")

    names = list(dict.fromkeys(row[args.column] for row in rows))
    print(f"{len(names)} distinct names in {args.csv}, {len(targets)} ballots in {args.election}")

    jev = JevMatch(threshold=args.jev_threshold)
    backends = [ExactMatch(), CaseInsensitiveMatch(), CleanedMatch()]
    if not args.no_jev:
        backends.append(jev)
    if not args.no_console:
        console = ConsoleMatch()
        if not console.interactive:
            print("Not running in an interactive terminal, so unmatched names won't be asked about.")
        backends.append(console)

    try:
        matcher_class = SymmetricMatcher if args.mode == "symmetric" else AsymmetricMatcher
        matcher = matcher_class(names, targets, profile="uk-divisions")
    except ValueError as e:
        if args.mode == "symmetric":
            sys.exit(f"{e}. The CSV should name each of the {len(targets)} ballots in {args.election} exactly once.")
        sys.exit(f"{e}. The CSV names more wards than {args.election} has ballots.")
    matcher.run(backends)

    print("\nMatched by:")
    print(describe_matches(matcher))
    if not args.no_jev:
        print(f"Jev cost: ${jev.cost:.5f}")
    if matcher.unmatched_inputs:
        print("Unmatched names:", ", ".join(repr(n) for n in matcher.unmatched_inputs))
    if matcher.unmatched_targets:
        heading = "Unmatched ballots" if args.mode == "symmetric" else "Ballots not in the CSV (or unmatched)"
        print(f"{heading}:", "; ".join(t.label for t in matcher.unmatched_targets))

    for row in rows:
        m = matcher.match_for(row[args.column])
        row |= {
            "ballot_paper_id": m.target.id if m and m.target else "",
            "ballot_label": m.target.label if m and m.target else "",
            "matched_by": m.backend if m else "",
            "confidence": m.confidence if m and m.confidence is not None else "",
        }
    out = args.out or args.csv.removesuffix(".csv") + ".matched.csv"
    with open(out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames + ["ballot_paper_id", "ballot_label", "matched_by", "confidence"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nWrote {out}")
    if args.state_json:
        with open(args.state_json, "w") as f:
            json.dump(matcher.to_dict(), f, indent=2)
        print(f"Wrote {args.state_json}")

    if "expected_ballot" in fieldnames:
        score(rows, args.column)


if __name__ == "__main__":
    main()
