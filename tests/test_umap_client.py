import tempfile
import unittest
from pathlib import Path

import requests
from cryptography.fernet import Fernet
from unittest.mock import patch

from telegram_geo_report_bot.__main__ import _configure_umap_session
from telegram_geo_report_bot.umap.client import (
    UMapAmbiguousUploadError,
    UMapAuthenticationError,
    UMapClient,
    UMapConflictError,
    UMapError,
    UMapNetworkError,
)
from telegram_geo_report_bot.umap.session_store import (
    UMapCredentials,
    UMapSessionError,
    load_session,
    save_session,
)

from umap_helpers import layer, make_umap_services


class FakeResponse:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self.payload = payload
        self.headers = headers or {}

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeSession:
    def __init__(self, gets=None, post=None):
        self.cookies = requests.cookies.RequestsCookieJar()
        self.headers = {}
        self.gets = list(gets or [])
        self.post_response = post
        self.last_post = None

    def get(self, *args, **kwargs):
        result = self.gets.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def post(self, *args, **kwargs):
        self.last_post = (args, kwargs)
        if isinstance(self.post_response, Exception):
            raise self.post_response
        return self.post_response


class UMapClientTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.services = make_umap_services(Path(self.temporary.name))
        self.config = self.services.settings.config.umap
        self.credentials = UMapCredentials("SESSION_SECRET", "CSRF_SECRET", "Browser/1.0")

    def tearDown(self):
        self.temporary.cleanup()

    def client(self, session):
        return UMapClient(self.config, self.credentials, user_agent="Fallback/1.0", session=session)

    def test_download_requires_conflict_version(self):
        with self.assertRaises(UMapError):
            self.client(FakeSession(gets=[FakeResponse(payload=layer())])).download_layer()

    def test_upload_contains_csrf_reference_and_geojson(self):
        session = FakeSession(post=FakeResponse(payload={"id": self.config.layer_id}))
        self.client(session).upload_layer(layer(), "version-1")
        kwargs = session.last_post[1]
        self.assertEqual("CSRF_SECRET", kwargs["headers"]["X-CSRFToken"])
        self.assertEqual("version-1", kwargs["headers"]["X-Datalayer-Reference"])
        self.assertIn("geojson", kwargs["files"])

    def test_authentication_conflict_and_transient_errors_are_classified(self):
        with self.assertRaises(UMapAuthenticationError):
            self.client(FakeSession(gets=[FakeResponse(status=403)])).verify_login()
        with self.assertRaises(UMapNetworkError):
            self.client(FakeSession(gets=[FakeResponse(status=502)])).verify_login()
        with self.assertRaises(UMapConflictError):
            self.client(FakeSession(post=FakeResponse(status=412))).upload_layer(
                layer(), "version"
            )

    def test_upload_timeout_is_ambiguous_and_does_not_expose_secrets(self):
        client = self.client(FakeSession(post=requests.Timeout("SESSION_SECRET")))
        with self.assertRaises(UMapAmbiguousUploadError) as raised:
            client.upload_layer(layer(), "version")
        self.assertNotIn("SESSION_SECRET", str(raised.exception))
        self.assertNotIn("SESSION_SECRET", repr(self.credentials))

        with self.assertRaises(UMapAmbiguousUploadError):
            self.client(FakeSession(post=FakeResponse(status=502))).upload_layer(
                layer(), "version"
            )


class UMapSessionTests(unittest.TestCase):
    def test_encrypted_roundtrip_and_corrupt_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "session.enc"
            key = Fernet.generate_key()
            cipher = Fernet(key)
            credentials = UMapCredentials("SESSION_SECRET", "CSRF_SECRET", "Browser/1.0")
            save_session(path, cipher, credentials)
            stored = path.read_bytes()
            self.assertNotIn(b"SESSION_SECRET", stored)
            self.assertEqual(credentials, load_session(path, cipher))
            path.write_bytes(b"corrupt")
            with self.assertRaises(UMapSessionError):
                load_session(path, cipher)

    def test_interactive_configuration_verifies_before_saving(self):
        with tempfile.TemporaryDirectory() as temporary:
            services = make_umap_services(Path(temporary))
            with patch("getpass.getpass", side_effect=["session", "csrf"]), patch(
                "builtins.input", return_value="Browser/1.0"
            ), patch(
                "telegram_geo_report_bot.__main__.UMapClient"
            ) as client_class:
                self.assertEqual(0, _configure_umap_session(services))
            client_class.return_value.verify_login.assert_called_once_with()
            credentials = load_session(
                services.settings.paths.umap_session_file,
                services.settings.cipher,
            )
            self.assertEqual("session", credentials.session_id)
            self.assertEqual("csrf", credentials.csrf_token)


if __name__ == "__main__":
    unittest.main()
