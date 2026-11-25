"""Minimal stdlib HTTPS JSON client with safe error redaction."""

import json
import urllib.error
import urllib.request

USER_AGENT = "Fenn-AI-Usage/1.0.0"


class HttpApiError(Exception):
    """API or network error carrying only redacted, non-secret details."""

    def __init__(self, status, message, retry_after=None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.retry_after = retry_after


def _safe_error_detail(raw_bytes):
    try:
        text = raw_bytes.decode("utf-8", "replace").strip()
    except Exception:
        return ""
    if not text:
        return ""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            err = parsed.get("error")
            if isinstance(err, dict):
                return str(err.get("message") or err.get("code") or "")[:140]
            if err:
                return str(err)[:140]
            if parsed.get("message"):
                return str(parsed["message"])[:140]
    except ValueError:
        pass
    return text.splitlines()[0][:100]


def http_json(url, headers=None, method="GET", body=None, timeout=12):
    """Fetches JSON over HTTP(S), closing error streams cleanly and redacting secrets."""
    req_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        req_headers.update(headers)
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=payload, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        retry_after = None
        try:
            raw_ra = exc.headers.get("Retry-After") if exc.headers else None
            if raw_ra and raw_ra.strip().isdigit():
                retry_after = int(raw_ra.strip())
            detail = _safe_error_detail(exc.read())
        finally:
            # Close the underlying socket/file handle so Python does not emit ResourceWarning.
            exc.close()
        msg = f"HTTP {exc.code}" + (f": {detail}" if detail else "")
        raise HttpApiError(exc.code, msg, retry_after=retry_after) from None
    except urllib.error.URLError as exc:
        raise HttpApiError(0, f"Network error: {exc.reason}") from None
    except TimeoutError:
        raise HttpApiError(0, "Request timed out") from None
    except ValueError:
        raise HttpApiError(0, "Invalid JSON response") from None
