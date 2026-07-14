import json as _json
import re
import urllib.request
from urllib.parse import urlsplit
from typing import Any, Optional


_FIRMWARE_RELEASE_PATH_RE = re.compile(
    r"^/RoBroLabs/fanbridge/releases/download/"
    r"fw-v[0-9]+\.[0-9]+\.[0-9]+/"
    r"fanbridge-rp2040-[0-9]+\.[0-9]+\.[0-9]+\.uf2(?:\.sha256)?$"
)
_GITHUB_ASSET_HOSTS = {
    "release-assets.githubusercontent.com",
    "objects.githubusercontent.com",
}


def _allowed_api_url(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        return (
            parsed.scheme == "https"
            and parsed.hostname == "api.github.com"
            and parsed.port in (None, 443)
            and not parsed.username
            and not parsed.password
        )
    except (TypeError, ValueError):
        return False


def _allowed_firmware_download_url(url: str, *, redirected: bool = False) -> bool:
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.port not in (None, 443)
            or parsed.username
            or parsed.password
        ):
            return False
        if redirected:
            return parsed.hostname in _GITHUB_ASSET_HOSTS or (
                parsed.hostname == "github.com"
                and bool(_FIRMWARE_RELEASE_PATH_RE.fullmatch(parsed.path))
            )
        return (
            parsed.hostname == "github.com"
            and not parsed.query
            and not parsed.fragment
            and bool(_FIRMWARE_RELEASE_PATH_RE.fullmatch(parsed.path))
        )
    except (TypeError, ValueError):
        return False


def http_get_json(url: str, timeout: float = 6.0) -> Optional[Any]:
    """Fetch a small JSON object from the fixed GitHub API trust boundary."""
    if not _allowed_api_url(url):
        return None
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "fanbridge/1.0",
        })
        # The URL and redirect destination are both constrained above/below;
        # urllib is used here without permitting arbitrary schemes or hosts.
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            if 200 <= resp.status < 300 and _allowed_api_url(resp.geturl()):
                declared = int(resp.headers.get("Content-Length", "0") or "0")
                if declared > 262144:
                    return None
                data = resp.read(262145)
                if len(data) > 262144:
                    return None
                return _json.loads(data.decode("utf-8", errors="ignore"))
    except (OSError, ValueError, UnicodeError, _json.JSONDecodeError):
        return None
    return None


def http_get_firmware_asset(url: str, *, max_bytes: int, timeout: float = 15.0) -> bytes | None:
    """Download a bounded asset from FanBridge's fixed GitHub release path."""
    if max_bytes < 1 or not _allowed_firmware_download_url(url):
        return None
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/octet-stream",
            "User-Agent": "fanbridge/1.0",
        })
        # The initial release path and final GitHub asset host are constrained.
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec B310
            if not (200 <= resp.status < 300):
                return None
            if not _allowed_firmware_download_url(resp.geturl(), redirected=True):
                return None
            declared = int(resp.headers.get("Content-Length", "0") or "0")
            if declared > max_bytes:
                return None
            data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                return None
            return data
    except (OSError, ValueError):
        return None
