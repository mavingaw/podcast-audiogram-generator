"""Posting a finished clip to YouTube.

Google's OAuth and the YouTube Data API, with plain `requests`: no client
library, because the two calls that matter — exchange a code for tokens, and
a resumable upload — are small, and a library would add a dependency chain
bigger than this application for them.

What the operator supplies once: a Google Cloud OAuth client (id + secret)
with the YouTube Data API enabled. What each person does once: press
"Connect YouTube" and approve. What Kinder stores: the refresh token per
person, in the settings table, never in a project or a render manifest.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from pathlib import Path

import requests
from sqlalchemy.orm import Session

from app.db.models import AppSetting

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"

CLIENT_ID_KEY = "youtube.client_id"
CLIENT_SECRET_KEY = "youtube.client_secret"
PRIVACY = ("private", "unlisted", "public")


class YouTubeError(RuntimeError):
    pass


# ---------------------------------------------------------------- settings


def _get(db: Session, key: str) -> str:
    row = db.get(AppSetting, key)
    return row.value if row and row.value else ""


def _set(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key) or AppSetting(key=key, value="")
    row.value = value
    db.merge(row)


def client(db: Session) -> tuple[str, str]:
    return _get(db, CLIENT_ID_KEY), _get(db, CLIENT_SECRET_KEY)


def configured(db: Session) -> bool:
    cid, secret = client(db)
    return bool(cid and secret)


def set_client(db: Session, client_id: str, client_secret: str) -> None:
    _set(db, CLIENT_ID_KEY, client_id.strip())
    # An empty secret keeps the old one: the form never echoes it back.
    if client_secret.strip():
        _set(db, CLIENT_SECRET_KEY, client_secret.strip())
    db.commit()


def _account_key(user_id: str) -> str:
    return f"youtube:{user_id}"


def account(db: Session, user_id: str) -> dict | None:
    raw = _get(db, _account_key(user_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def disconnect(db: Session, user_id: str) -> None:
    _set(db, _account_key(user_id), "")
    db.commit()


# ---------------------------------------------------------------- OAuth


def begin(db: Session, user_id: str, redirect_uri: str) -> str:
    """The Google consent URL for this person. The state is remembered so the
    callback can tell a real return from a forged one."""
    cid, _ = client(db)
    if not cid:
        raise YouTubeError("YouTube is not set up yet. An admin needs to add the Google client ID and secret.")
    state = secrets.token_urlsafe(24)
    _set(db, f"youtube.state:{user_id}", state)
    db.commit()
    params = {
        "client_id": cid,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        # Forces a refresh token even when the person approved before.
        "prompt": "consent",
        "state": state,
        "include_granted_scopes": "true",
    }
    return AUTH_URL + "?" + requests.compat.urlencode(params)


def finish(db: Session, user_id: str, redirect_uri: str, code: str, state: str) -> dict:
    """Exchange the code, look up the channel, remember the account."""
    expected = _get(db, f"youtube.state:{user_id}")
    if not expected or not secrets.compare_digest(expected, state or ""):
        raise YouTubeError("That sign-in did not start here. Try Connect again.")
    _set(db, f"youtube.state:{user_id}", "")
    cid, secret = client(db)
    response = requests.post(TOKEN_URL, data={
        "code": code, "client_id": cid, "client_secret": secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
    }, timeout=20)
    if response.status_code != 200:
        raise YouTubeError("Google did not accept the sign-in: " + _reason(response))
    tokens = response.json()
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise YouTubeError("Google did not give a lasting sign-in. Remove Kinder from your Google account's third-party access and connect again.")
    acct = {
        "refresh_token": refresh,
        "access_token": tokens.get("access_token", ""),
        "expires_at": time.time() + float(tokens.get("expires_in", 0)) - 60,
        "channel": "",
        "channel_id": "",
    }
    try:
        info = requests.get(CHANNELS_URL, params={"part": "snippet", "mine": "true"},
                            headers={"Authorization": f"Bearer {acct['access_token']}"}, timeout=20).json()
        item = (info.get("items") or [{}])[0]
        acct["channel"] = item.get("snippet", {}).get("title", "")
        acct["channel_id"] = item.get("id", "")
    except Exception:  # pragma: no cover - the name is a nicety
        log.debug("could not read the channel name", exc_info=True)
    _set(db, _account_key(user_id), json.dumps(acct))
    db.commit()
    return acct


def _access_token(db: Session, user_id: str) -> str:
    acct = account(db, user_id)
    if not acct:
        raise YouTubeError("Connect YouTube first.")
    if acct.get("access_token") and time.time() < float(acct.get("expires_at", 0)):
        return acct["access_token"]
    cid, secret = client(db)
    response = requests.post(TOKEN_URL, data={
        "refresh_token": acct["refresh_token"], "client_id": cid, "client_secret": secret,
        "grant_type": "refresh_token",
    }, timeout=20)
    if response.status_code != 200:
        raise YouTubeError("YouTube sign-in has expired. Connect again. (" + _reason(response) + ")")
    tokens = response.json()
    acct["access_token"] = tokens.get("access_token", "")
    acct["expires_at"] = time.time() + float(tokens.get("expires_in", 0)) - 60
    _set(db, _account_key(user_id), json.dumps(acct))
    db.commit()
    return acct["access_token"]


# ---------------------------------------------------------------- upload


def upload(
    db: Session,
    user_id: str,
    video: Path,
    title: str,
    description: str = "",
    privacy: str = "private",
    tags: list[str] | None = None,
) -> dict:
    """Resumable upload of one file. Returns the video id and its URL.

    Private by default: a wrong clip published to a channel is not something
    a person should be able to do by accident from a button called Post.
    """
    if privacy not in PRIVACY:
        privacy = "private"
    if not video.exists():
        raise YouTubeError("Export the clip first.")
    token = _access_token(db, user_id)
    body = {
        "snippet": {
            "title": (title or "Clip")[:100],
            "description": description[:5000],
            "tags": (tags or [])[:20],
            "categoryId": "22",  # People & Blogs
        },
        "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False},
    }
    size = video.stat().st_size
    start = requests.post(
        UPLOAD_URL,
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(size),
            "X-Upload-Content-Type": "video/mp4",
        },
        data=json.dumps(body),
        timeout=30,
    )
    if start.status_code not in (200, 201):
        raise YouTubeError("YouTube refused the upload: " + _reason(start))
    session_url = start.headers.get("Location")
    if not session_url:
        raise YouTubeError("YouTube did not open an upload session.")
    with video.open("rb") as handle:
        sent = requests.put(
            session_url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "video/mp4",
                     "Content-Length": str(size)},
            data=handle,
            timeout=600,
        )
    if sent.status_code not in (200, 201):
        raise YouTubeError("The upload did not finish: " + _reason(sent))
    payload = sent.json()
    video_id = payload.get("id", "")
    return {
        "id": video_id,
        "url": f"https://youtu.be/{video_id}" if video_id else "",
        "privacy": privacy,
        "title": body["snippet"]["title"],
    }


def _reason(response) -> str:
    try:
        data = response.json()
        err = data.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("status") or response.status_code)
        if isinstance(err, str):
            return err + (": " + data["error_description"] if data.get("error_description") else "")
    except Exception:
        pass
    return f"HTTP {response.status_code}"
