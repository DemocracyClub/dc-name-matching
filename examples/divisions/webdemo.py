"""A mock web interface showing the two-stage matching flow over HTTP.

Stage 1 (POST /match): run the automatic backends (exact, case-insensitive,
cleaned, optionally Jev) on a CSV of ward names, store matcher.to_dict() and
redirect to a review page.

Stage 2 (POST /review/<id>): apply the choices a person made in the form with
ManualMatch, after restoring the matcher with from_partial, store its new state
and show the review page again, until everything is matched. There's no console
here, so a ConsoleMatch backend would do nothing.

State is kept as JSON files in webdemo_state/, standing in for a database or
session. Elections and ballots come from data/.
Jev needs an API key in DEFAPI_API_KEY, which can be set in a .env file.

Usage:
    uv run examples/divisions/webdemo.py       # then open http://127.0.0.1:5000
"""

import csv
import io
import json
import uuid
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, Response, abort, redirect, render_template_string, request, url_for

from match_divisions import load_ballots_from_csv
from dc_name_matching import (
    AsymmetricMatcher,
    BaseMatcher,
    CaseInsensitiveMatch,
    CleanedMatch,
    ExactMatch,
    JevMatch,
    ManualMatch,
    SymmetricMatcher,
)

HERE = Path(__file__).parent
TESTDATA = HERE / "data"
STATE_DIR = HERE / "webdemo_state"
NO_MATCH = "__no_match__"

app = Flask(__name__)


# --- State (stands in for a database) ----------------------------------------


def save_state(job_id: str, state: dict) -> None:
    STATE_DIR.mkdir(exist_ok=True)
    (STATE_DIR / f"{job_id}.json").write_text(json.dumps(state, indent=2))


def load_state(job_id: str) -> dict:
    path = STATE_DIR / f"{job_id}.json"
    if not job_id.isalnum() or not path.exists():
        abort(404)
    return json.loads(path.read_text())


MATCHERS = {cls.mode: cls for cls in (SymmetricMatcher, AsymmetricMatcher)}


def load_ballots(election: str):
    return load_ballots_from_csv(election, str(TESTDATA / f"{election}.ballots.csv"))


def load_matcher(state: dict) -> BaseMatcher:
    """Restore the matcher saved in `state`, as the right class for its mode.

    The saved state only has ballot IDs, so the election's ballots are loaded
    again (in a real app, from the database).
    """
    return MATCHERS[state["matcher"]["mode"]].from_partial(state["matcher"], load_ballots(state["election"]))


def elections() -> list[str]:
    return sorted(p.name.removesuffix(".ballots.csv") for p in TESTDATA.glob("*.ballots.csv"))


# --- Views -------------------------------------------------------------------


@app.get("/")
def index():
    return render_template_string(INDEX, elections=elections(), error=request.args.get("error"))


@app.post("/match")
def match():
    """Stage 1: automatic matching."""
    election = request.form["election"]
    if election not in elections():
        abort(400)
    column = request.form.get("column") or "ward"
    upload = request.files.get("csv")
    text = upload.read().decode("utf-8-sig") if upload and upload.filename else (TESTDATA / f"{election}.csv").read_text()
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if column not in (reader.fieldnames or []):
        return redirect(url_for("index", error=f"No {column!r} column; columns are {reader.fieldnames}"))

    names = [row[column] for row in rows]
    targets = load_ballots(election)
    backends = [ExactMatch(), CaseInsensitiveMatch(), CleanedMatch()]
    jev = JevMatch() if request.form.get("use_jev") else None
    if jev:
        backends.append(jev)
    matcher_class = MATCHERS.get(request.form.get("mode", "symmetric"))
    if matcher_class is None:
        abort(400)
    try:
        matcher = matcher_class(names, targets, profile="uk-divisions")
    except ValueError as e:
        return redirect(url_for("index", error=str(e)))

    matcher.run(backends)
    job_id = uuid.uuid4().hex
    save_state(
        job_id,
        {
            "election": election,
            "column": column,
            "fieldnames": reader.fieldnames,
            "rows": rows,
            "jev_cost": jev.cost if jev else None,
            "matcher": matcher.to_dict(),
        },
    )
    return redirect(url_for("review", job_id=job_id))


@app.get("/review/<job_id>")
def review(job_id: str):
    state = load_state(job_id)
    matcher = load_matcher(state)
    unmatched = []
    for text in matcher.unmatched_inputs:
        scores = {s.target.id: s for s in matcher.suggestions.get(text, [])}
        unmatched.append({"text": text, "choices": [(t, scores.get(t.id)) for t in matcher.choices_for(text)]})
    return render_template_string(
        REVIEW,
        job_id=job_id,
        state=state,
        matcher=matcher,
        unmatched=unmatched,
        rows_per_name={
            name: sum(r[state["column"]] in matcher.spellings[name] for r in state["rows"]) for name in matcher.matches
        },
        no_match=NO_MATCH,
        error=request.args.get("error"),
    )


@app.post("/review/<job_id>")
def submit_review(job_id: str):
    """Stage 2: apply the person's choices."""
    state = load_state(job_id)
    matcher = load_matcher(state)

    # Fields are numbered rather than named after the inputs, which can be any string.
    decisions = {}
    for key, value in request.form.items():
        if key.startswith("choice-") and value:
            text = request.form[f"input-{key.removeprefix('choice-')}"]
            decisions[text] = None if value == NO_MATCH else value
    try:
        matcher.run([ManualMatch(decisions)])
    except ValueError as e:
        return redirect(url_for("review", job_id=job_id, error=str(e)))

    state["matcher"] = matcher.to_dict()
    save_state(job_id, state)
    return redirect(url_for("review", job_id=job_id))


@app.get("/review/<job_id>/matched.csv")
def download(job_id: str):
    state = load_state(job_id)
    matcher = load_matcher(state)
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=state["fieldnames"] + ["ballot_paper_id", "ballot_label", "matched_by"])
    writer.writeheader()
    for row in state["rows"]:
        m = matcher.match_for(row[state["column"]])
        target = m.target if m else None
        writer.writerow(
            row | {"ballot_paper_id": target.id if target else "", "ballot_label": target.label if target else "",
                   "matched_by": m.backend if m else ""}
        )
    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={state['election']}.matched.csv"},
    )


# --- Templates ---------------------------------------------------------------

BASE_CSS = """
<style>
  :root { --fg: #1c1c1c; --muted: #666; --line: #ddd; --bg: #fff; --accent: #0b5cad; --warn: #fff4d6; --err: #fde2e2; --ok: #e3f4e6; }
  @media (prefers-color-scheme: dark) {
    :root { --fg: #e8e8e8; --muted: #9a9a9a; --line: #3a3a3a; --bg: #181818; --accent: #6aa8ff; --warn: #3a3220; --err: #4a2626; --ok: #203a26; }
  }
  body { font: 15px/1.45 system-ui, sans-serif; color: var(--fg); background: var(--bg); max-width: 60rem; margin: 2rem auto; padding: 0 1rem; }
  h1 { font-size: 1.4rem; } h2 { font-size: 1.1rem; margin-top: 2rem; }
  table { border-collapse: collapse; width: 100%; } th, td { text-align: left; padding: .35rem .5rem; border-bottom: 1px solid var(--line); vertical-align: top; }
  th { font-weight: 600; } .muted { color: var(--muted); } code { font-size: .85em; }
  .tag { font-size: .8em; padding: .05rem .4rem; border-radius: 3px; border: 1px solid var(--line); white-space: nowrap; }
  .box { padding: .6rem .8rem; border-radius: 4px; margin: 1rem 0; } .error { background: var(--err); } .warn { background: var(--warn); } .ok { background: var(--ok); }
  select { max-width: 100%; } button { font: inherit; padding: .4rem 1rem; }
  label { display: block; margin: .6rem 0 .2rem; font-weight: 600; } a { color: var(--accent); }
</style>
"""

INDEX = BASE_CSS + """
<title>Ward matching demo</title>
<h1>Ward matching demo</h1>
<p class="muted">Stage 1: automatic matching. Stage 2: a person reviews and fills in whatever is left.</p>
{% if error %}<div class="box error">{{ error }}</div>{% endif %}
<form method="post" action="{{ url_for('match') }}" enctype="multipart/form-data">
  <label for="election">Election</label>
  <select name="election" id="election">
    {% for e in elections %}<option>{{ e }}</option>{% endfor %}
  </select>
  <label for="csv">Candidates CSV <span class="muted">(optional; defaults to the test data for that election)</span></label>
  <input type="file" name="csv" id="csv" accept=".csv">
  <label for="column">Ward name column</label>
  <input name="column" id="column" value="ward">
  <label>Mode</label>
  <label style="font-weight: normal"><input type="radio" name="mode" value="symmetric" checked>
    Symmetric <span class="muted">(the CSV names every ballot in the election)</span></label>
  <label style="font-weight: normal"><input type="radio" name="mode" value="asymmetric">
    Asymmetric <span class="muted">(the CSV names some of the ballots, e.g. only contested wards)</span></label>
  <label><input type="checkbox" name="use_jev" value="1" checked> Use Jev <span class="muted">(calls the API; may take ~10-30s)</span></label>
  <p><button>Run automatic matching</button></p>
</form>
"""

REVIEW = BASE_CSS + """
<title>Review matches</title>
<p><a href="{{ url_for('index') }}">&larr; Start again</a></p>
<h1>{{ state.election }}</h1>
{% if error %}<div class="box error">{{ error }}</div>{% endif %}
{% if matcher.is_complete %}
  <div class="box ok">All {{ matcher.matches|length }} names are matched.
    {% if matcher.unmatched_targets %}{{ matcher.unmatched_targets|length }} ballots weren't in the CSV.{% endif %}
    <a href="{{ url_for('download', job_id=job_id) }}">Download the matched CSV</a></div>
{% else %}
  <div class="box warn">{{ unmatched|length }} of {{ matcher.matches|length + unmatched|length }} names need a person to choose.
    {% if state.jev_cost is not none %}<span class="muted">Jev cost ${{ '%.5f'|format(state.jev_cost) }}.</span>{% endif %}</div>

  <h2>Needs review</h2>
  <form method="post">
    <table>
      <tr><th>Name in CSV</th><th>Ballot</th></tr>
      {% for item in unmatched %}
      <tr>
        <td>{{ item.text }}<input type="hidden" name="input-{{ loop.index }}" value="{{ item.text }}"></td>
        <td>
          <select name="choice-{{ loop.index }}">
            <option value="">Decide later</option>
            {% for target, suggestion in item.choices %}
              <option value="{{ target.id }}">
                {% if suggestion %}★ {% endif %}{{ target.summary }}
                {% if suggestion %}({{ suggestion.source }}{% if suggestion.score is not none %}, {{ suggestion.score }}{% endif %}){% endif %}
              </option>
            {% endfor %}
            <option value="{{ no_match }}">No match</option>
          </select>
        </td>
      </tr>
      {% endfor %}
    </table>
    <p class="muted">★ marks suggestions from earlier backends.{% if matcher.mode == "symmetric" %} If every name but one is decided, the last is matched to the last ballot automatically.{% endif %}</p>
    <p><button>Save choices</button></p>
  </form>
{% endif %}

<h2>Matched ({{ matcher.matches|length }})</h2>
<table>
  <tr><th>Name in CSV</th><th>Ballot</th><th>Matched by</th><th>Rows</th></tr>
  {% for text, m in matcher.matches.items() %}
  <tr>
    <td>{{ text }}</td>
    <td>{% if m.target %}{{ m.target.label }}<br><code class="muted">{{ m.target.id }}</code>{% else %}<em>No match</em>{% endif %}</td>
    <td><span class="tag">{{ m.backend }}{% if m.confidence is not none %} {{ m.confidence }}{% endif %}</span></td>
    <td>{{ rows_per_name[text] }}</td>
  </tr>
  {% endfor %}
</table>
"""


if __name__ == "__main__":
    load_dotenv()
    app.run(debug=True)
