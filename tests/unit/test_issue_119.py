import os
from unittest.mock import patch

import pytest

from gateway.interceptors import request_interceptor


@pytest.fixture(autouse=True)
def reset_globals():
    request_interceptor._warned_fallback_signing_key = False
    request_interceptor._scoped_token_signing_key_cache = None
    request_interceptor._scoped_token_signing_key_expiry = 0
    yield


def test_scoped_token_signing_key_fails_in_prod_if_missing():
    # In prod, it must fail if no key or secret is provided
    env = {
        "PLATFORM_ENV": "prod",
        "SCOPED_TOKEN_SIGNING_KEY": "",
        "SCOPED_TOKEN_SIGNING_KEY_SECRET_ARN": "",
    }
    with patch.dict(os.environ, env, clear=True):
        msg = "SCOPED_TOKEN_SIGNING_KEY or SCOPED_TOKEN_SIGNING_KEY_SECRET_ARN must be configured"
        with pytest.raises(RuntimeError, match=msg):
            request_interceptor._get_scoped_token_signing_key()


def test_scoped_token_signing_key_fallback_works_in_local():
    # In local, it should still fall back for backward compatibility
    env = {"PLATFORM_ENV": "local", "SCOPED_TOKEN_SIGNING_KEY": ""}
    with patch.dict(os.environ, env, clear=True):
        if "SCOPED_TOKEN_SIGNING_KEY" in os.environ:
            del os.environ["SCOPED_TOKEN_SIGNING_KEY"]

        key = request_interceptor._get_scoped_token_signing_key()
        assert key is not None
        assert len(key) == 64  # hex encoded sha256


def test_scoped_token_signing_key_from_secret_works():
    s_arn = "arn:aws:sm:eu-west-2:123456789012:s:my-key"  # pragma: allowlist secret
    mock_key = "secret-key-from-sm-with-enough-length"  # pragma: allowlist secret

    env = {"PLATFORM_ENV": "prod", "SCOPED_TOKEN_SIGNING_KEY_SECRET_ARN": s_arn}
    with patch.dict(os.environ, env, clear=True):
        with patch(
            "gateway.interceptors.request_interceptor.get_secret", return_value=mock_key
        ) as mock_get_secret:
            key = request_interceptor._get_scoped_token_signing_key()
            assert key == mock_key
            mock_get_secret.assert_called_once_with(s_arn, max_age=300)

            # Test caching
            key2 = request_interceptor._get_scoped_token_signing_key()
            assert key2 == mock_key
            assert mock_get_secret.call_count == 1


def test_scoped_token_signing_key_length_warning(caplog):
    short_key = "too-short"
    with patch.dict(os.environ, {"SCOPED_TOKEN_SIGNING_KEY": short_key}, clear=False):
        key = request_interceptor._get_scoped_token_signing_key()
        assert key == short_key
        assert "SCOPED_TOKEN_SIGNING_KEY is too short" in caplog.text
