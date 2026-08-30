"""Connecting social accounts and posting clips to them.

One pattern for every platform, the same as YouTube's: an admin pastes the
platform's app credentials once, each person presses Connect and signs in on
the platform's own page, and finished clips grow a Post button. Until the
credentials are in, nothing shows.

What each platform actually allows differs, and this module is honest about
it rather than pretending:

- **Meta** (Facebook Page + Instagram Business): full video posting via the
  Graph API. Requires a Facebook developer app; Instagram needs a
  Business/Creator account linked to a Facebook Page, and publishing outside
  the app's own testers needs Meta's app review.
- **TikTok**: direct video upload via the Content Posting API. An app that
  has not passed TikTok's audit may only post as private (SELF_ONLY) — the
  post succeeds and sits in the person's drafts/private list.
- **LinkedIn**: video shares via the assets API.
- **Pinterest**: video pins to the person's first board.
- **X**: the free API tier cannot upload video, so Post shares the clip's
  public Kinder link as a post — the page previews with the poster frame.

Tokens are stored per person in the settings table, never in projects.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlencode

import requests
from sqlalchemy.orm import Session

from app.db.models import AppSetting

log = logging.getLogger(__name__)


class SocialError(RuntimeError):
    pass


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    auth_url: str
    token_url: str
    scopes: str
    # Extra query parameters some platforms insist on.
    auth_params: dict = field(default_factory=dict)
    # How the client id/secret are named in the token request.
    id_param: str = "client_id"
    secret_param: str = "client_secret"
    # A one-line note shown to the admin next to the key fields.
    note: str = ""
    # What Post actually does, shown on the button's tooltip.
    posts: str = "video"


PROVIDERS: dict[str, Provider] = {
    "meta": Provider(
        key="meta",
        label="Facebook + Instagram",
        auth_url="https://www.facebook.com/v21.0/dialog/oauth",
        token_url="https://graph.facebook.com/v21.0/oauth/access_token",
        scopes="pages_show_list,pages_manage_posts,pages_read_engagement,instagram_basic,instagram_content_publish,business_management",
        note="Meta developer app. Instagram posting needs a Business/Creator account linked to a Facebook Page; posting for people outside the app's testers needs Meta app review.",
        posts="video to the Page, and a Reel to the linked Instagram",
    ),
    "tiktok": Provider(
        key="tiktok",
        label="TikTok",
        auth_url="https://www.tiktok.com/v2/auth/authorize/",
        token_url="https://open.tiktokapis.com/v2/oauth/token/",
        scopes="user.info.basic,video.publish,video.upload",
        auth_params={"response_type": "code"},
        id_param="client_key",
        note="TikTok developer app with the Content Posting API. Before TikTok's audit approves the app, posts can only be private (they land in the person's private videos).",
        posts="video (private until TikTok audits the app)",
    ),
    "linkedin": Provider(
        key="linkedin",
        label="LinkedIn",
        auth_url="https://www.linkedin.com/oauth/v2/authorization",
        token_url="https://www.linkedin.com/oauth/v2/accessToken",
        scopes="openid profile w_member_social",
        note="LinkedIn developer app with the Share on LinkedIn product enabled.",
        posts="video share on the person's profile",
    ),
    "pinterest": Provider(
        key="pinterest",
        label="Pinterest",
        auth_url="https://www.pinterest.com/oauth/",
        token_url="https://api.pinterest.com/v5/oauth/token",
        scopes="boards:read,pins:read,pins:write,user_accounts:read",
        note="Pinterest developer app. Pins go to the account's first board.",
        posts="video pin to the first board",
    ),
    "x": Provider(
        key="x",
        label="X (Twitter)",
        auth_url="https://twitter.com/i/oauth2/authorize",
        token_url="https://api.twitter.com/2/oauth2/token",
        scopes="tweet.read tweet.write users.read offline.access",
        auth_params={"code_challenge": "challenge", "code_challenge_method": "plain"},
        note="X developer app (free tier). The free API cannot upload video, so Post shares the clip's public Kinder link.",
        posts="a post with the clip's public link (the free API cannot upload video)",
    ),
}


# ---------------------------------------------------------------- settings


def _get(db: Session, key: str) -> str:
    row = db.get(AppSetting, key)
    return row.value if row and row.value else ""


def _set(db: Session, key: str, value: str) -> None:
    row = db.get(AppSetting, key) or AppSetting(key=key, value="")
    row.value = value
    db.merge(row)


def provider(key: str) -> Provider:
    spec = PROVIDERS.get(key)
    if not spec:
        raise SocialError(f"Unknown platform: {key}")
    return spec


def creds(db: Session, key: str) -> tuple[str, str]:
    return _get(db, f"social.{key}.client_id"), _get(db, f"social.{key}.client_secret")


def set_creds(db: Session, key: str, client_id: str, client_secret: str) -> None:
    provider(key)
    _set(db, f"social.{key}.client_id", client_id.strip())
    if client_secret.strip():
        _set(db, f"social.{key}.client_secret", client_secret.strip())
    if not client_id.strip():
        # Clearing the id turns the platform off entirely.
        _set(db, f"social.{key}.client_secret", "")
    db.commit()


def configured(db: Session, key: str) -> bool:
    cid, secret = creds(db, key)
    return bool(cid and secret)


def _account_key(key: str, user_id: str) -> str:
    return f"social.{key}:{user_id}"


def account(db: Session, key: str, user_id: str) -> dict | None:
    raw = _get(db, _account_key(key, user_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def save_account(db: Session, key: str, user_id: str, data: dict) -> None:
    _set(db, _account_key(key, user_id), json.dumps(data))
    db.commit()


def disconnect(db: Session, key: str, user_id: str) -> None:
    _set(db, _account_key(key, user_id), "")
    db.commit()


# ---------------------------------------------------------------- OAuth


def begin(db: Session, key: str, user_id: str, redirect_uri: str) -> str:
    spec = provider(key)
    cid, _ = creds(db, key)
    if not cid:
        raise SocialError(f"{spec.label} is not set up yet. An admin needs to add its app keys.")
    state = secrets.token_urlsafe(24)
    _set(db, f"social.state.{key}:{user_id}", state)
    db.commit()
    params = {
        spec.id_param: cid,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": spec.scopes,
        "state": state,
        **spec.auth_params,
    }
    return spec.auth_url + "?" + urlencode(params)


def finish(db: Session, key: str, user_id: str, redirect_uri: str, code: str, state: str) -> dict:
    spec = provider(key)
    expected = _get(db, f"social.state.{key}:{user_id}")
    if not expected or not secrets.compare_digest(expected, state or ""):
        raise SocialError("That sign-in did not start here. Try Connect again.")
    _set(db, f"social.state.{key}:{user_id}", "")
    cid, secret = creds(db, key)
    data = {
        "code": code,
        spec.id_param: cid,
        spec.secret_param: secret,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }
    if key == "x":
        data["code_verifier"] = "challenge"
    response = requests.post(spec.token_url, data=data, timeout=20)
    if response.status_code != 200:
        raise SocialError(f"{spec.label} did not accept the sign-in: {_reason(response)}")
    tokens = response.json()
    if key == "tiktok" and tokens.get("error"):
        raise SocialError(f"{spec.label} did not accept the sign-in: {tokens.get('error_description') or tokens['error']}")
    acct = {
        "access_token": tokens.get("access_token", ""),
        "refresh_token": tokens.get("refresh_token", ""),
        "expires_at": time.time() + float(tokens.get("expires_in", 3600)) - 60,
        "name": "",
        "extra": {},
    }
    try:
        acct.update(_enrich(db, key, acct))
    except Exception:  # pragma: no cover - the name is a nicety
        log.debug("could not enrich the %s account", key, exc_info=True)
    save_account(db, key, user_id, acct)
    return acct


def _enrich(db: Session, key: str, acct: dict) -> dict:
    """Names and ids worth keeping: the page, the IG account, the board."""
    token = acct["access_token"]
    if key == "meta":
        pages = requests.get(
            "https://graph.facebook.com/v21.0/me/accounts",
            params={"fields": "id,name,access_token,instagram_business_account{id,username}", "access_token": token},
            timeout=20,
        ).json().get("data") or []
        if not pages:
            raise SocialError("That Facebook account manages no Page. Instagram posting needs a Page-linked Business account.")
        page = pages[0]
        ig = page.get("instagram_business_account") or {}
        return {
            "name": page.get("name", ""),
            "extra": {
                "page_id": page.get("id", ""),
                "page_token": page.get("access_token", ""),
                "ig_id": ig.get("id", ""),
                "ig_username": ig.get("username", ""),
            },
        }
    if key == "tiktok":
        info = requests.get(
            "https://open.tiktokapis.com/v2/user/info/",
            params={"fields": "display_name"},
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        ).json()
        return {"name": (info.get("data") or {}).get("user", {}).get("display_name", "")}
    if key == "linkedin":
        me = requests.get(
            "https://api.linkedin.com/v2/userinfo",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        ).json()
        return {"name": me.get("name", ""), "extra": {"person_urn": "urn:li:person:" + str(me.get("sub", ""))}}
    if key == "pinterest":
        user = requests.get(
            "https://api.pinterest.com/v5/user_account",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        ).json()
        boards = requests.get(
            "https://api.pinterest.com/v5/boards",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        ).json().get("items") or []
        return {
            "name": user.get("username", ""),
            "extra": {"board_id": boards[0]["id"] if boards else "", "board_name": boards[0]["name"] if boards else ""},
        }
    if key == "x":
        me = requests.get(
            "https://api.twitter.com/2/users/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=20,
        ).json()
        return {"name": (me.get("data") or {}).get("username", "")}
    return {}


def access_token(db: Session, key: str, user_id: str) -> dict:
    """The account with a live access token, refreshed when possible."""
    spec = provider(key)
    acct = account(db, key, user_id)
    if not acct:
        raise SocialError(f"Connect {spec.label} first.")
    if acct.get("access_token") and time.time() < float(acct.get("expires_at", 0)):
        return acct
    if not acct.get("refresh_token"):
        raise SocialError(f"The {spec.label} sign-in has expired. Connect again.")
    cid, secret = creds(db, key)
    response = requests.post(spec.token_url, data={
        "grant_type": "refresh_token",
        "refresh_token": acct["refresh_token"],
        spec.id_param: cid,
        spec.secret_param: secret,
    }, timeout=20)
    if response.status_code != 200:
        raise SocialError(f"The {spec.label} sign-in has expired. Connect again. ({_reason(response)})")
    tokens = response.json()
    acct["access_token"] = tokens.get("access_token", acct["access_token"])
    if tokens.get("refresh_token"):
        acct["refresh_token"] = tokens["refresh_token"]
    acct["expires_at"] = time.time() + float(tokens.get("expires_in", 3600)) - 60
    save_account(db, key, user_id, acct)
    return acct


# ---------------------------------------------------------------- posting


def post(
    db: Session,
    key: str,
    user_id: str,
    video: Path,
    title: str,
    description: str,
    share_url: str = "",
) -> dict:
    """Post one clip. Returns {platform, url?, detail} or raises SocialError."""
    if key != "x" and not video.exists():
        raise SocialError("Export the clip first.")
    if key == "meta":
        return _post_meta(db, user_id, video, title, description)
    if key == "tiktok":
        return _post_tiktok(db, user_id, video, title)
    if key == "linkedin":
        return _post_linkedin(db, user_id, video, title, description)
    if key == "pinterest":
        return _post_pinterest(db, user_id, video, title, description)
    if key == "x":
        return _post_x(db, user_id, title, share_url)
    raise SocialError(f"Posting to {key} is not wired yet.")


def _post_meta(db: Session, user_id: str, video: Path, title: str, description: str) -> dict:
    acct = access_token(db, "meta", user_id)
    extra = acct.get("extra") or {}
    page_id, page_token = extra.get("page_id"), extra.get("page_token")
    if not page_id or not page_token:
        raise SocialError("The connected Facebook account has no Page. Connect again with a Page.")
    with video.open("rb") as handle:
        fb = requests.post(
            f"https://graph-video.facebook.com/v21.0/{page_id}/videos",
            data={"access_token": page_token, "title": title[:100], "description": description[:5000]},
            files={"source": ("clip.mp4", handle, "video/mp4")},
            timeout=600,
        )
    if fb.status_code != 200:
        raise SocialError("Facebook refused the video: " + _reason(fb))
    result = {"platform": "meta", "detail": f"Posted to the {acct.get('name', 'Facebook')} Page", "url": ""}
    ig_id = extra.get("ig_id")
    if ig_id:
        try:
            result["detail"] += "; " + _post_instagram_reel(ig_id, page_token, video, description or title)
        except SocialError as error:
            result["detail"] += f"; Instagram: {error}"
    return result


def _post_instagram_reel(ig_id: str, token: str, video: Path, caption: str) -> str:
    size = video.stat().st_size
    init = requests.post(
        f"https://graph.facebook.com/v21.0/{ig_id}/media",
        data={"media_type": "REELS", "upload_type": "resumable", "caption": caption[:2200], "access_token": token},
        timeout=30,
    ).json()
    container, upload_uri = init.get("id"), init.get("uri")
    if not container or not upload_uri:
        raise SocialError(str(init.get("error", {}).get("message", "could not start the upload")))
    with video.open("rb") as handle:
        up = requests.post(
            upload_uri,
            headers={"Authorization": f"OAuth {token}", "offset": "0", "file_size": str(size)},
            data=handle,
            timeout=600,
        )
    if up.status_code != 200:
        raise SocialError("the Reel upload failed")
    done = requests.post(
        f"https://graph.facebook.com/v21.0/{ig_id}/media_publish",
        data={"creation_id": container, "access_token": token},
        timeout=60,
    )
    if done.status_code != 200:
        raise SocialError(str(done.json().get("error", {}).get("message", "publishing the Reel failed")))
    return "a Reel is on Instagram"


def _post_tiktok(db: Session, user_id: str, video: Path, title: str) -> dict:
    acct = access_token(db, "tiktok", user_id)
    size = video.stat().st_size
    init = requests.post(
        "https://open.tiktokapis.com/v2/post/publish/video/init/",
        headers={"Authorization": f"Bearer {acct['access_token']}", "Content-Type": "application/json"},
        json={
            "post_info": {"title": title[:150], "privacy_level": "SELF_ONLY"},
            "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1},
        },
        timeout=30,
    ).json()
    data = init.get("data") or {}
    upload_url = data.get("upload_url")
    if not upload_url:
        raise SocialError("TikTok refused the upload: " + str((init.get("error") or {}).get("message", init)))
    with video.open("rb") as handle:
        up = requests.put(
            upload_url,
            headers={"Content-Type": "video/mp4", "Content-Range": f"bytes 0-{size - 1}/{size}"},
            data=handle,
            timeout=600,
        )
    if up.status_code not in (200, 201):
        raise SocialError("The TikTok upload did not finish.")
    return {"platform": "tiktok", "url": "", "detail": "Sent to TikTok as a private video — open the TikTok app to review and publish it"}


def _post_linkedin(db: Session, user_id: str, video: Path, title: str, description: str) -> dict:
    acct = access_token(db, "linkedin", user_id)
    person = (acct.get("extra") or {}).get("person_urn", "")
    headers = {"Authorization": f"Bearer {acct['access_token']}"}
    register = requests.post(
        "https://api.linkedin.com/v2/assets?action=registerUpload",
        headers=headers,
        json={"registerUploadRequest": {
            "owner": person,
            "recipes": ["urn:li:digitalmediaRecipe:feedshare-video"],
            "serviceRelationships": [{"relationshipType": "OWNER", "identifier": "urn:li:userGeneratedContent"}],
        }},
        timeout=30,
    ).json()
    value = register.get("value") or {}
    asset = value.get("asset")
    upload = ((value.get("uploadMechanism") or {}).get(
        "com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest") or {}).get("uploadUrl")
    if not asset or not upload:
        raise SocialError("LinkedIn refused the upload: " + str(register))
    with video.open("rb") as handle:
        up = requests.put(upload, headers=headers, data=handle, timeout=600)
    if up.status_code not in (200, 201):
        raise SocialError("The LinkedIn upload did not finish.")
    share = requests.post(
        "https://api.linkedin.com/v2/ugcPosts",
        headers={**headers, "X-Restli-Protocol-Version": "2.0.0"},
        json={
            "author": person,
            "lifecycleState": "PUBLISHED",
            "specificContent": {"com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": (description or title)[:2900]},
                "shareMediaCategory": "VIDEO",
                "media": [{"status": "READY", "media": asset, "title": {"text": title[:200]}}],
            }},
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        },
        timeout=60,
    )
    if share.status_code not in (200, 201):
        raise SocialError("LinkedIn refused the share: " + _reason(share))
    return {"platform": "linkedin", "url": "", "detail": "Posted to LinkedIn"}


def _post_pinterest(db: Session, user_id: str, video: Path, title: str, description: str) -> dict:
    acct = access_token(db, "pinterest", user_id)
    board = (acct.get("extra") or {}).get("board_id")
    if not board:
        raise SocialError("The Pinterest account has no board yet — make one on Pinterest first.")
    headers = {"Authorization": f"Bearer {acct['access_token']}"}
    media = requests.post("https://api.pinterest.com/v5/media", headers=headers,
                          json={"media_type": "video"}, timeout=30).json()
    media_id, params = media.get("media_id"), media.get("upload_parameters") or {}
    upload_url = media.get("upload_url")
    if not media_id or not upload_url:
        raise SocialError("Pinterest refused the upload: " + str(media))
    with video.open("rb") as handle:
        up = requests.post(upload_url, data=params, files={"file": ("clip.mp4", handle, "video/mp4")}, timeout=600)
    if up.status_code not in (200, 201, 204):
        raise SocialError("The Pinterest upload did not finish.")
    pin = requests.post("https://api.pinterest.com/v5/pins", headers=headers, json={
        "board_id": board,
        "title": title[:100],
        "description": description[:500],
        "media_source": {"source_type": "video_id", "media_id": media_id, "cover_image_url": ""},
    }, timeout=60)
    if pin.status_code not in (200, 201):
        raise SocialError("Pinterest refused the pin: " + _reason(pin))
    pid = pin.json().get("id", "")
    return {"platform": "pinterest", "url": f"https://www.pinterest.com/pin/{pid}/" if pid else "", "detail": "Pinned"}


def _post_x(db: Session, user_id: str, title: str, share_url: str) -> dict:
    if not share_url:
        raise SocialError("Make a share link first (Copy link), then post to X.")
    acct = access_token(db, "x", user_id)
    text = (title[:200] + "\n" + share_url).strip()
    response = requests.post(
        "https://api.twitter.com/2/tweets",
        headers={"Authorization": f"Bearer {acct['access_token']}"},
        json={"text": text[:280]},
        timeout=30,
    )
    if response.status_code not in (200, 201):
        raise SocialError("X refused the post: " + _reason(response))
    tweet = (response.json().get("data") or {}).get("id", "")
    name = acct.get("name") or "i"
    return {"platform": "x", "url": f"https://x.com/{name}/status/{tweet}" if tweet else "", "detail": "Posted the clip's link on X"}


def _reason(response) -> str:
    try:
        data = response.json()
        err = data.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("status") or response.status_code)
        if isinstance(err, str):
            return err + (": " + data["error_description"] if data.get("error_description") else "")
        if data.get("message"):
            return str(data["message"])
    except Exception:
        pass
    return f"HTTP {response.status_code}"
