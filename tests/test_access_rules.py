"""Access rule enforcement tests (requires DB).

The access rule — "shared=True OR owner=user" — is a security invariant.
These tests verify it at the HTTP level, which catches regressions in the view
decorator chain and the SQL query together.
"""
import re

import pytest
from django.contrib.auth.models import User
from django.test import Client

from api.models import APIKey
from filestores.models import FileStore


@pytest.fixture
def alice(db):
    return User.objects.create_user("alice", password="test")


@pytest.fixture
def bob(db):
    return User.objects.create_user("bob", password="test")


@pytest.fixture
def alice_key(alice):
    return APIKey.generate(alice, label="alice-key")


@pytest.fixture
def bob_key(bob):
    return APIKey.generate(bob, label="bob-key")


@pytest.fixture
def shared_store(alice):
    return FileStore.objects.create(
        uid="shared", display_name="Shared", ref="/tmp/shared",
        shared=True, owner=alice,
    )


@pytest.fixture
def private_store(alice):
    return FileStore.objects.create(
        uid="private", display_name="Private", ref="/tmp/private",
        shared=False, ephemeral=False, owner=alice,
    )


@pytest.fixture
def ephemeral_store(alice):
    return FileStore.objects.create(
        uid="ephem001", display_name="Ephemeral", ref="/tmp/ephemeral",
        shared=False, ephemeral=True, owner=alice,
    )


def _auth(key: APIKey) -> dict:
    return {"HTTP_AUTHORIZATION": f"Bearer {key.key}"}


class TestAuthentication:
    def test_no_token_returns_401(self, db):
        r = Client().get("/api/filestores/")
        assert r.status_code == 401

    def test_invalid_token_returns_401(self, db):
        r = Client().get("/api/filestores/", HTTP_AUTHORIZATION="Bearer garbage")
        assert r.status_code == 401


class TestListAccessRule:
    def test_shared_store_visible_to_owner(self, shared_store, alice_key):
        r = Client().get("/api/filestores/", **_auth(alice_key))
        assert r.status_code == 200
        uids = [fs["uid"] for fs in r.json()["filestores"]]
        assert "shared" in uids

    def test_shared_store_visible_to_non_owner(self, shared_store, bob_key):
        r = Client().get("/api/filestores/", **_auth(bob_key))
        assert r.status_code == 200
        uids = [fs["uid"] for fs in r.json()["filestores"]]
        assert "shared" in uids

    def test_private_store_visible_to_owner(self, private_store, alice_key):
        r = Client().get("/api/filestores/", **_auth(alice_key))
        assert r.status_code == 200
        uids = [fs["uid"] for fs in r.json()["filestores"]]
        assert "private" in uids

    def test_private_store_not_visible_to_other_user(self, private_store, bob_key):
        r = Client().get("/api/filestores/", **_auth(bob_key))
        assert r.status_code == 200
        uids = [fs["uid"] for fs in r.json()["filestores"]]
        assert "private" not in uids


class TestDetailAccessRule:
    def test_owner_can_access_private_store(self, private_store, alice_key):
        r = Client().get(f"/api/filestores/{private_store.uid}/", **_auth(alice_key))
        assert r.status_code == 200

    def test_non_owner_cannot_access_private_store(self, private_store, bob_key):
        r = Client().get(f"/api/filestores/{private_store.uid}/", **_auth(bob_key))
        assert r.status_code == 404


class TestDeleteAccessRule:
    def test_owner_can_delete_ephemeral_store(self, ephemeral_store, alice_key):
        r = Client().delete(f"/api/filestores/{ephemeral_store.uid}/", **_auth(alice_key))
        assert r.status_code == 200
        assert r.json()["deleted"] is True

    def test_non_owner_cannot_delete_shared_store(self, shared_store, bob_key):
        r = Client().delete(f"/api/filestores/{shared_store.uid}/", **_auth(bob_key))
        assert r.status_code == 403

    def test_non_owner_gets_404_on_private_store_delete(self, private_store, bob_key):
        r = Client().delete(f"/api/filestores/{private_store.uid}/", **_auth(bob_key))
        assert r.status_code == 404


class TestPatch:
    def test_owner_can_update_display_name(self, private_store, alice_key):
        r = Client().patch(
            f"/api/filestores/{private_store.uid}/",
            data={"displayName": "Renamed"},
            content_type="application/json",
            **_auth(alice_key),
        )
        assert r.status_code == 200
        assert r.json()["displayName"] == "Renamed"

    def test_non_owner_cannot_patch(self, shared_store, bob_key):
        r = Client().patch(
            f"/api/filestores/{shared_store.uid}/",
            data={"displayName": "Hijacked"},
            content_type="application/json",
            **_auth(bob_key),
        )
        assert r.status_code == 403

    def test_empty_display_name_returns_400(self, private_store, alice_key):
        r = Client().patch(
            f"/api/filestores/{private_store.uid}/",
            data={"displayName": "   "},
            content_type="application/json",
            **_auth(alice_key),
        )
        assert r.status_code == 400


class TestDeregister:
    def test_owner_can_deregister(self, private_store, alice_key):
        r = Client().post(
            f"/api/filestores/{private_store.uid}/deregister",
            **_auth(alice_key),
        )
        assert r.status_code == 200
        assert r.json()["deregistered"] is True
        assert not FileStore.objects.filter(uid=private_store.uid).exists()

    def test_non_owner_cannot_deregister_shared_store(self, shared_store, bob_key):
        r = Client().post(
            f"/api/filestores/{shared_store.uid}/deregister",
            **_auth(bob_key),
        )
        assert r.status_code == 403
        assert FileStore.objects.filter(uid=shared_store.uid).exists()

    def test_non_owner_gets_404_on_private_store_deregister(self, private_store, bob_key):
        r = Client().post(
            f"/api/filestores/{private_store.uid}/deregister",
            **_auth(bob_key),
        )
        assert r.status_code == 404


class TestEphemeralDelete:
    def test_owner_cannot_delete_shared_store(self, shared_store, alice_key):
        r = Client().delete(f"/api/filestores/{shared_store.uid}/", **_auth(alice_key))
        assert r.status_code == 403
        assert FileStore.objects.filter(uid=shared_store.uid).exists()

    def test_owner_cannot_delete_non_ephemeral_private_store(self, private_store, alice_key):
        r = Client().delete(f"/api/filestores/{private_store.uid}/", **_auth(alice_key))
        assert r.status_code == 403
        assert FileStore.objects.filter(uid=private_store.uid).exists()


class TestRegister:
    def test_register_returns_201_with_generated_uid(self, alice_key, db):
        r = Client().post(
            "/api/filestores/register",
            data={"ref": "/tmp/new", "displayName": "New Store", "kind": "local"},
            content_type="application/json",
            **_auth(alice_key),
        )
        assert r.status_code == 201
        assert re.fullmatch(r"[a-z0-9]{8}", r.json()["uid"])

    def test_caller_supplied_uid_is_ignored(self, alice_key, db):
        r = Client().post(
            "/api/filestores/register",
            data={"uid": "chosen01", "ref": "/tmp/x", "displayName": "Chosen"},
            content_type="application/json",
            **_auth(alice_key),
        )
        assert r.status_code == 201
        assert r.json()["uid"] != "chosen01"

    def test_duplicate_display_names_are_allowed(self, alice_key, db):
        payload = {"ref": "/tmp/x", "displayName": "Same Name"}
        first = Client().post(
            "/api/filestores/register",
            data=payload,
            content_type="application/json",
            **_auth(alice_key),
        )
        second = Client().post(
            "/api/filestores/register",
            data=payload,
            content_type="application/json",
            **_auth(alice_key),
        )
        assert first.status_code == second.status_code == 201
        assert first.json()["uid"] != second.json()["uid"]

    def test_missing_display_name_returns_400(self, alice_key, db):
        r = Client().post(
            "/api/filestores/register",
            data={"ref": "/tmp/x"},
            content_type="application/json",
            **_auth(alice_key),
        )
        assert r.status_code == 400

    def test_creator_becomes_owner(self, alice_key, db):
        r = Client().post(
            "/api/filestores/register",
            data={"ref": "/tmp/owned", "displayName": "Owned"},
            content_type="application/json",
            **_auth(alice_key),
        )
        assert FileStore.objects.get(uid=r.json()["uid"]).owner.username == "alice"


class TestProvision:
    def test_provisions_under_generated_uid(self, alice_key, db, settings, tmp_path):
        settings.FILESTORE_ROOT = tmp_path
        r = Client().post(
            "/api/filestores/",
            data={"uid": "chosen01", "displayName": "Working Files"},
            content_type="application/json",
            **_auth(alice_key),
        )

        assert r.status_code == 201
        body = r.json()
        assert re.fullmatch(r"[a-z0-9]{8}", body["uid"])
        assert body["uid"] != "chosen01"
        assert body["ref"] == str(tmp_path / body["uid"])
        assert (tmp_path / body["uid"]).is_dir()
        assert body["ephemeral"] is True

    def test_missing_display_name_returns_400(self, alice_key, db, settings, tmp_path):
        settings.FILESTORE_ROOT = tmp_path
        r = Client().post(
            "/api/filestores/",
            data={},
            content_type="application/json",
            **_auth(alice_key),
        )

        assert r.status_code == 400


class TestFilesEndpoint:
    def test_returns_file_list(self, shared_store, alice_key, tmp_path):
        (tmp_path / "a.txt").write_text("hello")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.txt").write_text("world")
        shared_store.ref = str(tmp_path)
        shared_store.save()
        r = Client().get(f"/api/filestores/{shared_store.uid}/files/", **_auth(alice_key))
        assert r.status_code == 200
        paths = {f["path"] for f in r.json()["files"]}
        assert "a.txt" in paths
        assert "sub/b.txt" in paths

    def test_requires_auth(self, shared_store):
        r = Client().get(f"/api/filestores/{shared_store.uid}/files/")
        assert r.status_code == 401

    def test_private_store_not_accessible_to_non_owner(self, private_store, bob_key):
        r = Client().get(f"/api/filestores/{private_store.uid}/files/", **_auth(bob_key))
        assert r.status_code == 404