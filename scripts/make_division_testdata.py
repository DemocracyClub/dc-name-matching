"""Generate candidate CSVs with messily-spelt ward names, for examples/divisions.

For each election, every ballot gets 1-4 candidate rows. The ward name is the
real messy name from YNR's (YourNextRepresentative's) division_matches.json where there is one, otherwise
a deterministic mutation of the official label (exact, case changes, '&'/'and',
' Ward' suffix, apostrophes, typos, merged words, ...). The expected ballot id
is included so match results can be scored.

Files are written to examples/divisions/data/: <election>.csv and, so the data
can be used without a WhoCanIVoteFor (WCIVF) database, each election's full ballot list in
<election>.ballots.csv.

Source data: scripts/data/division_matches.json (from YNR) and
scripts/data/division_ballots.csv (exported from WCIVF by benchmark_divisions.py).

Usage:
    uv run scripts/make_division_testdata.py
"""

import csv
import json
import os
import random
import re
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).parent
SOURCE_DIR = HERE / "data"
OUT_DIR = HERE.parent / "examples" / "divisions" / "data"

ELECTIONS = [
    "local.buckinghamshire.2025-05-01",
    "local.flintshire.2022-05-05",
    "local.stockport.2023-05-04",
    "local.staffordshire.2021-05-06",
    "local.copeland.2019-05-02",
]


def typo(label: str, rng: random.Random) -> str:
    """Drop or swap a letter inside a longer word."""
    words = label.split()
    i = max(range(len(words)), key=lambda j: len(words[j]))
    w = words[i]
    if len(w) < 5:
        return label.lower()
    k = rng.randrange(1, len(w) - 2)
    words[i] = w[:k] + w[k + 1] + w[k] + w[k + 2 :] if rng.random() < 0.5 else w[:k] + w[k + 1 :]
    return " ".join(words)


def merge_words(label: str, rng: random.Random) -> str:
    words = label.split()
    if len(words) < 2:
        return label.upper()
    i = rng.randrange(len(words) - 1)
    return " ".join(words[:i] + [words[i] + words[i + 1].lower()] + words[i + 2 :])


MUTATIONS = {
    "exact": lambda s, r: s,
    "lower": lambda s, r: s.lower(),
    "upper": lambda s, r: s.upper(),
    "and": lambda s, r: s.replace(" & ", " and ") if " & " in s else s.replace(" and ", " & ") or s,
    "ward suffix": lambda s, r: f"{s} Ward",
    "apostrophe": lambda s, r: s.replace("'", "’") if "'" in s else s.replace("’", "'") if "’" in s else s.lower(),
    "seats suffix": lambda s, r: f"{s} ({r.choice([2, 3])} seats)",
    "double space": lambda s, r: re.sub(r" ", "  ", s, count=1) if " " in s else s.upper(),
    "typo": typo,
    "merged words": merge_words,
}


def main() -> None:
    with open(SOURCE_DIR / "division_matches.json") as f:
        gold = json.load(f)
    messy: dict[str, str] = {}
    for key, ballot in gold.items():
        if ballot != "--deleted--":
            messy.setdefault(ballot, key.split("--", 1)[1])

    ballots = defaultdict(list)
    with open(SOURCE_DIR / "division_ballots.csv", newline="") as f:
        for row in csv.DictReader(f):
            ballots[row["election_id"]].append(row)

    os.makedirs(OUT_DIR, exist_ok=True)
    for election in ELECTIONS:
        rng = random.Random(election)
        rows = []
        for ballot in ballots[election]:
            if ballot["ballot_paper_id"] in messy:
                name, how = messy[ballot["ballot_paper_id"]], "ynr"
            else:
                how = rng.choice(list(MUTATIONS))
                name = MUTATIONS[how](ballot["label"], rng)
            for n in range(rng.randint(1, 4)):
                rows.append(
                    {
                        "candidate": f"Candidate {len(rows) + 1}",
                        "ward": name,
                        "expected_ballot": ballot["ballot_paper_id"],
                        "variant": how,
                    }
                )
        rng.shuffle(rows)
        path = OUT_DIR / f"{election}.csv"
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["candidate", "ward", "expected_ballot", "variant"])
            writer.writeheader()
            writer.writerows(rows)
        ballots_path = OUT_DIR / f"{election}.ballots.csv"
        with open(ballots_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["ballot_paper_id", "label", "cancelled"], extrasaction="ignore")
            writer.writeheader()
            writer.writerows(ballots[election])
        print(f"Wrote {path}: {len(ballots[election])} wards, {len(rows)} rows (+ {ballots_path})")


if __name__ == "__main__":
    main()
