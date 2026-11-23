"""Google Calendar sync for worked days (standard library only).

One-way: the app is the source of truth. Each worked day becomes an all-day event in a dedicated
secondary calendar ("Work") that the app creates itself, using the narrow scope calendar.app.created.
Events are tagged with private extended properties so the sync only ever touches its own events.

All HTTP goes through a transport callable(method, url, headers, body_bytes_or_None) -> (status, body_bytes),
so tests can pass a fake one and never touch the network.
"""
import datetime
import http.client
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
API = "https://www.googleapis.com/calendar/v3"
SCOPE = "https://www.googleapis.com/auth/calendar.app.created"
CALENDAR_NAME = "Work"
TIMEZONE = "Europe/Brussels"
TAG_APP = "financeApp"
TAG_DATE = "financeDate"
TIMEOUT = 10

sync_lock = threading.Lock()
_token_cache = {}  # refresh_token -> (access_token, expires_at)
_cache_lock = threading.Lock()


class GcalError(Exception):
    """A Google call failed. status is the HTTP status (0 for network errors)."""

    def __init__(self, message, status=0):
        super().__init__(message)
        self.status = status


class AuthRevoked(GcalError):
    """The refresh token no longer works: the user must connect again."""


def urllib_transport(method, url, headers, body):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.read()
    except (urllib.error.URLError, OSError, http.client.HTTPException) as err:
        raise GcalError(f"Network error talking to Google: {type(err).__name__}") from None


def _json(raw):
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def auth_url(client_id, redirect_uri, state):
    """The Google consent page URL (offline access, always ask so a refresh token comes back)."""
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "false",
        "state": state,
    }
    return AUTH_URL + "?" + urllib.parse.urlencode(params)


def _post_form(transport, url, fields):
    body = urllib.parse.urlencode(fields).encode()
    return transport("POST", url, {"Content-Type": "application/x-www-form-urlencoded"}, body)


def exchange_code(client_id, client_secret, redirect_uri, code, transport=urllib_transport):
    """Trade the callback code for tokens. Returns the refresh token; raises GcalError."""
    status, raw = _post_form(transport, TOKEN_URL, {
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    })
    data = _json(raw)
    if status != 200:
        raise GcalError("Google refused the sign-in (" + str(data.get("error", status)) + ")", status)
    granted = str(data.get("scope", "")).split()
    if granted and SCOPE not in granted:
        raise GcalError("Calendar access was not granted. Tick the calendar box when connecting.", status)
    refresh = data.get("refresh_token")
    if not refresh:
        raise GcalError("Google sent no refresh token. Disconnect and connect again.", status)
    if data.get("access_token"):
        _cache_put(refresh, data["access_token"], data.get("expires_in", 0))
    return refresh


def revoke(token, transport=urllib_transport):
    """Best effort: tell Google to forget the token. Never raises."""
    try:
        _post_form(transport, REVOKE_URL, {"token": token})
    except GcalError:
        pass
    with _cache_lock:
        _token_cache.pop(token, None)


def _cache_put(refresh, access, expires_in):
    try:
        ttl = int(expires_in)
    except (TypeError, ValueError):
        ttl = 0
    with _cache_lock:
        _token_cache[refresh] = (access, time.time() + max(ttl - 60, 0))


class Client:
    """Calendar API calls with a refresh token. Access tokens are cached per refresh token."""

    def __init__(self, client_id, client_secret, refresh_token, transport=urllib_transport):
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.transport = transport

    def _access_token(self, force=False):
        with _cache_lock:
            cached = _token_cache.get(self.refresh_token)
        if cached and not force and cached[1] > time.time():
            return cached[0]
        status, raw = _post_form(self.transport, TOKEN_URL, {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
        })
        data = _json(raw)
        if status != 200 or not data.get("access_token"):
            if data.get("error") in ("invalid_grant", "unauthorized_client"):
                raise AuthRevoked("Google access was revoked or expired. Connect again in Settings.", status)
            raise GcalError("Could not refresh the Google token (" + str(data.get("error", status)) + ")", status)
        _cache_put(self.refresh_token, data["access_token"], data.get("expires_in", 3600))
        return data["access_token"]

    def call(self, method, path, params=None, body=None):
        """One API call. Returns the decoded JSON (or {}); raises GcalError on any non-2xx."""
        url = API + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        payload = json.dumps(body).encode() if body is not None else None
        for attempt in (0, 1):
            headers = {"Authorization": "Bearer " + self._access_token(force=attempt == 1)}
            if payload is not None:
                headers["Content-Type"] = "application/json"
            status, raw = self.transport(method, url, headers, payload)
            if status == 401 and attempt == 0:
                continue
            if 200 <= status < 300:
                return _json(raw)
            msg = _json(raw).get("error", {})
            msg = msg.get("message") if isinstance(msg, dict) else msg
            raise GcalError(f"Google Calendar error {status}: {msg or 'unknown'}", status)

    def create_calendar(self):
        data = self.call("POST", "/calendars", body={"summary": CALENDAR_NAME, "timeZone": TIMEZONE})
        if not data.get("id"):
            raise GcalError("Google did not return a calendar id")
        return data["id"]

    def list_tagged_events(self, calendar_id, first_day, after_last_day):
        """Our events whose start lies in [first_day, after_last_day) (ISO dates)."""
        events, page = [], None
        path = "/calendars/" + urllib.parse.quote(calendar_id, safe="") + "/events"
        while True:
            params = {
                "privateExtendedProperty": TAG_APP + "=1",
                "timeMin": first_day + "T00:00:00Z",
                "timeMax": after_last_day + "T00:00:00Z",
                "maxResults": 250,
                "showDeleted": "false",
            }
            if page:
                params["pageToken"] = page
            data = self.call("GET", path, params=params)
            events.extend(data.get("items", []))
            page = data.get("nextPageToken")
            if not page:
                return events

    def insert_event(self, calendar_id, body):
        return self.call("POST", "/calendars/" + urllib.parse.quote(calendar_id, safe="") + "/events", body=body)

    def patch_event(self, calendar_id, event_id, body):
        return self.call("PATCH", "/calendars/" + urllib.parse.quote(calendar_id, safe="") + "/events/"
                         + urllib.parse.quote(event_id, safe=""), body=body)

    def delete_event(self, calendar_id, event_id):
        try:
            self.call("DELETE", "/calendars/" + urllib.parse.quote(calendar_id, safe="") + "/events/"
                      + urllib.parse.quote(event_id, safe=""))
        except GcalError as err:
            if err.status not in (404, 410):  # already gone is fine
                raise


def _hours(minutes):
    return f"{minutes / 60:.2f}".rstrip("0").rstrip(".")


def event_body(day):
    """The all-day event for one worked day {"date", "minutes", "holiday"}."""
    start = datetime.date.fromisoformat(day["date"])
    title = ("Holiday " if day["holiday"] else "Work ") + _hours(day["minutes"]) + "h"
    return {
        "summary": title,
        "start": {"date": start.isoformat()},
        "end": {"date": (start + datetime.timedelta(days=1)).isoformat()},
        "transparency": "opaque",
        "extendedProperties": {"private": {TAG_APP: "1", TAG_DATE: start.isoformat()}},
    }


def month_bounds(work_month):
    """(first day, first day of next month) as ISO dates."""
    y, m = int(work_month[:4]), int(work_month[5:7])
    first = datetime.date(y, m, 1)
    nxt = datetime.date(y + (m == 12), m % 12 + 1, 1)
    return first.isoformat(), nxt.isoformat()


def plan_month(work_month, days, events):
    """Diff wanted days against existing tagged events.

    Returns (inserts, patches, deletes): inserts = [body], patches = [(event_id, body)], deletes = [event_id].
    Only events tagged with a date in work_month are considered; duplicates for a date are deleted.
    """
    wanted = {d["date"]: event_body(d) for d in days if d["date"].startswith(work_month + "-")}
    seen = {}
    deletes = []
    for ev in events:
        tag = (ev.get("extendedProperties") or {}).get("private", {}).get(TAG_DATE, "")
        if not tag.startswith(work_month + "-"):
            continue
        if tag in seen or tag not in wanted:
            deletes.append(ev["id"])
        else:
            seen[tag] = ev
    inserts, patches = [], []
    for date, body in sorted(wanted.items()):
        ev = seen.get(date)
        if ev is None:
            inserts.append(body)
        elif (ev.get("summary") != body["summary"] or (ev.get("start") or {}).get("date") != body["start"]["date"]
              or (ev.get("end") or {}).get("date") != body["end"]["date"]):
            patches.append((ev["id"], body))
    return inserts, patches, deletes


def sync_month(client, calendar_id, work_month, days):
    """Make the calendar match days for one work month. Returns {"inserted", "patched", "deleted"}."""
    first, after = month_bounds(work_month)
    # a day of margin on each side: all-day events are in the calendar's time zone, not UTC
    margin = datetime.timedelta(days=1)
    lo = (datetime.date.fromisoformat(first) - margin).isoformat()
    hi = (datetime.date.fromisoformat(after) + margin).isoformat()
    events = client.list_tagged_events(calendar_id, lo, hi)
    inserts, patches, deletes = plan_month(work_month, days, events)
    for event_id in deletes:
        client.delete_event(calendar_id, event_id)
    for event_id, body in patches:
        client.patch_event(calendar_id, event_id, body)
    for body in inserts:
        client.insert_event(calendar_id, body)
    return {"inserted": len(inserts), "patched": len(patches), "deleted": len(deletes)}
