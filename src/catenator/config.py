"""Configuration settings and defaults for Catenator services and summarization.

This module defines configuration constants for AI summarization and Jev
integration, including per-request and run-wide input budgets and bounded recovery,
along with immutable proxy connection settings for Luna Decisions. Gateway
credentials are separate from upstream keys; pricing is unknown unless configured.
It also provides the get_jev_settings function, which merges configuration values across user home (~/.env),
project-specific (<project>/.env), and explicit process environments (os.environ)
without mutating process state.
"""

from dataclasses import dataclass, field
import math
import os
from pathlib import Path
from typing import Optional, Union

import dotenv

DEFAULT_LLM_MODEL: str = "muse-code/muse-spark-1.3"
DEFAULT_LLM_BASE_URL: str = "https://llm.ph1l.uk/v1"
DEFAULT_LLM_MAX_TOKENS: int = 2048

# Local cl100k estimates reserve headroom for model tokenization and question framing.
JEV_STATE_TOKEN_LIMIT: int = 24000
JEV_REQUEST_TOKEN_LIMIT: int = 48000
JEV_PAIR_TOKEN_LIMIT: int = 26000
JEV_MAX_CONTEXT_SPLITS: int = 12
JEV_MAX_QUESTIONS: int = 400
DEFAULT_JEV_TOKEN_LIMIT: int = 500_000


@dataclass(frozen=True)
class JevSettings:
    """Immutable Luna settings for the proxy's Jev-compatible API."""

    api_key: str = field(default="", repr=False)
    url: str = "http://10.0.0.10:8046/v1/systemone"
    model: str = "openai/gpt-6-luna"
    input_price_per_million: Optional[float] = None
    timeout: float = 60.0

    def __post_init__(self) -> None:
        if self.input_price_per_million is None:
            return
        if isinstance(self.input_price_per_million, bool) or not isinstance(
            self.input_price_per_million, (int, float)
        ):
            raise ValueError(
                f"Invalid input_price_per_million: {self.input_price_per_million!r} must be numeric"
            )
        if (
            not math.isfinite(self.input_price_per_million)
            or self.input_price_per_million < 0
        ):
            raise ValueError(
                f"Invalid input_price_per_million: {self.input_price_per_million!r} must be finite and non-negative"
            )


def get_jev_settings(
    project_path: Optional[Union[str, Path]] = None,
) -> JevSettings:
    """Load and merge Jev settings from ~/.env, <project>/.env, and os.environ.

    Precedence order (highest to lowest):
    1. Explicit os.environ
    2. <project>/.env (if project_path is provided and exists)
    3. ~/.env (if present in user home)

    Empty string values fall back to defaults, except api_key which remains empty.
    Process environment (os.environ) is not mutated.
    """
    target_keys = (
        "CATENATOR_DECISION_API_KEY",
        "CATENATOR_DECISION_URL",
        "CATENATOR_DECISION_MODEL",
        "CATENATOR_DECISION_INPUT_PRICE",
    )

    home_env = Path.home() / ".env"
    home_vals = (
        dotenv.dotenv_values(home_env, interpolate=False)
        if home_env.is_file()
        else {}
    )

    project_vals = {}
    if project_path is not None:
        p = Path(project_path)
        proj_env = p / ".env"

        if proj_env.is_file():
            project_vals = dotenv.dotenv_values(proj_env, interpolate=False)

    merged: dict[str, str] = {}
    for source in (home_vals, project_vals, os.environ):
        for key in target_keys:
            if key in source:
                val = source[key]
                if val is not None:
                    merged[key] = str(val).strip()

    api_key = merged.get("CATENATOR_DECISION_API_KEY", "")

    raw_url = merged.get("CATENATOR_DECISION_URL")
    url = raw_url if raw_url else "http://10.0.0.10:8046/v1/systemone"

    raw_model = merged.get("CATENATOR_DECISION_MODEL")
    model = raw_model if raw_model else "openai/gpt-6-luna"

    raw_price = merged.get("CATENATOR_DECISION_INPUT_PRICE")
    if raw_price is not None and raw_price != "":
        try:
            price = float(raw_price)
        except (ValueError, TypeError):
            raise ValueError(
                f"Invalid CATENATOR_DECISION_INPUT_PRICE: {raw_price!r}"
            )
        if isinstance(price, bool) or not math.isfinite(price) or price < 0:
            raise ValueError(
                f"Invalid CATENATOR_DECISION_INPUT_PRICE: {raw_price!r} must be finite and non-negative"
            )
    else:
        price = None

    return JevSettings(
        api_key=api_key,
        url=url,
        model=model,
        input_price_per_million=price,
        timeout=60.0,
    )
