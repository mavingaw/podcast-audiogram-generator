from __future__ import annotations

import ipaddress
import socket
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

import feedparser

from app.core.config import settings

MAX_RSS_BYTES = 5 * 1024 * 1024


class RssFetchError(ValueError):
    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


def parse_feed_url(url: str) -> feedparser.FeedParserDict:
    _validate_feed_url(url)
    opener = build_opener(_ValidatingRedirectHandler)
    request = Request(url, headers={"User-Agent": "PodcastAudiogramStudio/0.1"})
    try:
        with opener.open(request, timeout=15) as response:
            body = response.read(MAX_RSS_BYTES + 1)
    except HTTPError as exc:
        raise RssFetchError(f"Feed returned HTTP {exc.code}", status_code=400) from exc
    except URLError as exc:
        raise RssFetchError("Feed could not be fetched", status_code=400) from exc

    if len(body) > MAX_RSS_BYTES:
        raise RssFetchError("Feed is too large", status_code=413)
    parsed = feedparser.parse(body)
    if parsed.bozo and not parsed.entries:
        raise RssFetchError("Feed could not be parsed", status_code=400)
    return parsed


class _ValidatingRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        _validate_feed_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _validate_feed_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise RssFetchError("Only HTTP(S) feeds are supported")
    if not parsed.hostname:
        raise RssFetchError("Feed URL must include a host")
    if parsed.username or parsed.password:
        raise RssFetchError("Feed URL credentials are not supported")
    if settings.allow_private_rss:
        return

    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise RssFetchError("Feed host could not be resolved") from exc

    for family, _, _, _, sockaddr in addresses:
        if family not in {socket.AF_INET, socket.AF_INET6}:
            continue
        address = ipaddress.ip_address(sockaddr[0])
        if _is_blocked_address(address):
            raise RssFetchError("Feed host resolves to a private or local address", status_code=403)


def _is_blocked_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(
        (
            address.is_loopback,
            address.is_link_local,
            address.is_private,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )
