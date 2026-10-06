"""Matching with Jev, a multiple-choice model (typesafe/jev-1.13 via defapi.org).

API docs: https://defapi.org/api/model/en/typesafe/jev-1.13
"""

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
import httpx

from .core import Match, MatchBackend, Suggestion, Suggestions, Target
from .profiles import Profile

logger = logging.getLogger(__name__)

BASE_URL = "https://api.defapi.org"
ENDPOINT = "/api/v1/decisions"
MODEL = "typesafe/jev-1.13"
API_KEY_ENV = "DEFAPI_API_KEY"

# The option Jev picks when no target fits.
NO_MATCH = "no_match"
# The most options Jev accepts in one question, including NO_MATCH.
MAX_OPTIONS = 255


class JevError(RuntimeError):
    """A Jev request couldn't be made or failed."""


# --- API client --------------------------------------------------------------


def choice(instructions: str, options: dict[str, str]) -> dict:
    """A single-select question; `options` maps option name -> description (max 255)."""
    return {"type": "choice", "instructions": instructions, "criteria": options}


def build_request(state: str | dict | list, questions: dict[str, dict]) -> dict:
    return {"model": MODEL, "state": state, "questions": questions}


def api_key_from_env() -> str:
    key = os.environ.get(API_KEY_ENV)
    if not key:
        raise JevError(f"No Jev API key: pass api_key or set the {API_KEY_ENV} environment variable")
    return key


def send(body: dict, api_key: str, timeout: float = 60.0, attempts: int = 3) -> dict:
    """POST a request body to the decisions endpoint and return the JSON response.

    Connection errors and 5xx responses are retried with backoff; 4xx errors
    (bad request, bad key, no credit) raise JevError straight away.
    """
    for attempt in range(1, attempts + 1):
        try:
            response = httpx.post(
                BASE_URL + ENDPOINT,
                headers={"Authorization": f"Bearer {api_key}"},
                json=body,
                timeout=timeout,
            )
        except httpx.TransportError:
            if attempt == attempts:
                raise
        else:
            if response.status_code < 500 or attempt == attempts:
                break
        time.sleep(2**attempt)
    if response.is_error:
        raise JevError(f"HTTP {response.status_code}: {response.text}")
    return response.json()


# --- Backend -----------------------------------------------------------------


class JevMatch(MatchBackend):
    """Ask Jev to pick from the remaining targets; accept answers at or above `threshold`.

    Jev is given the matcher's profile's instructions, or `instructions` if passed. Answers
    below the threshold are left unmatched, with Jev's top picks as
    suggestions. The API key is `api_key`, or the DEFAPI_API_KEY environment
    variable. If a request fails, a warning is logged and the input is passed on
    unmatched. `cost` adds up what's been spent, in US dollars.
    """

    name = "jev"

    def __init__(
        self,
        *,
        instructions: str | None = None,
        threshold: float = 0.9,
        workers: int = 4,
        api_key: str | None = None,
    ):
        self.instructions = instructions
        self.threshold = threshold
        self.workers = workers
        self.api_key = api_key
        self.cost = 0.0

    def _ask(self, text: str, targets: list[Target], instructions: str, api_key: str) -> tuple[dict, float] | None:
        """Jev's answer for `text` and what it cost, or None if the request failed."""
        options = {t.id: t.summary for t in targets}
        options[NO_MATCH] = "None of the listed options is a plausible match"
        try:
            response = send(build_request(text, {"match": choice(instructions, options)}), api_key)
            return response["answers"]["match"], float(response.get("consumed") or 0)
        except Exception as e:  # noqa: BLE001 - pass the input on rather than failing the run
            logger.warning("Jev failed for %r: %s", text, e)
            return None

    def match(
        self,
        inputs: list[str],
        targets: list[Target],
        suggestions: Suggestions,
        profile: Profile,
        *,
        one_to_one: bool,
    ) -> list[Match]:
        if not targets:
            return []
        if len(targets) > MAX_OPTIONS - 1:
            raise ValueError(f"Jev supports at most {MAX_OPTIONS - 1} targets, got {len(targets)}")
        api_key = self.api_key or api_key_from_env()
        instructions = self.instructions or profile.jev_instructions

        by_id = {t.id: t for t in targets}
        with ThreadPoolExecutor(self.workers) as pool:
            responses = list(pool.map(lambda text: self._ask(text, targets, instructions, api_key), inputs))

        matches = []
        for text, response in zip(inputs, responses):
            if response is None:
                continue
            answer, cost = response
            self.cost += cost
            picked, confidence = answer["choice"], answer.get("confidence") or 0
            if picked in by_id and confidence >= self.threshold:
                matches.append(Match(text, by_id[picked], self.name, confidence))
                continue
            top = sorted(answer.get("probabilities", {}).items(), key=lambda kv: kv[1], reverse=True)
            suggestions[text].extend(
                Suggestion(by_id[tid], self.name, round(p, 2)) for tid, p in top[:3] if tid in by_id and p > 0.05
            )
        return matches
