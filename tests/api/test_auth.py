import pytest
from fastapi import HTTPException

from app.api.auth import require_api_key
from app.config import Settings


def test_require_api_key_allows_when_no_key_configured():
    settings = Settings(_env_file=None, api_key=None)
    require_api_key(x_api_key=None, settings=settings)  # does not raise


def test_require_api_key_rejects_missing_header_when_key_configured():
    settings = Settings(_env_file=None, api_key="secret123")
    with pytest.raises(HTTPException) as exc_info:
        require_api_key(x_api_key=None, settings=settings)
    assert exc_info.value.status_code == 401


def test_require_api_key_rejects_wrong_header_when_key_configured():
    settings = Settings(_env_file=None, api_key="secret123")
    with pytest.raises(HTTPException) as exc_info:
        require_api_key(x_api_key="wrong", settings=settings)
    assert exc_info.value.status_code == 401


def test_require_api_key_accepts_correct_header():
    settings = Settings(_env_file=None, api_key="secret123")
    require_api_key(x_api_key="secret123", settings=settings)  # does not raise
