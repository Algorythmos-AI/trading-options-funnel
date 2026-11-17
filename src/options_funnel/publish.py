"""Send the funnel's health document to the dashboard's signed ingest (`/api/ingest`), as the research editions do.

- Signature: header `x-wt-signature: sha256=<hex HMAC-SHA256(secret, f"{ts}." + body)>`, `x-wt-timestamp: ts`,
  `x-wt-key-id: radar` (the research key; a new key id would be broader, see trading-lab-research ingest.ts).
- Secrets only from the environment: `RADAR_INGEST_SECRET`, and `VERCEL_AUTOMATION_BYPASS_SECRET` for the
  deployment protection. Never printed.
- Before sending, `/api/health` must say the site accepts `stocksdelta/funnel` v1 (`editions.funnel.accepts`); a site
  that does not know the kind would refuse the whole document.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_BASE = "https://lab.algorythmos.com"
KEY_ID = "radar"
TIMEOUT_S = 20


class PublishError(RuntimeError):
    """The document was not sent or not accepted."""


def sign(secret: str, ts: int, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class Sent:
    status: int
    outcome: str


def _headers(bypass: str | None) -> dict[str, str]:
    return {"x-vercel-protection-bypass": bypass} if bypass else {}


def accepts(base: str, bypass: str | None) -> int | None:
    """The newest funnel format the site accepts, or None when it does not know the kind."""
    request = urllib.request.Request(f"{base}/api/health", headers=_headers(bypass))
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            health: Any = json.load(response)
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise PublishError(f"health check failed: {type(error).__name__}") from error
    value = ((health.get("editions") or {}).get("funnel") or {}).get("accepts") if isinstance(health, dict) else None
    return value if isinstance(value, int) else None


def send(doc: dict[str, Any], base: str = DEFAULT_BASE, now: float | None = None) -> Sent:
    """POST the document once. 200 with stored or duplicate is success; anything else raises PublishError."""
    secret = os.environ.get("RADAR_INGEST_SECRET")
    if not secret:
        raise PublishError("RADAR_INGEST_SECRET is not set")
    bypass = os.environ.get("VERCEL_AUTOMATION_BYPASS_SECRET")
    accepted = accepts(base, bypass)
    if accepted is None or accepted < doc["schema_version"]:
        raise PublishError(f"the site does not accept stocksdelta/funnel v{doc['schema_version']} (accepts {accepted})")
    body = json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode()
    ts = int(now if now is not None else time.time())
    headers = {
        "content-type": "application/json",
        "x-wt-timestamp": str(ts),
        "x-wt-signature": sign(secret, ts, body),
        "x-wt-key-id": KEY_ID,
        **_headers(bypass),
    }
    request = urllib.request.Request(f"{base}/api/ingest", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            answer: Any = json.load(response)
            return Sent(response.status, str(answer.get("status", "stored")) if isinstance(answer, dict) else "stored")
    except urllib.error.HTTPError as error:
        raise PublishError(f"HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise PublishError(f"send failed: {type(error).__name__}") from error
