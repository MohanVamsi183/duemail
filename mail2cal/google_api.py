"""Thin wrappers around the Gmail and Google Calendar APIs."""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .extract import html_to_text

# Read mail (never modify it) + create/edit calendar events.
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/calendar.events"]

COLORS = {"lavender": "1", "sage": "2", "grape": "3", "flamingo": "4", "banana": "5",
          "tangerine": "6", "peacock": "7", "graphite": "8", "blueberry": "9",
          "basil": "10", "tomato": "11"}


@dataclass
class Mail:
    id: str
    thread_id: str
    subject: str
    sender: str
    received: datetime
    body: str
    labels: list[str] = field(default_factory=list)   # label names

    @property
    def link(self) -> str:
        return f"https://mail.google.com/mail/u/0/#all/{self.thread_id}"


def get_credentials(home: Path):
    """Load the saved login, refreshing or opening the browser sign-in if needed."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    token, secrets = home / "token.json", home / "credentials.json"
    creds = Credentials.from_authorized_user_file(str(token), SCOPES) if token.exists() else None
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError:
            creds = None                # expired/revoked -> sign in again
    else:
        creds = None
    if creds is None:
        if not secrets.exists():
            raise SystemExit(
                f"Missing {secrets}\nCreate a Google OAuth 'Desktop app' client and save its "
                "JSON there. See README.md, section 'Google setup'.")
        creds = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES).run_local_server(port=0)
    token.write_text(creds.to_json())
    try:
        token.chmod(0o600)
    except OSError:
        pass
    return creds


class Gmail:
    def __init__(self, creds):
        from googleapiclient.discovery import build
        self.api = build("gmail", "v1", credentials=creds, cache_discovery=False).users()
        self._labels: dict[str, str] | None = None

    def labels(self) -> dict[str, str]:
        """label id -> label name"""
        if self._labels is None:
            res = self.api.labels().list(userId="me").execute()
            self._labels = {l["id"]: l["name"] for l in res.get("labels", [])}
        return self._labels

    def label_id(self, name: str) -> str | None:
        for i, n in self.labels().items():
            if n.lower() == name.lower():
                return i
        return None

    def search(self, query: str = "", label_id: str | None = None, limit: int = 100) -> list[str]:
        ids, page = [], None
        while len(ids) < limit:
            kw = dict(userId="me", q=query, maxResults=min(100, limit - len(ids)), pageToken=page)
            if label_id:
                kw["labelIds"] = [label_id]
            res = self.api.messages().list(**kw).execute()
            ids += [m["id"] for m in res.get("messages", [])]
            page = res.get("nextPageToken")
            if not page:
                break
        return ids

    def get(self, msg_id: str, tz) -> Mail:
        m = self.api.messages().get(userId="me", id=msg_id, format="full").execute()
        hdr = {h["name"].lower(): h["value"] for h in m["payload"].get("headers", [])}
        names = self.labels()
        return Mail(
            id=m["id"], thread_id=m["threadId"],
            subject=hdr.get("subject", "(no subject)"), sender=hdr.get("from", ""),
            received=datetime.fromtimestamp(int(m["internalDate"]) / 1000, tz),
            body=_body(m["payload"]) or m.get("snippet", ""),
            labels=[names.get(i, i) for i in m.get("labelIds", [])])


def _decode(part) -> str:
    data = part.get("body", {}).get("data")
    if not data:
        return ""
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def _body(payload) -> str:
    """Prefer text/plain; fall back to text/html with tags stripped."""
    plain, html = [], []

    def walk(p):
        mime = p.get("mimeType", "")
        if p.get("filename"):
            return                                   # attachment
        if mime == "text/plain":
            plain.append(_decode(p))
        elif mime == "text/html":
            html.append(_decode(p))
        for sub in p.get("parts", []) or []:
            walk(sub)

    walk(payload)
    text = "\n".join(t for t in plain if t.strip())
    return text if text.strip() else html_to_text("\n".join(html))


class Calendar:
    def __init__(self, creds):
        from googleapiclient.discovery import build
        self.api = build("calendar", "v3", credentials=creds, cache_discovery=False).events()

    def insert(self, calendar_id: str, body: dict) -> str:
        return self.api.insert(calendarId=calendar_id, body=body).execute()["id"]
