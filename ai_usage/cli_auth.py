"""Local AI CLI credential discovery, safe CLI refresh, and Antigravity statusLine setup.

Never writes directly to third-party OAuth token files; CLI session refreshes
are delegated strictly to official vendor binaries and settings updates are
strictly opt-in.
"""

import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_REFRESH_LOCK = threading.Lock()
_LAST_REFRESH = {}


def _read_json_file(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_json_atomic(path, payload):
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, dest)


def decode_jwt_payload(token):
    """Decodes an unverified JWT claims object for expiry and account-id lookup."""
    if not token or not isinstance(token, str):
        return {}
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    padded = payload + "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        claims = json.loads(raw.decode("utf-8"))
        return claims if isinstance(claims, dict) else {}
    except (ValueError, UnicodeDecodeError):
        return {}


def extract_chatgpt_jwt_meta(token):
    """Extracts expiry status, chatgpt_account_id, and plan_type from an OpenAI JWT."""
    claims = decode_jwt_payload(token)
    exp = claims.get("exp")
    expired = isinstance(exp, (int, float)) and exp < (time.time() + 30)
    auth_ns = claims.get("https://api.openai.com/auth")
    account_id = auth_ns.get("chatgpt_account_id") if isinstance(auth_ns, dict) else None
    plan = auth_ns.get("chatgpt_plan_type") if isinstance(auth_ns, dict) else None
    return {"expired": expired, "account_id": account_id, "plan": plan}


def _claude_cli_path():
    return Path.home() / ".claude" / ".credentials.json"


def _codex_cli_path():
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        candidate = Path(codex_home) / "auth.json"
        if candidate.exists():
            return candidate
    return Path.home() / ".codex" / "auth.json"


def _gemini_cli_path():
    custom = os.environ.get("GEMINI_CLI_HOME")
    if custom:
        for candidate in (
            Path(custom) / ".gemini" / "oauth_creds.json",
            Path(custom) / "oauth_creds.json",
        ):
            if candidate.exists():
                return candidate
    return Path.home() / ".gemini" / "oauth_creds.json"


def _gcloud_adc_path():
    cfg = os.environ.get("CLOUDSDK_CONFIG")
    if cfg:
        candidate = Path(cfg) / "application_default_credentials.json"
        if candidate.exists():
            return candidate
    appdata = os.environ.get("APPDATA")
    if appdata:
        win_adc = Path(appdata) / "gcloud" / "application_default_credentials.json"
        if win_adc.exists():
            return win_adc
    return Path.home() / ".config" / "gcloud" / "application_default_credentials.json"


def _parse_iso_ts(raw):
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    if "." in s:
        # Python < 3.11 dislikes >6 fractional subsecond digits such as .3696229Z.
        head, tail = s.split(".", 1)
        tz_idx = max(tail.find("+"), tail.find("-"))
        if tz_idx == -1:
            s = f"{head}.{tail[:6]}"
        else:
            s = f"{head}.{tail[:6]}{tail[tz_idx:]}"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _win32_read_credential(target_name):
    """Reads a Generic credential string from Windows Credential Manager read-only."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        import ctypes.wintypes as w

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ("Flags", w.DWORD),
                ("Type", w.DWORD),
                ("TargetName", w.LPWSTR),
                ("Comment", w.LPWSTR),
                ("LastWritten", w.FILETIME),
                ("CredentialBlobSize", w.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", w.DWORD),
                ("AttributeCount", w.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", w.LPWSTR),
                ("UserName", w.LPWSTR),
            ]

        adv = ctypes.windll.advapi32
        adv.CredReadW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
        adv.CredReadW.restype = w.BOOL
        adv.CredFree.argtypes = [ctypes.c_void_p]

        pcred = ctypes.POINTER(CREDENTIAL)()
        if not adv.CredReadW(target_name, 1, 0, ctypes.byref(pcred)):
            return None
        try:
            blob_size = pcred.contents.CredentialBlobSize
            if not blob_size or not pcred.contents.CredentialBlob:
                return None
            raw = bytes(pcred.contents.CredentialBlob[:blob_size])
        finally:
            adv.CredFree(pcred)
        for enc in ("utf-8", "utf-16-le"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return None
    except (OSError, AttributeError, ValueError):
        return None


def read_claude_cli():
    """Reads Claude Code's OAuth credentials if present."""
    data = _read_json_file(_claude_cli_path())
    if not isinstance(data, dict):
        return None
    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict) or not oauth.get("accessToken"):
        return None
    exp_ms = oauth.get("expiresAt")
    expired = isinstance(exp_ms, (int, float)) and (exp_ms / 1000.0) < (time.time() + 30)
    return {
        "access_token": str(oauth["accessToken"]),
        "plan": oauth.get("subscriptionType"),
        "expired": expired,
    }


def read_codex_cli():
    """Reads Codex CLI credentials (~/.codex/auth.json or $CODEX_HOME/auth.json)."""
    data = _read_json_file(_codex_cli_path())
    if not isinstance(data, dict):
        return None
    tokens = data.get("tokens")
    if isinstance(tokens, dict) and tokens.get("access_token"):
        token = str(tokens["access_token"])
        jwt_meta = extract_chatgpt_jwt_meta(token)
        return {
            "access_token": token,
            "account_id": tokens.get("account_id") or jwt_meta["account_id"],
            "plan": jwt_meta["plan"],
            "expired": jwt_meta["expired"],
            "api_key": None,
        }
    if data.get("OPENAI_API_KEY"):
        return {
            "access_token": None,
            "account_id": None,
            "plan": None,
            "expired": False,
            "api_key": str(data["OPENAI_API_KEY"]),
        }
    return None


def _run_silent_refresh(key, cmd, min_cooldown=60):
    """Runs an official CLI read command with lock + cooldown so its own token refresh updates disk safely."""
    with _REFRESH_LOCK:
        now = time.monotonic()
        if now - _LAST_REFRESH.get(key, 0.0) < min_cooldown:
            return False
        _LAST_REFRESH[key] = now
    try:
        res = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=_NO_WINDOW,
            check=False,
        )
        return res.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def refresh_claude_cli(min_cooldown=60):
    """Refreshes an expired Claude Code session via `claude auth status` without touching single-use tokens."""
    exe = shutil.which("claude")
    if not exe:
        return None
    _run_silent_refresh("claude", [exe, "auth", "status", "--text"], min_cooldown=min_cooldown)
    return read_claude_cli()


def refresh_antigravity_cli(min_cooldown=60):
    """Refreshes an expired Antigravity OAuth session via `agy models` using agy's own keyring refresh."""
    exe = _agy_binary_path()
    if not exe:
        return None
    _run_silent_refresh("antigravity", [exe, "models"], min_cooldown=min_cooldown)
    return read_antigravity_auth()


def _parse_gemini_oauth_dict(data, label="Gemini CLI"):
    if not isinstance(data, dict) or not data.get("access_token"):
        return None
    exp_ms = data.get("expiry_date")
    if isinstance(exp_ms, (int, float)):
        expired = (exp_ms / 1000.0) < (time.time() + 30)
    else:
        exp_ts = _parse_iso_ts(data.get("expiry") or data.get("expires_at"))
        expired = exp_ts is not None and exp_ts < (time.time() + 30)
    project = (
        os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GOOGLE_CLOUD_PROJECT_ID")
        or data.get("project_id")
    )
    return {
        "source": "oauth",
        "access_token": str(data["access_token"]),
        "refresh_token": data.get("refresh_token"),
        "client_id": data.get("client_id"),
        "client_secret": data.get("client_secret"),
        "project": project.strip() if isinstance(project, str) and project.strip() else None,
        "expired": expired,
        "label": label,
    }


def read_gemini_cli():
    """Reads Gemini CLI OAuth (file or OS keyring), Google Cloud ADC, or GEMINI_API_KEY env credentials."""
    parsed = _parse_gemini_oauth_dict(_read_json_file(_gemini_cli_path()), label="Gemini CLI")
    if parsed:
        return parsed

    for target in ("gemini-cli-oauth", "gemini:gemini-cli"):
        raw = _win32_read_credential(target)
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            tok = obj.get("token") if isinstance(obj.get("token"), dict) else obj
            parsed = _parse_gemini_oauth_dict(tok, label="Gemini CLI (keyring)")
            if parsed:
                return parsed

    adc = _read_json_file(_gcloud_adc_path())
    if isinstance(adc, dict) and adc.get("refresh_token") and adc.get("client_id") and adc.get("client_secret"):
        proj = adc.get("quota_project_id") or os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GOOGLE_CLOUD_PROJECT_ID")
        return {
            "source": "adc",
            "client_id": str(adc["client_id"]),
            "client_secret": str(adc["client_secret"]),
            "refresh_token": str(adc["refresh_token"]),
            "quota_project": proj.strip() if isinstance(proj, str) and proj.strip() else None,
            "expired": False,
            "label": "Google Cloud ADC",
        }
    env_key = (os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "").strip()
    if env_key:
        return {
            "source": "env_key",
            "api_key": env_key,
            "expired": False,
            "label": "GEMINI_API_KEY",
        }
    return None


def _agy_settings_path():
    return Path.home() / ".gemini" / "antigravity-cli" / "settings.json"


def _agy_binary_path():
    which = shutil.which("agy")
    if which:
        return which
    local = os.environ.get("LOCALAPPDATA")
    candidates = [
        Path.home() / ".gemini" / "bin" / "agy.exe",
        Path.home() / ".antigravity" / "bin" / "agy.exe",
    ]
    if local:
        candidates.append(Path(local) / "agy" / "bin" / "agy.exe")
    for p in candidates:
        if p.is_file():
            return str(p)
    return None


def _agy_binary_exists():
    return _agy_binary_path() is not None


def read_antigravity_auth():
    """Reads Antigravity CLI/App OAuth credentials from the OS keyring (read-only)."""
    raw = _win32_read_credential("gemini:antigravity")
    if raw:
        try:
            obj = json.loads(raw)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            tok = obj.get("token")
            if isinstance(tok, dict) and tok.get("access_token"):
                exp_ts = _parse_iso_ts(tok.get("expiry") or tok.get("expires_at"))
                expired = exp_ts is not None and exp_ts < (time.time() + 30)
                auth_method = str(obj.get("auth_method") or "consumer")
                return {
                    "access_token": str(tok["access_token"]),
                    "auth_method": auth_method,
                    "expired": expired,
                    "label": f"Antigravity ({auth_method})",
                }
    return None


def read_antigravity_local_meta():
    """Reads non-secret local Antigravity settings such as active model and install presence."""
    settings = _read_json_file(_agy_settings_path())
    model_name = settings.get("model") if isinstance(settings, dict) else None
    cli_dir = Path.home() / ".gemini" / "antigravity-cli"
    app_dir = Path.home() / ".gemini" / "antigravity"
    installed = _agy_binary_exists() or cli_dir.is_dir() or app_dir.is_dir()
    return {"installed": installed, "model": str(model_name) if model_name else None}


def read_antigravity_status(data_dir):
    """Reads the latest documented agy statusLine quota snapshot if available."""
    if not data_dir:
        return None
    data = _read_json_file(Path(data_dir) / "antigravity_status.json")
    return data if isinstance(data, dict) else None


_STATUSLINE_MARKERS = ("agy_statusline.py", "ai_usage_statusline.cmd")


def _is_our_statusline(cmd):
    text = str(cmd or "")
    return any(m in text for m in _STATUSLINE_MARKERS)


def _agy_wrapper_path():
    return _agy_settings_path().parent / "ai_usage_statusline.cmd"


def _win32_short_path(path_obj):
    """Returns Windows 8.3 space-free path if available so cmd /c needs no quotes."""
    s = str(path_obj)
    if sys.platform != "win32" or " " not in s:
        return s
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(512)
        n = ctypes.windll.kernel32.GetShortPathNameW(s, buf, len(buf))
        if 0 < n < len(buf):
            return buf.value
    except (OSError, AttributeError):
        pass
    return s


def antigravity_statusline_state():
    """Returns 'enabled', 'custom', or 'off' for ~/.gemini/antigravity-cli/settings.json."""
    settings = _read_json_file(_agy_settings_path())
    if not isinstance(settings, dict):
        return "off"
    sl = settings.get("statusLine")
    if not isinstance(sl, dict):
        return "off"
    cmd = str(sl.get("command") or "")
    if _is_our_statusline(cmd):
        return "enabled" if sl.get("enabled", True) else "off"
    return "custom" if cmd else "off"


def enable_antigravity_statusline(data_dir):
    """Opts into Antigravity CLI statusLine telemetry without clobbering existing user configs."""
    cfg_path = _agy_settings_path()
    settings = _read_json_file(cfg_path)
    if settings is None:
        settings = {}
    elif not isinstance(settings, dict):
        return False, "Cannot parse ~/.gemini/antigravity-cli/settings.json"
    existing = settings.get("statusLine")
    if isinstance(existing, dict) and existing.get("command") and not _is_our_statusline(existing["command"]):
        return False, "Custom statusLine already configured in Antigravity settings.json"

    script_path = Path(__file__).resolve().parent / "agy_statusline.py"
    if sys.platform == "win32":
        # Go's statusline_runner calls exec.Command("cmd", "/c", cmd) which backslash-escapes double quotes.
        # A quote-free .cmd wrapper keeps paths safe across spaces without any quotes in settings.json.
        wrapper = _agy_wrapper_path()
        try:
            wrapper.parent.mkdir(parents=True, exist_ok=True)
            bat = f'@echo off\n"{sys.executable}" "{script_path}" --data-dir "{data_dir}" --quiet\n'
            wrapper.write_text(bat, encoding="utf-8")
        except OSError as exc:
            return False, f"Could not write statusLine wrapper: {exc}"
        cmd = _win32_short_path(wrapper)
    else:
        cmd = f'"{sys.executable}" "{script_path}" --data-dir "{data_dir}" --quiet'

    settings["statusLine"] = {
        "type": "command",
        "command": cmd,
        "stack_with_default": True,
        "enabled": True,
    }
    try:
        _write_json_atomic(cfg_path, settings)
    except OSError as exc:
        return False, f"Could not update Antigravity settings.json: {exc}"
    return True, "Enabled agy statusLine sync — next 'agy' session streams live quotas"


def repair_legacy_antigravity_statusline(data_dir):
    """Upgrades an already-enabled quoted or stale AI Usage hook on Windows cleanly."""
    if sys.platform != "win32" or not data_dir:
        return False
    if os.path.normcase(os.path.abspath(str(data_dir))).startswith(
        os.path.normcase(os.path.abspath(tempfile.gettempdir()))
    ):
        return False
    settings = _read_json_file(_agy_settings_path())
    if not isinstance(settings, dict):
        return False
    sl = settings.get("statusLine")
    if not isinstance(sl, dict) or not sl.get("enabled", True):
        return False
    cmd = str(sl.get("command") or "")
    if "agy_statusline.py" in cmd and "ai_usage_statusline.cmd" not in cmd:
        ok, _ = enable_antigravity_statusline(data_dir)
        return ok
    if "ai_usage_statusline.cmd" in cmd:
        wrapper = _agy_wrapper_path()
        expected = str(Path(__file__).resolve().parent / "agy_statusline.py")
        try:
            cur = wrapper.read_text(encoding="utf-8") if wrapper.is_file() else ""
        except OSError:
            cur = ""
        if expected not in cur or str(data_dir) not in cur:
            ok, _ = enable_antigravity_statusline(data_dir)
            return ok
    return False


def disable_antigravity_statusline():
    """Removes the AI Usage statusLine hook cleanly while keeping other Antigravity settings."""
    cfg_path = _agy_settings_path()
    settings = _read_json_file(cfg_path)
    if not isinstance(settings, dict):
        return False, "No Antigravity settings.json found"
    sl = settings.get("statusLine")
    if not (isinstance(sl, dict) and _is_our_statusline(sl.get("command"))):
        return False, "AI Usage statusLine sync is not active"
    settings.pop("statusLine", None)
    try:
        _write_json_atomic(cfg_path, settings)
        wrapper = _agy_wrapper_path()
        if wrapper.exists():
            wrapper.unlink()
    except OSError as exc:
        return False, f"Could not update Antigravity settings.json: {exc}"
    return True, "Disabled agy statusLine sync"


def _read_gh_hosts_token():
    candidates = [Path.home() / ".config" / "gh" / "hosts.yml"]
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "GitHub CLI" / "hosts.yml")
    for p in candidates:
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("oauth_token:"):
                tok = stripped.split(":", 1)[1].strip().strip("'\"")
                if tok:
                    return tok
    return None


def read_gh_cli_token():
    """Returns the active GitHub / Copilot CLI token via env vars, `gh auth token`, or hosts.yml."""
    for env_var in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        tok = (os.environ.get(env_var) or "").strip()
        if tok:
            return tok
    exe = shutil.which("gh")
    if exe:
        try:
            res = subprocess.run(
                [exe, "auth", "token"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=4,
                creationflags=_NO_WINDOW,
                check=False,
            )
            tok = res.stdout.strip()
            if res.returncode == 0 and tok:
                return tok
        except (OSError, subprocess.SubprocessError):
            pass
    return _read_gh_hosts_token()


def detect_local_clis(data_dir=None):
    """Detects which AI CLIs are installed/signed in on this PC without exposing secrets."""
    claude = read_claude_cli()
    codex = read_codex_cli()
    gemini = read_gemini_cli()
    agy_auth = read_antigravity_auth()
    agy_meta = read_antigravity_local_meta()
    agy_status = read_antigravity_status(data_dir)
    agy_found = bool(agy_auth or agy_meta["installed"] or agy_status)
    agy_label = (agy_auth or {}).get("label") or ("Antigravity CLI (agy)" if agy_meta["installed"] else "Antigravity")
    gh_found = read_gh_cli_token() is not None if (
        shutil.which("gh") or any(os.environ.get(v) for v in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"))
    ) else (_read_gh_hosts_token() is not None)
    return {
        "claude": {"found": claude is not None, "label": (claude or {}).get("plan") or "Claude Code"},
        "chatgpt": {"found": codex is not None, "label": "Codex CLI"},
        "gemini": {"found": gemini is not None, "label": (gemini or {}).get("label") or "Gemini CLI"},
        "antigravity": {"found": agy_found, "label": agy_label},
        "copilot": {"found": gh_found, "label": "GitHub CLI (gh)"},
    }
