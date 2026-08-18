import io
import json
import os
import socket
import ssl
import sys
import unittest
import urllib.error
import urllib.request
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from arr_orchestrator.config import ServiceEndpoint
from arr_orchestrator.credentials import CredentialError, SecretValue
from arr_orchestrator.transport import (
    QbittorrentSession,
    ReadOnlyHttpTransport,
    TransportFailure,
    TransportPolicy,
    _default_opener,
    authenticate_qbittorrent_session,
)


class Response:
    def __init__(self, body=b'{"status":"ok"}', status=200, content_type="application/json", headers=None):
        self.status = status
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.body = io.BytesIO(body)
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self, amount=-1): return self.body.read(amount)


class BrokenReadResponse(Response):
    def read(self, amount=-1):
        raise ConnectionResetError("private-read-canary")


class Opener:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []
    def open(self, request, timeout=None):
        self.requests.append((request, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException): raise outcome
        return outcome


class Resolver:
    def resolve(self, ref):
        self.ref = ref
        return SecretValue("synthetic-token")


class TransportTests(unittest.TestCase):
    endpoint = ServiceEndpoint("svc", "https://service.test", "file:svc-token")

    def client(self, outcomes, **policy):
        opener = Opener(outcomes)
        transport = ReadOnlyHttpTransport(
            self.endpoint, Resolver(), policy=TransportPolicy(retry_backoff_seconds=0, **policy), opener=opener
        )
        return transport, opener

    def test_get_json_returns_parsed_data_without_url_header_or_body_metadata(self):
        client, opener = self.client([Response()])
        result = client.get_json("/api/v1/probe")
        self.assertEqual({"status": "ok"}, result)
        self.assertNotIn("url", json.dumps(result).lower())
        request, timeout = opener.requests[0]
        self.assertEqual("GET", request.method)
        self.assertEqual("Bearer synthetic-token", request.headers["Authorization"])
        self.assertGreater(timeout, 0)

        head_client, head_opener = self.client([Response(body=b"", content_type="")])
        self.assertEqual({}, head_client.request_json("HEAD", "/api/v1/probe"))
        self.assertEqual("HEAD", head_opener.requests[0][0].method)

    def test_supports_a_validated_service_specific_credential_header(self):
        opener = Opener([Response()])
        transport = ReadOnlyHttpTransport(
            self.endpoint,
            Resolver(),
            opener=opener,
            credential_header="X-Api-Key",
            credential_prefix="",
        )
        self.assertEqual({"status": "ok"}, transport.get_json("/api/v3/system/status"))
        request, _ = opener.requests[0]
        self.assertEqual("synthetic-token", request.headers["X-api-key"])
        self.assertNotIn("Authorization", request.headers)

        for header, prefix in (("Bad\r\nHeader", ""), ("X-Api-Key", "bad\n")):
            unopened = Opener([Response()])
            with self.subTest(header=header, prefix=prefix), self.assertRaises(ValueError):
                ReadOnlyHttpTransport(
                    self.endpoint,
                    Resolver(),
                    opener=unopened,
                    credential_header=header,
                    credential_prefix=prefix,
                )
            self.assertEqual([], unopened.requests)

        for header, prefix in (("Host", ""), ("Cookie", ""), ("Authorization", ""), ("X-Api-Key", "Bearer ")):
            unopened = Opener([Response()])
            with self.subTest(header=header, prefix=prefix), self.assertRaises(ValueError):
                ReadOnlyHttpTransport(
                    self.endpoint,
                    Resolver(),
                    opener=unopened,
                    credential_header=header,
                    credential_prefix=prefix,
                )
            self.assertEqual([], unopened.requests)

    def test_classifies_authentication_and_missing_resources_without_response_details(self):
        for status, code in ((401, "AUTH_FAILED"), (403, "AUTH_FAILED"), (404, "RESOURCE_NOT_FOUND")):
            failure = urllib.error.HTTPError(
                "https://service.test/private", status, "private-status-canary", {}, None
            )
            client, _ = self.client([failure], max_attempts=1)
            with self.subTest(status=status), self.assertRaises(TransportFailure) as caught:
                client.get_json("/private")
            self.assertEqual(code, caught.exception.code)
            self.assertNotIn("private-status-canary", repr(caught.exception) + str(caught.exception))
            self.assertIsNone(caught.exception.__cause__)
            self.assertIsNone(caught.exception.__context__)

    def test_default_opener_ignores_all_ambient_proxies(self):
        with mock.patch.dict(
            os.environ,
            {"http_proxy": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9"},
            clear=False,
        ):
            opener = _default_opener(True)
        proxy_handlers = [
            handler for handler in opener.handlers if isinstance(handler, urllib.request.ProxyHandler)
        ]
        self.assertFalse(proxy_handlers)

    def test_policy_rejects_non_finite_deadlines_and_backoff(self):
        for value in (float("inf"), float("-inf"), float("nan")):
            with self.subTest(deadline=value), self.assertRaises(ValueError):
                TransportPolicy(deadline_seconds=value)
            with self.subTest(backoff=value), self.assertRaises(ValueError):
                TransportPolicy(retry_backoff_seconds=value)

    def test_refuses_mutation_cross_origin_paths_and_redirects(self):
        client, _ = self.client([Response()])
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method), self.assertRaisesRegex(TransportFailure, "MUTATION_DISABLED"):
                client.request_json(method, "/safe")
        for path in ("https://evil.test/x", "//evil.test/x", "../escape", "/safe#fragment"):
            with self.subTest(path=path), self.assertRaises(TransportFailure):
                client.get_json(path)
        for path in ("/%2e%2e/private", "/safe%2F..%2Fprivate", "/safe\x00private", "/safe\nprivate"):
            with self.subTest(path=path), self.assertRaisesRegex(TransportFailure, "ORIGIN_DENIED"):
                client.get_json(path)
        redirect = urllib.error.HTTPError("https://service.test/x", 302, "Found", {"Location": "https://evil.test"}, None)
        client, _ = self.client([redirect])
        with self.assertRaisesRegex(TransportFailure, "REDIRECT_DENIED"):
            client.get_json("/x")

    def test_enforces_content_type_json_shape_size_and_sensitive_keys(self):
        opaque_body = json.dumps(
            {"value": "-----BEGIN " + "PRIVATE" + " KEY-----\nredacted\n-----END " + "PRIVATE" + " KEY-----"}
        ).encode("utf-8")
        certificate_body = json.dumps(
            {"value": "-----BEGIN " + "CERTIFICATE-----\nredacted\n-----END " + "CERTIFICATE-----"}
        ).encode("utf-8")
        public_material_body = json.dumps(
            {"value": "-----BEGIN " + "PUBLIC KEY-----\nredacted\n-----END " + "PUBLIC KEY-----"}
        ).encode("utf-8")
        cases = (
            (Response(content_type="text/plain"), "CONTENT_TYPE_INVALID"),
            (Response(body=b"{"), "JSON_INVALID"),
            (Response(body=b'"scalar"'), "JSON_INVALID"),
            (Response(body=b'{"api_key":"leak"}'), "PRIVATE_DATA_REDACTED"),
            (Response(body=b'{"url":"https://private.test"}'), "PRIVATE_DATA_REDACTED"),
            (Response(body=b'{"downloadUrl":"redacted-value"}'), "PRIVATE_DATA_REDACTED"),
            (Response(body=b'{"link":"http://x/y"}'), "PRIVATE_DATA_REDACTED"),
            (Response(body=b'{"link":"file:///private"}'), "PRIVATE_DATA_REDACTED"),
            (Response(body=b'{"link":"//private.test/x"}'), "PRIVATE_DATA_REDACTED"),
            (Response(body=b'{"echo":"synthetic-token"}'), "PRIVATE_DATA_REDACTED"),
            (Response(body=b'{"private_key":"redacted-value"}'), "PRIVATE_DATA_REDACTED"),
            (Response(body=b'{"privateKey":"redacted-value"}'), "PRIVATE_DATA_REDACTED"),
            (Response(body=opaque_body), "PRIVATE_DATA_REDACTED"),
            (Response(body=certificate_body), "PRIVATE_DATA_REDACTED"),
            (Response(body=public_material_body), "PRIVATE_DATA_REDACTED"),
            (Response(body=b'{"value":"%68%74%74%70%73%3A%2F%2Fprivate.test/x"}'), "PRIVATE_DATA_REDACTED"),
            (Response(body=b"x" * 129), "RESPONSE_TOO_LARGE"),
        )
        for response, code in cases:
            with self.subTest(code=code):
                client, _ = self.client([response], max_response_bytes=128)
                with self.assertRaisesRegex(TransportFailure, code):
                    client.get_json("/x")

    def test_retries_only_classified_idempotent_reads_with_one_deadline(self):
        error = urllib.error.URLError(socket.timeout("private-url-token"))
        client, opener = self.client([error, Response()], max_attempts=2, deadline_seconds=1)
        self.assertEqual({"status": "ok"}, client.get_json("/x"))
        self.assertEqual(2, len(opener.requests))
        client, opener = self.client([Response(status=503), Response()], max_attempts=2)
        self.assertEqual({"status": "ok"}, client.get_json("/x"))
        self.assertEqual(2, len(opener.requests))

    def test_failures_are_typed_redacted_and_never_echo_private_material(self):
        private_material = "https://service.test/private?credential=synthetic-private"
        client, _ = self.client([urllib.error.URLError(private_material)], max_attempts=1)
        with self.assertRaises(TransportFailure) as caught:
            client.get_json("/private")
        text = repr(caught.exception) + str(caught.exception)
        self.assertNotIn(private_material, text)
        self.assertNotIn("secret", text)
        self.assertEqual("SERVICE_UNREACHABLE", caught.exception.code)

        for body in (b'{"value":"raw-canary"', b'{"value":"raw-\xff-canary"}'):
            client, _ = self.client([Response(body=body)], max_attempts=1)
            with self.subTest(body=body), self.assertRaises(TransportFailure) as malformed:
                client.get_json("/private")
            self.assertEqual("JSON_INVALID", malformed.exception.code)
            self.assertIsNone(malformed.exception.__cause__)
            self.assertIsNone(malformed.exception.__context__)

        client, _ = self.client([BrokenReadResponse()], max_attempts=1)
        with self.assertRaises(TransportFailure) as broken_read:
            client.get_json("/private")
        self.assertEqual("SERVICE_UNREACHABLE", broken_read.exception.code)
        self.assertNotIn("private-read-canary", str(broken_read.exception))
        self.assertIsNone(broken_read.exception.__cause__)
        self.assertIsNone(broken_read.exception.__context__)

        client, _ = self.client([Response()])
        with self.assertRaises(TransportFailure) as invalid_path:
            client.get_json("/%ff")
        self.assertEqual("ORIGIN_DENIED", invalid_path.exception.code)
        self.assertIsNone(invalid_path.exception.__cause__)
        self.assertIsNone(invalid_path.exception.__context__)

        class UnicodeResolver:
            def resolve(self, ref):
                return SecretValue("tøken")

        transport = ReadOnlyHttpTransport(self.endpoint, UnicodeResolver(), opener=Opener([Response()]))
        with self.assertRaises(TransportFailure) as unicode_header:
            transport.get_json("/x")
        self.assertEqual("CREDENTIAL_INVALID", unicode_header.exception.code)
        self.assertIsNone(unicode_header.exception.__cause__)
        self.assertIsNone(unicode_header.exception.__context__)

        class FailingResolver:
            def resolve(self, ref):
                try:
                    raise OSError("private-filesystem-canary")
                except OSError as error:
                    raise CredentialError("credential unavailable") from error

        unopened = Opener([Response()])
        transport = ReadOnlyHttpTransport(self.endpoint, FailingResolver(), opener=unopened)
        with self.assertRaises(TransportFailure) as credential_failure:
            transport.get_json("/x")
        self.assertEqual("CREDENTIAL_INVALID", credential_failure.exception.code)
        self.assertIsNone(credential_failure.exception.__cause__)
        self.assertIsNone(credential_failure.exception.__context__)
        self.assertEqual([], unopened.requests)

    def test_rejects_response_that_completes_after_wall_clock_deadline(self):
        readings = iter((0.0, 0.0, 2.0))
        client, _ = self.client([Response()], max_attempts=1, deadline_seconds=1)
        client.clock = lambda: next(readings)
        with self.assertRaisesRegex(TransportFailure, "DEADLINE_EXCEEDED"):
            client.get_json("/x")

    def test_qbittorrent_auth_post_is_exact_opaque_and_never_retried(self):
        opener = Opener([Response(body=b"Ok.", content_type="text/plain", headers={"Set-Cookie": "SID=private-cookie; HttpOnly"})])
        session = authenticate_qbittorrent_session(
            ServiceEndpoint("qbit", "https://qbit.test", "file:unused"),
            SecretValue("user"), SecretValue("password"), opener=opener,
        )
        self.assertIsInstance(session, QbittorrentSession)
        self.assertNotIn("private-cookie", repr(session))
        self.assertEqual(1, len(opener.requests))
        request, _ = opener.requests[0]
        self.assertEqual("POST", request.method)
        self.assertEqual("/api/v2/auth/login", request.full_url.removeprefix("https://qbit.test"))

        readings = iter((0.0, 0.0, 0.0, 2.0))
        with self.assertRaisesRegex(TransportFailure, "DEADLINE_EXCEEDED"):
            authenticate_qbittorrent_session(
                ServiceEndpoint("qbit", "https://qbit.test", "file:unused"),
                SecretValue("user"),
                SecretValue("password"),
                policy=TransportPolicy(deadline_seconds=1),
                opener=Opener([Response(body=b"Ok.", content_type="text/plain")]),
                clock=lambda: next(readings),
            )

    def test_tls_certificate_failure_is_typed_and_never_retried(self):
        failure = urllib.error.URLError(ssl.SSLCertVerificationError("private-cert-canary"))
        client, opener = self.client([failure, failure, failure], max_attempts=3)
        with self.assertRaises(TransportFailure) as caught:
            client.get_json("/x")
        self.assertEqual("TLS_VERIFICATION_FAILED", caught.exception.code)
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(1, len(opener.requests))
        self.assertNotIn("private-cert-canary", str(caught.exception))

        for outcome in (
            urllib.error.URLError(ssl.SSLCertVerificationError("private-qbit-cert-canary")),
            ssl.SSLError("private-qbit-tls-canary"),
        ):
            with self.subTest(qbittorrent_tls=type(outcome).__name__):
                qbit_opener = Opener([outcome])
                with self.assertRaises(TransportFailure) as qbit_failure:
                    authenticate_qbittorrent_session(
                        ServiceEndpoint("qbit", "https://qbit.test", "file:unused"),
                        SecretValue("user"),
                        SecretValue("password"),
                        opener=qbit_opener,
                    )
                self.assertEqual("TLS_VERIFICATION_FAILED", qbit_failure.exception.code)
                self.assertFalse(qbit_failure.exception.retryable)
                self.assertEqual(1, len(qbit_opener.requests))
                self.assertIsNone(qbit_failure.exception.__cause__)
                self.assertIsNone(qbit_failure.exception.__context__)


if __name__ == "__main__":
    unittest.main()
