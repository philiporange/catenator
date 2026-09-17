"""Tests for Jev HTTP client and configuration resolution.

This test suite verifies the JevClient request shape, header and authentication
handling, payload structure with explicit per-file paths in question instructions,
safe handling of network/HTTP errors without credential leakage, strict response
validation, and environment precedence/isolation across configurations.
"""

from pathlib import Path
from unittest.mock import MagicMock
import math
import os
import pytest
import requests

from src.catenator.config import (
    JevSettings,
    get_jev_settings,
)
from src.catenator.jev_client import JevClient, JevError


THREE_LEVEL_CRITERIA = [
    "0: ignore",
    "1: summarize",
    "2: verbatim",
]


def _build_sample_questions() -> dict:
    return {
        "q_catenator": {
            "type": "score",
            "instructions": "Score src/catenator/catenator.py for inclusion in orientation context.",
            "criteria": THREE_LEVEL_CRITERIA,
        },
        "q_cli": {
            "type": "score",
            "instructions": "Score src/catenator/cli.py for inclusion in orientation context.",
            "criteria": THREE_LEVEL_CRITERIA,
        },
    }


def _build_valid_api_response() -> dict:
    return {
        "model": "jev-1.13.0",
        "usage": {"input_tokens": 1250},
        "answers": {
            "q_catenator": {
                "type": "score",
                "score": 2,
                "confidence": 0.95,
                "probabilities": {"0": 0.01, "1": 0.04, "2": 0.95},
            },
            "q_cli": {
                "type": "score",
                "score": 1,
                "confidence": 0.85,
                "probabilities": {"0": 0.05, "1": 0.85, "2": 0.10},
            },
        },
    }


def test_jev_settings_repr_masks_api_key() -> None:
    settings = JevSettings(api_key="super-secret-key-xyz")
    assert "super-secret-key-xyz" not in repr(settings)
    assert settings.api_key == "super-secret-key-xyz"
    assert settings.url == "https://api.typesafe.ai/v1/systemone"
    assert settings.model == "jev-1.13.0"
    assert settings.input_price_per_million == 0.042
    assert settings.timeout == 60.0


def test_request_shape_and_field_preservation() -> None:
    questions = _build_sample_questions()
    state_content = "def catenate(): pass"
    settings = JevSettings(api_key="typesafe-bearer-token")

    mock_session = MagicMock(spec=requests.Session)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = _build_valid_api_response()
    mock_session.post.return_value = mock_response

    client = JevClient(settings, session=mock_session)
    result = client.evaluate(state=state_content, questions=questions)

    assert mock_session.post.call_count == 1
    call_args, call_kwargs = mock_session.post.call_args

    assert call_args[0] == "https://api.typesafe.ai/v1/systemone"
    assert call_kwargs["timeout"] == 60.0
    assert call_kwargs["allow_redirects"] is False

    headers = call_kwargs["headers"]
    assert headers["Authorization"] == "Bearer typesafe-bearer-token"
    assert headers["Content-Type"] == "application/json"

    payload = call_kwargs["json"]
    assert payload["state"] == state_content
    assert payload["model"] == "jev-1.13.0"
    assert payload["questions"] == questions

    # Verify per-file paths are present in instructions, not question IDs
    assert (
        "src/catenator/catenator.py"
        in payload["questions"]["q_catenator"]["instructions"]
    )
    assert (
        "src/catenator/cli.py" in payload["questions"]["q_cli"]["instructions"]
    )

    # Verify preserved fields in result
    assert result["model"] == "jev-1.13.0"
    assert result["usage"]["input_tokens"] == 1250
    assert result["answers"]["q_catenator"]["score"] == 2
    assert result["answers"]["q_catenator"]["probabilities"] == {
        "0": 0.01,
        "1": 0.04,
        "2": 0.95,
    }
    assert result["answers"]["q_cli"]["score"] == 1
    assert result["answers"]["q_cli"]["confidence"] == 0.85


def test_missing_api_key_refusal() -> None:
    mock_session = MagicMock(spec=requests.Session)

    for empty_key in ("", "   "):
        settings = JevSettings(api_key=empty_key)
        client = JevClient(settings, session=mock_session)

        with pytest.raises(JevError) as exc_info:
            client.evaluate("state", _build_sample_questions())

        msg = str(exc_info.value)
        assert "TYPESAFE_API_KEY" in msg
        assert "environment or ~/.env" in msg

    assert mock_session.post.call_count == 0


def test_empty_questions_refusal() -> None:
    mock_session = MagicMock(spec=requests.Session)
    settings = JevSettings(api_key="valid-key")
    client = JevClient(settings, session=mock_session)

    with pytest.raises(JevError) as exc_info:
        client.evaluate("state", {})

    assert "Questions must be a non-empty dictionary" in str(exc_info.value)
    assert mock_session.post.call_count == 0


@pytest.mark.parametrize("status_code", [400, 413, 422])
def test_http_context_limits_errors(status_code: int) -> None:
    mock_session = MagicMock(spec=requests.Session)
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.text = "Error detail: payload context exceeded"
    mock_session.post.return_value = mock_response

    key = "secret-credential-should-not-leak"
    settings = JevSettings(api_key=key)
    client = JevClient(settings, session=mock_session)

    with pytest.raises(JevError) as exc_info:
        client.evaluate("state", _build_sample_questions())

    msg = str(exc_info.value)
    assert f"HTTP {status_code}" in msg
    assert "check Jev context limits" in msg
    assert key not in msg
    assert "https://" not in msg
    assert "payload context exceeded" not in msg


def test_http_generic_error_sanitization() -> None:
    mock_session = MagicMock(spec=requests.Session)
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal error details"
    mock_session.post.return_value = mock_response

    key = "secret-key"
    settings = JevSettings(api_key=key)
    client = JevClient(settings, session=mock_session)

    with pytest.raises(JevError) as exc_info:
        client.evaluate("state", _build_sample_questions())

    msg = str(exc_info.value)
    assert "HTTP 500" in msg
    assert "context limits" not in msg
    assert key not in msg
    assert "https://" not in msg
    assert "Internal error details" not in msg


def test_network_exception_sanitization() -> None:
    mock_session = MagicMock(spec=requests.Session)
    mock_session.post.side_effect = requests.ConnectionError(
        "Failed to connect to https://api.typesafe.ai"
    )

    key = "secret-key"
    settings = JevSettings(api_key=key)
    client = JevClient(settings, session=mock_session)

    with pytest.raises(JevError) as exc_info:
        client.evaluate("state", _build_sample_questions())

    msg = str(exc_info.value)
    assert "ConnectionError" in msg
    assert key not in msg
    assert "https://" not in msg


def test_invalid_json_response() -> None:
    mock_session = MagicMock(spec=requests.Session)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.side_effect = ValueError("Invalid JSON string")
    mock_session.post.return_value = mock_response

    client = JevClient(JevSettings(api_key="key"), session=mock_session)
    with pytest.raises(JevError) as exc_info:
        client.evaluate("state", _build_sample_questions())
    assert "not valid JSON" in str(exc_info.value)


def test_non_dict_response() -> None:
    mock_session = MagicMock(spec=requests.Session)
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = ["not", "a", "dict"]
    mock_session.post.return_value = mock_response

    client = JevClient(JevSettings(api_key="key"), session=mock_session)
    with pytest.raises(JevError) as exc_info:
        client.evaluate("state", _build_sample_questions())
    assert "must be a JSON object" in str(exc_info.value)


@pytest.mark.parametrize("bad_model", [None, "", "   ", 123])
def test_invalid_model_response(bad_model) -> None:
    mock_session = MagicMock(spec=requests.Session)
    mock_response = MagicMock()
    mock_response.status_code = 200
    data = _build_valid_api_response()
    data["model"] = bad_model
    mock_response.json.return_value = data
    mock_session.post.return_value = mock_response

    client = JevClient(JevSettings(api_key="key"), session=mock_session)
    with pytest.raises(JevError) as exc_info:
        client.evaluate("state", _build_sample_questions())
    assert "model" in str(exc_info.value)


@pytest.mark.parametrize(
    "bad_input_tokens", [None, "10", -1, True, False, 10.5]
)
def test_invalid_usage_input_tokens(bad_input_tokens) -> None:
    mock_session = MagicMock(spec=requests.Session)
    mock_response = MagicMock()
    mock_response.status_code = 200
    data = _build_valid_api_response()
    if bad_input_tokens is None:
        del data["usage"]["input_tokens"]
    else:
        data["usage"]["input_tokens"] = bad_input_tokens
    mock_response.json.return_value = data
    mock_session.post.return_value = mock_response

    client = JevClient(JevSettings(api_key="key"), session=mock_session)
    with pytest.raises(JevError) as exc_info:
        client.evaluate("state", _build_sample_questions())
    assert "usage" in str(exc_info.value)


def test_missing_or_extra_question_ids_in_answers() -> None:
    mock_session = MagicMock(spec=requests.Session)
    mock_response = MagicMock()
    mock_response.status_code = 200
    client = JevClient(JevSettings(api_key="key"), session=mock_session)

    # Missing q_cli
    data_missing = _build_valid_api_response()
    del data_missing["answers"]["q_cli"]
    mock_response.json.return_value = data_missing
    mock_session.post.return_value = mock_response

    with pytest.raises(JevError) as exc_info:
        client.evaluate("state", _build_sample_questions())
    assert "mismatch" in str(exc_info.value)

    # Extra q_unexpected
    data_extra = _build_valid_api_response()
    data_extra["answers"]["q_unexpected"] = {
        "type": "score",
        "score": 1,
    }
    mock_response.json.return_value = data_extra
    with pytest.raises(JevError) as exc_info:
        client.evaluate("state", _build_sample_questions())
    assert "mismatch" in str(exc_info.value)


@pytest.mark.parametrize(
    "bad_score", [None, "1", -0.1, 2.1, True, False, math.nan, math.inf]
)
def test_invalid_score_validation(bad_score) -> None:
    mock_session = MagicMock(spec=requests.Session)
    mock_response = MagicMock()
    mock_response.status_code = 200
    data = _build_valid_api_response()
    data["answers"]["q_catenator"]["score"] = bad_score
    mock_response.json.return_value = data
    mock_session.post.return_value = mock_response

    client = JevClient(JevSettings(api_key="key"), session=mock_session)
    with pytest.raises(JevError) as exc_info:
        client.evaluate("state", _build_sample_questions())
    assert "score" in str(exc_info.value)


@pytest.mark.parametrize(
    "bad_conf", ["0.5", -0.1, 1.1, True, False, math.nan, math.inf]
)
def test_invalid_confidence_validation(bad_conf) -> None:
    mock_session = MagicMock(spec=requests.Session)
    mock_response = MagicMock()
    mock_response.status_code = 200
    data = _build_valid_api_response()
    data["answers"]["q_catenator"]["confidence"] = bad_conf
    mock_response.json.return_value = data
    mock_session.post.return_value = mock_response

    client = JevClient(JevSettings(api_key="key"), session=mock_session)
    with pytest.raises(JevError) as exc_info:
        client.evaluate("state", _build_sample_questions())
    assert "confidence" in str(exc_info.value)


def test_fractional_score_and_valid_confidence_accepted() -> None:
    mock_session = MagicMock(spec=requests.Session)
    mock_response = MagicMock()
    mock_response.status_code = 200
    data = _build_valid_api_response()
    data["answers"]["q_catenator"]["score"] = 1.5
    data["answers"]["q_catenator"]["confidence"] = 0.5
    mock_response.json.return_value = data
    mock_session.post.return_value = mock_response

    client = JevClient(JevSettings(api_key="key"), session=mock_session)
    res = client.evaluate("state", _build_sample_questions())
    assert res["answers"]["q_catenator"]["score"] == 1.5
    assert res["answers"]["q_catenator"]["confidence"] == 0.5


def test_env_precedence_and_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Set up isolated home directory
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    # Clear relevant env vars from active process
    for k in (
        "TYPESAFE_API_KEY",
        "TYPESAFE_URL",
        "TYPESAFE_MODEL",
        "CATENATOR_JEV_INPUT_PRICE",
    ):
        monkeypatch.delenv(k, raising=False)

    # 1. Base defaults when nothing is set
    default_settings = get_jev_settings()
    assert default_settings.api_key == ""
    assert default_settings.url == "https://api.typesafe.ai/v1/systemone"
    assert default_settings.model == "jev-1.13.0"
    assert default_settings.input_price_per_million == 0.042
    assert default_settings.timeout == 60.0

    # 2. ~/.env configuration
    home_env = fake_home / ".env"
    home_env.write_text(
        "TYPESAFE_API_KEY=home-key\n"
        "TYPESAFE_MODEL=jev-home-model\n"
        "CATENATOR_JEV_INPUT_PRICE=0.050\n"
    )

    settings_from_home = get_jev_settings()
    assert settings_from_home.api_key == "home-key"
    assert settings_from_home.model == "jev-home-model"
    assert settings_from_home.input_price_per_million == 0.050
    assert settings_from_home.url == "https://api.typesafe.ai/v1/systemone"

    # 3. <project>/.env overrides ~/.env
    proj_a = tmp_path / "project_a"
    proj_a.mkdir()
    proj_a_env = proj_a / ".env"
    proj_a_env.write_text(
        "TYPESAFE_API_KEY=proj-a-key\n"
        "TYPESAFE_MODEL=jev-proj-a\n"
        "CATENATOR_JEV_INPUT_PRICE=0.060\n"
    )

    settings_proj_a = get_jev_settings(proj_a)
    assert settings_proj_a.api_key == "proj-a-key"
    assert settings_proj_a.model == "jev-proj-a"
    assert settings_proj_a.input_price_per_million == 0.060

    # 4. Project isolation: project_b must NOT see project_a values
    proj_b = tmp_path / "project_b"
    proj_b.mkdir()
    proj_b_env = proj_b / ".env"
    proj_b_env.write_text("TYPESAFE_API_KEY=proj-b-key\n")

    settings_proj_b = get_jev_settings(proj_b)
    assert settings_proj_b.api_key == "proj-b-key"
    # Inherits from ~/.env, not project_a
    assert settings_proj_b.model == "jev-home-model"
    assert settings_proj_b.input_price_per_million == 0.050

    # 5. Explicit os.environ wins over both ~/.env and project .env
    monkeypatch.setenv("TYPESAFE_API_KEY", "env-key")
    monkeypatch.setenv("TYPESAFE_MODEL", "jev-env-model")
    monkeypatch.setenv("CATENATOR_JEV_INPUT_PRICE", "0.080")

    settings_env_override = get_jev_settings(proj_a)
    assert settings_env_override.api_key == "env-key"
    assert settings_env_override.model == "jev-env-model"
    assert settings_env_override.input_price_per_million == 0.080

    # 6. Empty values in env fallback to defaults (except api_key stays empty)
    monkeypatch.setenv("TYPESAFE_API_KEY", "")
    monkeypatch.setenv("TYPESAFE_URL", "")
    monkeypatch.setenv("TYPESAFE_MODEL", "")
    monkeypatch.setenv("CATENATOR_JEV_INPUT_PRICE", "")

    settings_empty = get_jev_settings(proj_a)
    assert settings_empty.api_key == ""
    assert settings_empty.url == "https://api.typesafe.ai/v1/systemone"
    assert settings_empty.model == "jev-1.13.0"
    assert settings_empty.input_price_per_million == 0.042

    # 7. Invalid price rejection
    for bad_price in ("not-a-number", "-0.01", "nan", "inf", "-inf"):
        monkeypatch.setenv("CATENATOR_JEV_INPUT_PRICE", bad_price)
        with pytest.raises(ValueError):
            get_jev_settings(proj_a)


def test_get_jev_settings_does_not_mutate_environ(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_snapshot = dict(os.environ)
    get_jev_settings()
    assert dict(os.environ) == env_snapshot
