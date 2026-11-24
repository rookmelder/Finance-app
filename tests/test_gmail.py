import base64
import json
import unittest

from src.gmail import Client, GmailError


def response(data):
    return json.dumps(data).encode()


class FakeTransport:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append((method, url, headers, body))
        return self.results.pop(0)


class GmailTests(unittest.TestCase):
    def client(self, transport, token=None):
        if token is None:
            token = lambda force=False: "access"
        return Client(token, transport=transport)

    def test_search_pages_and_limit_query_encoding_and_empty(self):
        fake = FakeTransport([
            (200, response({"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "next"})),
            (200, response({"messages": [{"id": "c"}, {"id": "d"}]})),
        ])
        client = self.client(fake)
        query = 'from:me "hello world"'
        self.assertEqual(client.search(query, limit=3), ["a", "b", "c"])
        self.assertIn("q=from%3Ame+%22hello+world%22", fake.calls[0][1])
        self.assertIn("pageToken=next", fake.calls[1][1])
        self.assertEqual(fake.calls[0][0], "GET")
        empty = self.client(FakeTransport([(200, response({}))]))
        self.assertEqual(empty.search("x"), [])

    def test_message_nested_parts_headers_and_missing_subject(self):
        fake = FakeTransport([(200, response({
            "id": "id", "internalDate": "12345",
            "payload": {"headers": [{"name": "fRoM", "value": "a@example.test"}], "parts": [
                {"mimeType": "multipart/mixed", "parts": [
                    {"mimeType": "text/plain", "body": {"data": "ignored"}},
                    {"filename": "report.pdf", "mimeType": "application/pdf",
                     "body": {"attachmentId": "att", "size": 42}},
                ]},
            ]},
        }))])
        result = self.client(fake).get_message("id/with space")
        self.assertEqual(result, {"id": "id", "internal_date": 12345,
                                  "from": "a@example.test", "subject": "",
                                  "attachments": [{"filename": "report.pdf", "mime_type": "application/pdf",
                                                   "attachment_id": "att", "size": 42}]})
        self.assertIn("id%2Fwith%20space", fake.calls[0][1])
        self.assertIn("format=full", fake.calls[0][1])

    def test_attachment_urlsafe_without_padding_and_limit(self):
        encoded = base64.urlsafe_b64encode(b"\xfb\xff").decode().rstrip("=")
        fake = FakeTransport([(200, response({"data": encoded}))])
        self.assertEqual(self.client(fake).get_attachment("m", "a"), b"\xfb\xff")
        fake = FakeTransport([(200, response({"data": encoded}))])
        with self.assertRaises(GmailError):
            self.client(fake).get_attachment("m", "a", max_bytes=1)

    def test_attachment_too_large_is_refused_before_decoding(self):
        big = "A" * 400
        fake = FakeTransport([(200, response({"data": big}))])
        with self.assertRaises(GmailError):
            self.client(fake).get_attachment("m", "a", max_bytes=100)

    def test_unauthorized_retries_with_forced_token_then_fails(self):
        requested = []

        def provider(force=False):
            requested.append(force)
            return "fresh" if force else "old"

        fake = FakeTransport([(401, b"no"), (200, response({"messages": [{"id": "ok"}]}))])
        self.assertEqual(Client(provider, fake).search("x"), ["ok"])
        self.assertEqual(requested, [False, True])
        self.assertEqual(fake.calls[1][2]["Authorization"], "Bearer fresh")

        requested.clear()
        fake = FakeTransport([(401, b"no"), (401, b"still no")])
        with self.assertRaises(GmailError) as ctx:
            Client(provider, fake).search("x")
        self.assertEqual(ctx.exception.status, 401)

    def test_forbidden_insufficient_scope_and_non_json_error(self):
        fake = FakeTransport([(403, response({"error": {"message": "Insufficient Scope"}}))])
        with self.assertRaisesRegex(GmailError, "Gmail access was not granted"):
            self.client(fake).search("x")
        fake = FakeTransport([(500, b"not json")])
        with self.assertRaisesRegex(GmailError, "Gmail error 500: unknown"):
            self.client(fake).search("x")


if __name__ == "__main__":
    unittest.main()
