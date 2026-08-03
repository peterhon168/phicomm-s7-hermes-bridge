"""One-shot, privacy-preserving mitmproxy addon for Pai Health capture.

This addon intentionally does not save flows and never prints cookie, token,
authorization, account, device-id, or arbitrary string values. It reports only
the request/response route, status, JSON shape, and numeric fields whose names
look like body measurements. Run it only against the user's own phone/account
and stop it after the single history/data-claim request has been observed.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from mitmproxy import ctx, http


_MEASURE_KEY = re.compile(
    r"(?:weight|body.?fat|fat.?rate|bfr|bmi|water|protein|muscle|bone|metabol|visceral|measure|weigh)",
    re.IGNORECASE,
)
_SENSITIVE_KEY = re.compile(
    r"(?:token|secret|password|passwd|cookie|authorization|auth|session|phone|email|account|uid|user.?id|device.?id|mac|sn)",
    re.IGNORECASE,
)


def _hash(value: Any) -> str:
    data = str(value).encode("utf-8", "replace")
    return hashlib.sha256(data).hexdigest()[:12]


def _safe_url(url: str) -> str:
    parts = urlsplit(url)
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        query.append((key, "<hash:" + _hash(value) + ">"))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))


def _measurement_values(value: Any, path: str = "") -> dict[str, Any]:
    """Return only likely measurement values; all other scalars are omitted."""

    found: dict[str, Any] = {}
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if _MEASURE_KEY.search(str(key)) and not _SENSITIVE_KEY.search(str(key)):
                if isinstance(child, (int, float)) and not isinstance(child, bool):
                    found[child_path] = child
                elif isinstance(child, str) and len(child) <= 32:
                    # Keep numeric strings only. Do not print arbitrary text.
                    try:
                        found[child_path] = float(child)
                    except ValueError:
                        pass
            found.update(_measurement_values(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value[:10]):
            found.update(_measurement_values(child, f"{path}[{index}]"))
    return found


def _shape(value: Any, depth: int = 0) -> Any:
    """Describe JSON structure without exposing arbitrary data."""

    if depth > 4:
        return "<depth>"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, child in list(value.items())[:100]:
            key_text = str(key)
            if _SENSITIVE_KEY.search(key_text):
                result[key_text] = "<redacted>"
            elif isinstance(child, (dict, list)):
                result[key_text] = _shape(child, depth + 1)
            elif _MEASURE_KEY.search(key_text):
                result[key_text] = "<measurement>"
            else:
                result[key_text] = type(child).__name__
        return result
    if isinstance(value, list):
        return {"list": len(value), "item": _shape(value[0], depth + 1) if value else None}
    return type(value).__name__


def _json_summary(message: http.Message) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    content_type = message.headers.get("content-type", "")
    text = message.get_text(strict=False)
    if not text or "json" not in content_type.lower():
        return None, {"content_type": content_type, "text_length": len(text or "")}, {}
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None, {"content_type": content_type, "text_length": len(text)}, {}
    return value, {"content_type": content_type, "shape": _shape(value)}, _measurement_values(value)


def _emit(event: dict[str, Any]) -> None:
    # Print to stdout instead of writing a flow file. This remains visible even
    # when mitmdump is run in quiet mode; only sanitized target events reach it.
    print("PAI_CAPTURE " + json.dumps(event, ensure_ascii=False, separators=(",", ":")), flush=True)


def _is_target(flow: http.HTTPFlow) -> bool:
    host = flow.request.host.lower().rstrip(".")
    return host == "laisitech.com" or host.endswith(".laisitech.com")


def request(flow: http.HTTPFlow) -> None:
    if not _is_target(flow):
        return
    body, summary, measurements = _json_summary(flow.request)
    _emit(
        {
            "event": "request",
            "method": flow.request.method,
            "url": _safe_url(flow.request.pretty_url),
            "headers": {
                "content_type": flow.request.headers.get("content-type", ""),
                "authorization_present": bool(flow.request.headers.get("authorization")),
                "cookie_present": bool(flow.request.headers.get("cookie")),
                # Header names are useful for reproducing the request shape;
                # values are never emitted (especially token/sign/user IDs).
                "header_names": sorted(
                    str(name).lower() for name in flow.request.headers.keys()
                ),
            },
            "body": summary,
            "measurement_fields": measurements,
        }
    )


def response(flow: http.HTTPFlow) -> None:
    if not _is_target(flow):
        return
    body, summary, measurements = _json_summary(flow.response)
    _emit(
        {
            "event": "response",
            "method": flow.request.method,
            "url": _safe_url(flow.request.pretty_url),
            "status": flow.response.status_code,
            "body": summary,
            "measurement_fields": measurements,
        }
    )
