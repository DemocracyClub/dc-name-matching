"""Walk through the web demo's two stages with Flask's test client (without Jev)."""

import csv
import io
import re

import pytest

import webdemo

ELECTION = "local.copeland.2019-05-02"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(webdemo, "STATE_DIR", tmp_path)
    return webdemo.app.test_client()


def unmatched_fields(html: str) -> dict[str, str]:
    """{field number: input text} for the review form's unmatched rows."""
    return {n: text for n, text in re.findall(r'name="input-(\d+)" value="([^"]*)"', html)}


def option_value(html: str, field: str, label: str) -> str:
    select = re.search(rf'<select name="choice-{field}">(.*?)</select>', html, re.S).group(1)
    return re.search(rf'<option value="([^"]+)">\s*(?:★ )?{re.escape(label)}\b', select).group(1)


def start(client) -> tuple[str, str]:
    response = client.post("/match", data={"election": ELECTION, "column": "ward"})
    assert response.status_code == 302
    url = response.headers["Location"]
    return url, client.get(url).get_data(as_text=True)


def test_two_stage_flow(client):
    url, html = start(client)
    fields = unmatched_fields(html)
    assert sorted(fields.values()) == sorted(
        ["Whitehavencentral", "Moesby", "distington", "arlecdon", "cleator moor north", "egremont north"]
    )

    # Stage 2, first submission: decide two names, leave the rest for later.
    by_text = {text: n for n, text in fields.items()}
    choices = {"Moesby": "Moresby", "Whitehavencentral": "Whitehaven Central"}
    data = {f"input-{n}": text for n, text in fields.items()}
    data |= {f"choice-{by_text[t]}": option_value(html, by_text[t], label) for t, label in choices.items()}
    html = client.post(url, data=data, follow_redirects=True).get_data(as_text=True)
    assert len(unmatched_fields(html)) == 4

    # Second submission: decide three of the four; the last is paired automatically.
    fields = unmatched_fields(html)
    by_text = {text: n for n, text in fields.items()}
    choices = {"distington": "Distington, Lowca &amp; Parton", "arlecdon": "Arlecdon &amp; Ennerdale",
               "cleator moor north": "Cleator Moor"}
    data = {f"input-{n}": text for n, text in fields.items()}
    data |= {f"choice-{by_text[t]}": option_value(html, by_text[t], label) for t, label in choices.items()}
    html = client.post(url, data=data, follow_redirects=True).get_data(as_text=True)
    assert "All 17 names are matched" in html
    assert "last-remaining" in html

    rows = list(csv.DictReader(io.StringIO(client.get(f"{url}/matched.csv").get_data(as_text=True))))
    assert all(row["ballot_paper_id"] == row["expected_ballot"] for row in rows)


def test_choosing_a_ballot_twice_shows_an_error(client):
    url, html = start(client)
    fields = unmatched_fields(html)
    first, second = list(fields)[:2]
    value = option_value(html, first, "Moresby")
    data = {f"input-{n}": text for n, text in fields.items()} | {f"choice-{first}": value, f"choice-{second}": value}
    html = client.post(url, data=data, follow_redirects=True).get_data(as_text=True)
    assert "Chosen for more than one input" in html
    assert len(unmatched_fields(html)) == 6  # nothing was saved


def test_csv_with_wrong_number_of_wards_is_rejected(client):
    rows = (webdemo.TESTDATA / f"{ELECTION}.csv").read_text().splitlines()
    kept = [rows[0]] + [r for r in rows[1:] if "Moesby" not in r]
    response = client.post(
        "/match",
        data={"election": ELECTION, "column": "ward", "csv": (io.BytesIO("\n".join(kept).encode()), "short.csv")},
        follow_redirects=True,
    )
    assert "16 inputs and 17 targets" in response.get_data(as_text=True)


def test_asymmetric_mode_accepts_a_csv_naming_some_wards(client):
    rows = (webdemo.TESTDATA / f"{ELECTION}.csv").read_text().splitlines()
    kept = [rows[0]] + [r for r in rows[1:] if "Moesby" not in r]
    upload = lambda: (io.BytesIO("\n".join(kept).encode()), "some.csv")  # noqa: E731

    response = client.post("/match", data={"election": ELECTION, "column": "ward", "csv": upload()},
                           follow_redirects=True)
    assert "16 inputs and 17 targets" in response.get_data(as_text=True)  # symmetric by default

    response = client.post(
        "/match",
        data={"election": ELECTION, "column": "ward", "mode": "asymmetric", "csv": upload()},
        follow_redirects=True,
    )
    html = response.get_data(as_text=True)
    assert len(unmatched_fields(html)) == 5  # the 6 hard names, minus the one left out
    assert "last-remaining" not in html  # never paired while a ballot is spare


def test_jev_cost_is_shown(client, monkeypatch):
    import dc_name_matching.jev

    monkeypatch.setenv("DEFAPI_API_KEY", "test-key")
    monkeypatch.setattr(
        dc_name_matching.jev,
        "send",
        lambda body, api_key: {"answers": {"match": {"choice": "no_match", "confidence": 1}}, "consumed": "0.0001"},
    )
    response = client.post("/match", data={"election": ELECTION, "column": "ward", "use_jev": "1"},
                           follow_redirects=True)
    assert "Jev cost $0.00060" in response.get_data(as_text=True)  # one call for each of the 6 hard names
