"""HTTP client and response validation for the Typesafe Jev API.

This module provides the JevClient class for dispatching file scoring requests to
the Typesafe Jev API endpoint, enforcing bearer authentication, strict request shape,
and response structure validation including score bounds, confidence ranges, and token usage.
"""

import math
from typing import Any, Dict, Optional

import requests

from .config import JevSettings


class JevError(RuntimeError):
    """Raised when Jev requests fail or return invalid response data."""


class JevClient:
    """Client for evaluating state against questions using the Typesafe Jev API."""

    def __init__(
        self,
        settings: JevSettings,
        session: Optional[requests.Session] = None,
    ) -> None:
        self.settings = settings
        self.session = session if session is not None else requests.Session()

    def evaluate(
        self, state: Any, questions: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Evaluate a state representation against questions via Typesafe Jev API.

        Makes exactly one POST request without retries or redirects. Validates
        all response fields, ensuring every question receives a valid score answer.
        """
        if not self.settings.api_key or not self.settings.api_key.strip():
            raise JevError(
                "Missing Typesafe API key: set TYPESAFE_API_KEY in environment or ~/.env"
            )

        if not questions or not isinstance(questions, dict):
            raise JevError("Questions must be a non-empty dictionary")

        payload = {
            "state": state,
            "model": self.settings.model,
            "questions": questions,
        }
        headers = {
            "Authorization": f"Bearer {self.settings.api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = self.session.post(
                self.settings.url,
                json=payload,
                headers=headers,
                timeout=self.settings.timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise JevError(
                f"Jev request network error: {type(exc).__name__}"
            ) from None

        if response.status_code in (400, 413, 422):
            raise JevError(
                f"Jev request failed with HTTP {response.status_code}: check Jev context limits"
            )
        if response.status_code != 200:
            raise JevError(
                f"Jev request failed with HTTP {response.status_code}"
            )

        try:
            data = response.json()
        except ValueError:
            raise JevError("Jev response is not valid JSON") from None

        if not isinstance(data, dict):
            raise JevError("Jev response must be a JSON object")

        model = data.get("model")
        if not isinstance(model, str) or not model.strip():
            raise JevError("Jev response missing or invalid 'model'")

        usage = data.get("usage")
        if not isinstance(usage, dict):
            raise JevError("Jev response missing or invalid 'usage'")

        input_tokens = usage.get("input_tokens")
        if (
            isinstance(input_tokens, bool)
            or not isinstance(input_tokens, int)
            or input_tokens < 0
        ):
            raise JevError(
                "Jev response 'usage.input_tokens' must be a non-negative integer"
            )

        answers = data.get("answers")
        if not isinstance(answers, dict):
            raise JevError("Jev response missing or invalid 'answers'")

        expected_ids = set(questions.keys())
        actual_ids = set(answers.keys())
        if expected_ids != actual_ids:
            raise JevError("Jev response answers IDs mismatch")

        for q_id, ans in answers.items():
            if not isinstance(ans, dict):
                raise JevError(f"Jev answer for {q_id!r} must be an object")
            if ans.get("type") != "score":
                raise JevError(f"Jev answer for {q_id!r} type must be 'score'")

            score = ans.get("score")
            if (
                isinstance(score, bool)
                or not isinstance(score, (int, float))
                or not math.isfinite(score)
                or not (0 <= score <= 2)
            ):
                raise JevError(
                    f"Jev answer for {q_id!r} score must be a finite number in [0, 2]"
                )

            if "confidence" in ans:
                conf = ans["confidence"]
                if (
                    isinstance(conf, bool)
                    or not isinstance(conf, (int, float))
                    or not math.isfinite(conf)
                    or not (0 <= conf <= 1)
                ):
                    raise JevError(
                        f"Jev answer for {q_id!r} confidence must be a finite number in [0, 1]"
                    )

        return data
