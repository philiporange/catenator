"""Configuration settings and environment loading for Jev integration.

This module defines configuration constants and the immutable JevSettings dataclass
representing Typesafe Jev API connection parameters and pricing. It also provides
the get_jev_settings function, which merges configuration values across user home
(~/.env), project-specific (<project>/.env), and explicit process environments
(os.environ) without mutating process state.
"""

from dataclasses import dataclass, field
import math
import os
from pathlib import Path
from typing import Optional, Union

import dotenv

JEV_STATE_TOKEN_LIMIT: int = 28000
JEV_REQUEST_TOKEN_LIMIT: int = 60000
JEV_PAIR_TOKEN_LIMIT: int = 30000


@dataclass(frozen=True)
class JevSettings:
    """Immutable configuration settings for Jev API requests."""

    api_key: str = field(default="", repr=False)
    url: str = "https://api.typesafe.ai/v1/systemone"
    model: str = "jev-1.13.0"
    input_price_per_million: float = 0.042
    timeout: float = 60.0

    def __post_init__(self) -> None:
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
        "TYPESAFE_API_KEY",
        "TYPESAFE_URL",
        "TYPESAFE_MODEL",
        "CATENATOR_JEV_INPUT_PRICE",
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

    api_key = merged.get("TYPESAFE_API_KEY", "")

    raw_url = merged.get("TYPESAFE_URL")
    url = raw_url if raw_url else "https://api.typesafe.ai/v1/systemone"

    raw_model = merged.get("TYPESAFE_MODEL")
    model = raw_model if raw_model else "jev-1.13.0"

    raw_price = merged.get("CATENATOR_JEV_INPUT_PRICE")
    if raw_price is not None and raw_price != "":
        try:
            price = float(raw_price)
        except (ValueError, TypeError):
            raise ValueError(
                f"Invalid CATENATOR_JEV_INPUT_PRICE: {raw_price!r}"
            )
        if isinstance(price, bool) or not math.isfinite(price) or price < 0:
            raise ValueError(
                f"Invalid CATENATOR_JEV_INPUT_PRICE: {raw_price!r} must be finite and non-negative"
            )
    else:
        price = 0.042

    return JevSettings(
        api_key=api_key,
        url=url,
        model=model,
        input_price_per_million=price,
        timeout=60.0,
    )
