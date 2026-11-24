"""Small Gmail REST client (standard library only)."""
import base64
import http.client
import json
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
TIMEOUT = 10


class GmailError(Exception):
    """A Gmail call failed. status is the HTTP status (0 for network errors)."""

    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


def urllib_transport(method, url, headers, body):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()
    except (urllib.error.URLError, OSError, http.client.HTTPException) as err:
        raise GmailError(f"Network error talking to Google: {type(err).__name__}") from None


def _json(raw):
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


class Client:
    """Gmail API calls using an access token provider."""

    def __init__(self, token_provider, transport=urllib_transport):
        self.token_provider = token_provider
        self.transport = transport

    def call(self, method, path, params=None):
        url = BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        for attempt in (0, 1):
            headers = {"Authorization": "Bearer " + self.token_provider(force=attempt == 1)}
            status, raw = self.transport(method, url, headers, None)
            if status == 401 and attempt == 0:
                continue
            data = _json(raw)
            if 200 <= status < 300:
                return data
            error = data.get("error", {})
            message = error.get("message", "") if isinstance(error, dict) else str(error)
            if status == 403 and "insufficient scope" in message.lower():
                raise GmailError("Gmail access was not granted. Tick the Gmail box when connecting.", status)
            raise GmailError(f"Gmail error {status}: {message or 'unknown'}", status)

    def search(self, query, limit=50):
        if limit <= 0:
            return []
        found, page = [], None
        while len(found) < limit:
            params = {"q": query, "maxResults": min(limit - len(found), 100)}
            if page:
                params["pageToken"] = page
            data = self.call("GET", "/messages", params=params)
            messages = data.get("messages", [])
            found.extend(item["id"] for item in messages if item.get("id"))
            found = found[:limit]
            page = data.get("nextPageToken")
            if not page or not messages:
                break
        return found

    def get_message(self, message_id):
        data = self.call("GET", "/messages/" + urllib.parse.quote(message_id, safe=""),
                         params={"format": "full"})
        payload = data.get("payload", {})
        headers = {}
        for item in payload.get("headers", []):
            name = item.get("name", "").lower()
            if name in ("from", "subject"):
                headers[name] = item.get("value", "")
        attachments = []

        def walk(part):
            filename = part.get("filename", "")
            body = part.get("body", {})
            if filename and body.get("attachmentId"):
                attachments.append({
                    "filename": filename,
                    "mime_type": part.get("mimeType", ""),
                    "attachment_id": body["attachmentId"],
                    "size": body.get("size", 0),
                })
            for child in part.get("parts", []):
                walk(child)

        walk(payload)
        try:
            internal_date = int(data.get("internalDate", 0))
        except (TypeError, ValueError):
            internal_date = 0
        return {"id": data.get("id", message_id), "internal_date": internal_date,
                "from": headers.get("from", ""), "subject": headers.get("subject", ""),
                "attachments": attachments}

    def get_attachment(self, message_id, attachment_id, max_bytes=5_000_000):
        path = ("/messages/" + urllib.parse.quote(message_id, safe="") + "/attachments/"
                + urllib.parse.quote(attachment_id, safe=""))
        data = self.call("GET", path)
        encoded = data.get("data", "")
        if not isinstance(encoded, str) or len(encoded) > max_bytes * 4 // 3 + 8:
            raise GmailError("Gmail attachment exceeds the maximum size")
        try:
            content = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        except (ValueError, TypeError):
            raise GmailError("Gmail returned invalid attachment data") from None
        if len(content) > max_bytes:
            raise GmailError("Gmail attachment exceeds the maximum size")
        return content
