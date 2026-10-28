"""Auth layer tests (requires DB).

Covers APIKey model, resolve_user (transport layer), and get_bearer_user
(REST API layer). Both auth helpers resolve the same underlying APIKey table
— tested here together so the shared failure mode is visible in one place.
"""
import pytest
from django.contrib.auth.models import User
from django.test import RequestFactory

from api.auth import get_bearer_user
from api.models import APIKey
from transport.auth import resolve_user


@pytest.fixture
def user(db):
    return User.objects.create_user("testuser", password="test")


@pytest.fixture
def api_key(user):
    return APIKey.generate(user, label="test-key")


class TestAPIKeyModel:
    def test_generate_creates_exactly_one_key(self, user, api_key):
        assert APIKey.objects.filter(user=user).count() == 1

    def test_generated_key_is_long_and_url_safe(self, api_key):
        # secrets.token_urlsafe(32) produces ≥43 characters of base64url
        assert len(api_key.key) >= 32
        assert all(c.isalnum() or c in "-_" for c in api_key.key)

    def test_generate_stores_label(self, user):
        key = APIKey.generate(user, label="my-app")
        assert key.label == "my-app"

    def test_generate_creates_unique_keys(self, user):
        k1 = APIKey.generate(user, label="a")
        k2 = APIKey.generate(user, label="b")
        assert k1.key != k2.key


class TestResolveUser:
    def test_valid_key_returns_correct_user(self, user, api_key):
        assert resolve_user(api_key.key) == user

    def test_invalid_key_returns_none(self, db):
        assert resolve_user("totally-fake-token-xyz-123") is None

    def test_empty_string_returns_none(self, db):
        assert resolve_user("") is None


class TestGetBearerUser:
    def test_valid_bearer_returns_user(self, user, api_key):
        rf = RequestFactory()
        request = rf.get("/", HTTP_AUTHORIZATION=f"Bearer {api_key.key}")
        assert get_bearer_user(request) == user

    def test_missing_header_returns_none(self, db):
        rf = RequestFactory()
        request = rf.get("/")
        assert get_bearer_user(request) is None

    def test_non_bearer_scheme_returns_none(self, db):
        rf = RequestFactory()
        request = rf.get("/", HTTP_AUTHORIZATION="Basic dXNlcjpwYXNz")
        assert get_bearer_user(request) is None

    def test_bearer_with_invalid_token_returns_none(self, db):
        rf = RequestFactory()
        request = rf.get("/", HTTP_AUTHORIZATION="Bearer bad-token-xyz")
        assert get_bearer_user(request) is None