"""Benchmark Jev division matching against manual matches from YNR's resultsbot.

YNR is YourNextRepresentative, and WCIVF is WhoCanIVoteFor.

Source: https://github.com/DemocracyClub/yournextrepresentative/blob/master/ynr/apps/resultsbot/division_matches.json

Each key is "<election id>--<division name as written>" and each value is the
ballot paper id it was matched to, or "--deleted--" (treated as expecting
"no match"). For each entry, Jev chooses between every ballot in that election
(from WCIVF) plus a "no match" option.

Ballots are exported from WCIVF once to scripts/data/division_ballots.csv.
Results are cached in scripts/results/results_divisions[_<tag>].jsonl. Jev
needs an API key in DEFAPI_API_KEY, which can be set in a .env file.

Usage:
    uv run scripts/benchmark_divisions.py
    uv run scripts/benchmark_divisions.py --limit 10 --dry-run
    uv run scripts/benchmark_divisions.py --report-only
"""

import argparse
import csv
import json
import os
import subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from rapidfuzz import fuzz, process

from dc_name_matching.jev import api_key_from_env, build_request, choice, send

HERE = Path(__file__).parent
GOLD_PATH = HERE / "data" / "division_matches.json"
BALLOTS_PATH = HERE / "data" / "division_ballots.csv"
RESULTS_DIR = HERE / "results"
DELETED = "--deleted--"
NO_MATCH = "no_match"

INSTRUCTIONS = (
    "The state is the name of a ward or electoral division as written in an election "
    "results source. It may be abbreviated, misspelled, punctuated differently or use "
    "'and' instead of '&'. Choose the ballot for that ward or division."
)


def load_gold() -> list[tuple[str, str, str]]:
    """Return (election_id, division name, expected ballot id or NO_MATCH) tuples."""
    with open(GOLD_PATH) as f:
        gold = json.load(f)
    items = []
    for key, ballot in gold.items():
        election, name = key.split("--", 1)
        items.append((election, name, NO_MATCH if ballot == DELETED else ballot))
    return items


def export_ballots(elections: set[str], db: str = "wcivf") -> None:
    slugs = ", ".join(f"'{e}'" for e in sorted(elections))
    query = f"""
        SELECT e.slug AS election_id, pe.ballot_paper_id, p.label, pe.cancelled
        FROM elections_postelection pe
        JOIN elections_election e ON e.id = pe.election_id
        JOIN elections_post p ON p.ynr_id = pe.post_id
        WHERE e.slug IN ({slugs})
        ORDER BY e.slug, p.label, pe.ballot_paper_id
    """
    query = " ".join(query.split())
    with open(BALLOTS_PATH, "w") as f:
        subprocess.run(["psql", "-d", db, "-c", f"\\copy ({query}) TO STDOUT WITH CSV HEADER"], stdout=f, check=True)


def load_ballots() -> dict[str, list[dict]]:
    by_election = defaultdict(list)
    with open(BALLOTS_PATH, newline="") as f:
        for row in csv.DictReader(f):
            by_election[row["election_id"]].append(row)
    return by_election


def describe(ballot: dict) -> str:
    text = ballot["label"]
    if ".by." in ballot["ballot_paper_id"]:
        text += " (by-election)"
    if ballot["cancelled"] == "t":
        text += " [cancelled]"
    return text


def build_body(name: str, ballots: list[dict]) -> dict:
    options = {b["ballot_paper_id"]: describe(b) for b in ballots}
    options[NO_MATCH] = "None of the listed wards or divisions is a plausible match"
    return build_request(state=name, questions={"division": choice(INSTRUCTIONS, options)})


def run_one(election: str, name: str, expected: str, ballots: list[dict]) -> dict:
    record = {
        "election": election,
        "name": name,
        "expected": expected,
        "n_options": len(ballots) + 1,
    }
    try:
        response = send(build_body(name, ballots), api_key_from_env())
        answer = response["answers"]["division"]
        record |= {
            "predicted": answer["choice"],
            "confidence": answer.get("confidence"),
            "p_expected": answer.get("probabilities", {}).get(expected),
            "usage": response.get("usage"),
            "consumed": response.get("consumed"),
        }
    except Exception as e:  # noqa: BLE001 - record and carry on
        record["error"] = str(e)
    return record


def fuzzy_baseline(items: list[tuple[str, str, str]], ballots: dict[str, list[dict]]) -> float:
    """Top-1 fuzzy match on labels (never predicts no match)."""
    correct = 0
    for election, name, expected in items:
        choices = {b["ballot_paper_id"]: b["label"] for b in ballots[election]}
        _, _, key = process.extractOne(name, choices, scorer=fuzz.WRatio)
        correct += key == expected
    return correct / len(items)


def report(results: list[dict], baseline: float) -> None:
    ok = [r for r in results if "error" not in r]
    correct = [r for r in ok if r["predicted"] == r["expected"]]
    wrong = [r for r in ok if r["predicted"] != r["expected"]]
    deleted = [r for r in ok if r["expected"] == NO_MATCH]
    cost = sum(float(r["consumed"] or 0) for r in ok)

    def pct(n: int, d: int) -> str:
        return f"{n}/{d} ({100 * n / d:.1f}%)" if d else "n/a"

    print(f"Results:            {len(results)} ({len(results) - len(ok)} errors)")
    print(f"Accuracy:           {pct(len(correct), len(ok))}")
    print(f"  expected a ballot:   {pct(sum(r in correct for r in ok if r['expected'] != NO_MATCH), len(ok) - len(deleted))}")
    print(f"  expected no match:   {pct(sum(r in correct for r in deleted), len(deleted))}")
    print(f"Fuzzy top-1 baseline: {100 * baseline:.1f}%")
    print(f"Mean options:       {sum(r['n_options'] for r in ok) / max(len(ok), 1):.0f}")
    print(f"Total cost:         ${cost:.5f}")

    print("\nConfidence   n   accuracy")
    for lo, hi in ((0.9, 1.01), (0.7, 0.9), (0.5, 0.7), (0, 0.5)):
        band = [r for r in ok if r["confidence"] is not None and lo <= r["confidence"] < hi]
        if band:
            acc = sum(r["predicted"] == r["expected"] for r in band) / len(band)
            print(f"  {lo:.1f}-{min(hi, 1):.1f}  {len(band):4}   {100 * acc:.1f}%")

    print(f"\nMisses ({len(wrong)}):")
    for r in sorted(wrong, key=lambda r: -(r["confidence"] or 0)):
        print(
            f"  {r['name']!r}  [{r['election']}]\n      expected {r['expected']}"
            f"\n      got      {r['predicted']} conf={r['confidence']}"
        )
    for r in results:
        if "error" in r:
            print(f"  ERROR {r['name']!r} [{r['election']}]: {r['error']}")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tag", help="suffix for the results file, e.g. v2")
    parser.add_argument("--limit", type=int, help="only run the first N inputs")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--dry-run", action="store_true", help="print the first request instead of calling the API")
    parser.add_argument("--report-only", action="store_true", help="don't call the API, just report cached results")
    args = parser.parse_args()

    items = load_gold()
    if not os.path.exists(BALLOTS_PATH):
        export_ballots({election for election, _, _ in items})
    ballots = load_ballots()
    missing = {e for e, _, _ in items if e not in ballots}
    missing |= {x for e, _, x in items if x != NO_MATCH and x not in {b["ballot_paper_id"] for b in ballots[e]}}
    if missing:
        raise SystemExit(f"not found in {BALLOTS_PATH}: {missing}")

    items = items[: args.limit]
    if args.dry_run:
        election, name, _ = items[0]
        print(json.dumps(build_body(name, ballots[election]), indent=2))
        return

    out_path = RESULTS_DIR / f"results_divisions{f'_{args.tag}' if args.tag else ''}.jsonl"
    key = lambda election, name: f"{election}--{name}"  # noqa: E731
    cached = {}
    if os.path.exists(out_path):
        with open(out_path) as f:
            cached = {key(r["election"], r["name"]): r for r in map(json.loads, f) if "error" not in r}

    todo = [item for item in items if key(*item[:2]) not in cached]
    if todo and not args.report_only:
        print(f"Calling Jev for {len(todo)} inputs ({len(items) - len(todo)} cached)...")
        with ThreadPoolExecutor(args.workers) as pool, open(out_path, "a") as out:
            futures = [pool.submit(run_one, e, n, x, ballots[e]) for e, n, x in todo]
            for i, future in enumerate(as_completed(futures), 1):
                record = future.result()
                cached[key(record["election"], record["name"])] = record
                out.write(json.dumps(record) + "\n")
                out.flush()
                if i % 25 == 0:
                    print(f"  {i}/{len(todo)}")

    results = [cached[key(e, n)] for e, n, _ in items if key(e, n) in cached]
    report(results, fuzzy_baseline(items, ballots))


if __name__ == "__main__":
    main()
