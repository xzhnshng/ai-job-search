from __future__ import annotations

from email.message import Message
from io import BytesIO
import unittest
from urllib.error import HTTPError

from ai_job_search.infrastructure.http.safe_client import SafeHttpClient


def public_resolver(host, port, *, type):  # noqa: A002, ANN001
    return [(2, type, 6, "", ("93.184.216.34", port))]


class FakeResponse:
    def __init__(
        self,
        body: bytes,
        *,
        url: str = "https://jobs.example.com/openings",
        status: int = 200,
        content_type: str = "application/json",
        content_length: int | None = None,
    ) -> None:
        self.status = status
        self._url = url
        self._body = BytesIO(body)
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def geturl(self) -> str:
        return self._url

    def read(self, limit: int) -> bytes:
        return self._body.read(limit)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):  # noqa: ANN001
        return False


class FakeOpener:
    def __init__(self, *results) -> None:
        self.results = list(results)

    def open(self, request, timeout):  # noqa: ANN001
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class SafeHttpClientTests(unittest.TestCase):
    def test_fetch_accepts_bounded_allowlisted_public_https_response(self) -> None:
        opener = FakeOpener(FakeResponse(b'{"jobs":[]}'))
        client = SafeHttpClient(resolver=public_resolver, opener=opener)

        response = client.fetch(
            "https://jobs.example.com/openings",
            allowed_hosts={"jobs.example.com"},
            accepted_content_types={"application/json"},
        )

        self.assertEqual(200, response.status)
        self.assertEqual(b'{"jobs":[]}', response.body)

    def test_rejects_dns_resolution_to_private_address(self) -> None:
        def private_resolver(host, port, *, type):  # noqa: A002, ANN001
            return [(2, type, 6, "", ("127.0.0.1", port))]

        client = SafeHttpClient(
            resolver=private_resolver,
            opener=FakeOpener(FakeResponse(b"{}")),
        )
        with self.assertRaisesRegex(ValueError, "non-public IP"):
            client.fetch(
                "https://jobs.example.com/openings",
                allowed_hosts={"jobs.example.com"},
                accepted_content_types={"application/json"},
            )

    def test_rejects_redirect_to_non_allowlisted_host(self) -> None:
        headers = Message()
        headers["Location"] = "https://attacker.example/jobs"
        redirect = HTTPError(
            "https://jobs.example.com/openings", 302, "Found", headers, None
        )
        client = SafeHttpClient(
            resolver=public_resolver,
            opener=FakeOpener(redirect, FakeResponse(b"{}")),
        )

        with self.assertRaisesRegex(ValueError, "not allowlisted"):
            client.fetch(
                "https://jobs.example.com/openings",
                allowed_hosts={"jobs.example.com"},
                accepted_content_types={"application/json"},
            )

    def test_rejects_oversized_and_unexpected_content(self) -> None:
        oversized = SafeHttpClient(
            max_response_bytes=4,
            resolver=public_resolver,
            opener=FakeOpener(FakeResponse(b"12345")),
        )
        with self.assertRaisesRegex(ValueError, "size limit"):
            oversized.fetch(
                "https://jobs.example.com/openings",
                allowed_hosts={"jobs.example.com"},
                accepted_content_types={"application/json"},
            )

        wrong_type = SafeHttpClient(
            resolver=public_resolver,
            opener=FakeOpener(FakeResponse(b"{}", content_type="text/html")),
        )
        with self.assertRaisesRegex(ValueError, "content type"):
            wrong_type.fetch(
                "https://jobs.example.com/openings",
                allowed_hosts={"jobs.example.com"},
                accepted_content_types={"application/json"},
            )


if __name__ == "__main__":
    unittest.main()
