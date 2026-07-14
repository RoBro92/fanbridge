import json as _json
import urllib.request
from urllib.parse import urlsplit
from typing import Any, Dict, Optional


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


def http_get_json(url: str, timeout: float = 6.0) -> Optional[Dict[str, Any]]:
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
