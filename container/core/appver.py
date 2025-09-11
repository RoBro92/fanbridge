from typing import Tuple, Optional
from .http import http_get_json

def parse_semver_tuple(v: str) -> Tuple:
    try:
        core = str(v or '').strip().lstrip('vV')
        parts = core.split('-')[0]
        nums = [int(x) for x in parts.split('.') if x.isdigit()]
        return tuple(nums + [0] * (3 - len(nums)))
    except Exception:
        return (0, 0, 0)

def latest_github_release(repo: str, timeout: float = 6.0) -> Optional[str]:
    url = f"https://api.github.com/repos/{repo}/releases/latest"
    data = http_get_json(url, timeout=timeout)
    if isinstance(data, dict):
        tag = data.get('tag_name') or data.get('name')
        if isinstance(tag, str) and tag.strip():
            return tag.strip()
    return None

