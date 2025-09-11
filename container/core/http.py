import json as _json
import urllib.request
from typing import Any, Dict, Optional

def http_get_json(url: str, timeout: float = 6.0) -> Optional[Dict[str, Any]]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "fanbridge/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if 200 <= resp.status < 300:
                data = resp.read()
                return _json.loads(data.decode("utf-8", errors="ignore"))
    except Exception:
        return None
    return None

