"""Normalised data structures, provider metadata, setup guides, and formatting helpers."""

from datetime import datetime, timezone
import time

PROVIDER_META = {
    "claude": {
        "name": "Claude",
        "badge": "C",
        "icon": "asterisk",
        "pet_icon": "alien",
        "brand_color": "#D97757",
        "url": "https://claude.ai/settings/usage",
        "hint": "Paste Claude OAuth token, Admin key (sk-ant-admin…), or API key",
        "steps": [
            "1. For 5h/7d limits: sign in via Claude Code ('claude' -> /login), then click Link.",
            "2. For API spend: paste an Admin key (sk-ant-admin…) from Anthropic Console.",
            "3. Note: 'setup-token' tokens lack user:profile scope and return 403 on usage.",
        ],
        "links": [
            ("usage", "Usage page", "https://claude.ai/settings/usage"),
            ("console", "Admin keys", "https://console.anthropic.com/settings/keys"),
            ("docs", "Claude Code", "https://docs.anthropic.com/en/docs/claude-code/overview"),
        ],
    },
    "chatgpt": {
        "name": "ChatGPT",
        "badge": "O",
        "icon": "spiral",
        "brand_color": "#10A37F",
        "url": "https://chatgpt.com/codex/settings/usage",
        "hint": "Paste ChatGPT bearer JWT or OpenAI key (sk-…)",
        "steps": [
            "1. For Plus/Pro limits: run 'codex login' once, then click Link Codex CLI.",
            "2. Or paste a ChatGPT bearer JWT (account ID is detected automatically).",
            "3. Admin keys (sk-admin…) show monthly spend; sk-proj-… verifies status.",
        ],
        "links": [
            ("usage", "Codex usage", "https://chatgpt.com/codex/settings/usage"),
            ("cli", "Codex CLI", "https://developers.openai.com/codex/cli"),
            ("keys", "API keys", "https://platform.openai.com/api-keys"),
        ],
    },
    "gemini": {
        "name": "Gemini",
        "badge": "G",
        "icon": "star-four",
        "brand_color": "#4E8DF5",
        "url": "https://aistudio.google.com/usage",
        "hint": "Paste AI Studio API key (AIza…) or link Gemini CLI / gcloud ADC",
        "steps": [
            "1. Note: Google migrated individual Gemini CLI quotas to Antigravity (use Antigravity tab for agy).",
            "2. For Cloud Code Assist / Gemini CLI or gcloud ADC: sign in locally and click Link.",
            "3. Or paste an AI Studio API key (AIza…) / set GEMINI_API_KEY.",
        ],
        "links": [
            ("usage", "AI Studio usage", "https://aistudio.google.com/usage"),
            ("keys", "Get API key", "https://aistudio.google.com/apikey"),
            ("cli", "Gemini CLI", "https://github.com/google-gemini/gemini-cli"),
        ],
    },
    "antigravity": {
        "name": "Antigravity",
        "badge": "A",
        "icon": "planet",
        "brand_color": "#5B6EFF",
        "url": "https://antigravity.google/docs/cli/commands/usage",
        "hint": "Optional GCP Project ID for Enterprise / Cloud Assist (leave blank for auto)",
        "steps": [
            "1. Sign in via 'agy' CLI or Antigravity App (auto-detected via system keyring).",
            "2. Click Enable statusLine sync below so 'agy' streams live % quotas into Fenn.",
            "3. Or leave linked for session status and quick-open usage docs.",
        ],
        "links": [
            ("plans", "Plans", "https://antigravity.google/docs/plans"),
            ("usage", "Usage cmd", "https://antigravity.google/docs/cli/commands/usage"),
            ("statusline", "StatusLine docs", "https://antigravity.google/docs/cli/statusline"),
        ],
    },
    "openrouter": {
        "name": "OpenRouter",
        "badge": "R",
        "icon": "plug",
        "brand_color": "#8B5CF6",
        "url": "https://openrouter.ai/settings/credits",
        "hint": "Paste OpenRouter API key (sk-or-v1-…)",
        "steps": [
            "1. Create or copy an API key (sk-or-v1-…) on OpenRouter.",
            "2. Paste below to track key spend caps, credits, and daily/monthly spend.",
        ],
        "links": [
            ("keys", "Get API key", "https://openrouter.ai/settings/keys"),
            ("credits", "Credits", "https://openrouter.ai/settings/credits"),
        ],
    },
    "copilot": {
        "name": "Copilot",
        "badge": "P",
        "icon": "github-logo",
        "brand_color": "#38BDF8",
        "url": "https://github.com/settings/copilot",
        "hint": "Paste GitHub token (gho_… / ghp_…) or link gh CLI",
        "steps": [
            "1. Run 'gh auth login' in terminal, then click Link GitHub CLI.",
            "2. Or paste a GitHub OAuth / classic token with Copilot access.",
        ],
        "links": [
            ("copilot", "Copilot settings", "https://github.com/settings/copilot"),
            ("tokens", "Tokens", "https://github.com/settings/tokens"),
        ],
    },
    "custom": {
        "name": "Custom",
        "badge": "*",
        "icon": "gauge",
        "brand_color": "accent",
        "url": "",
        "hint": "Optional header (Bearer … or Header: Value)",
        "steps": [
            "1. Enter any HTTPS or localhost JSON URL and dot path (e.g. data.used_pct).",
            "2. Pick a colour and upload or drop a custom icon onto this page.",
        ],
        "links": [],
    },
}

PROVIDER_ORDER = ("claude", "chatgpt", "gemini", "antigravity", "openrouter", "copilot", "custom")

CUSTOM_COLOR_CHOICES = (
    ("Accent", "accent"),
    ("Coral", "#D97757"),
    ("Emerald", "#10A37F"),
    ("Blue", "#4E8DF5"),
    ("Violet", "#8B5CF6"),
    ("Cyan", "#38BDF8"),
    ("Amber", "#F59E0B"),
)

CUSTOM_PRESETS = {
    "deepseek": {
        "name": "DeepSeek",
        "url": "https://api.deepseek.com/user/balance",
        "path": "balance_infos.0.total_balance",
        "mode": 1,  # informational/remaining style
        "label": "USD",
        "color_idx": 3,
    },
    "pct_used": {
        "name": "Local LLM",
        "url": "http://localhost:8080/usage",
        "path": "used_pct",
        "mode": 0,
        "label": "Quota",
        "color_idx": 4,
    },
}


def clamp_pct(value):
    """Clamps a numeric percentage to 0.0..100.0, or returns None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    if num != num:
        return None
    return max(0.0, min(100.0, num))


def parse_iso_epoch(raw):
    """Parses an ISO-8601 timestamp string to a UTC epoch float, or returns None."""
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def format_countdown(reset_epoch, now=None):
    """Formats remaining time until reset_epoch as e.g. '2h 14m' or '3d 4h'."""
    if reset_epoch is None:
        return None
    now = time.time() if now is None else now
    diff = int(max(0, reset_epoch - now))
    if diff < 60:
        return "now"
    mins = diff // 60
    if mins < 60:
        return f"{mins}m"
    hours = mins // 60
    rem_mins = mins % 60
    if hours < 24:
        return f"{hours}h {rem_mins}m" if rem_mins else f"{hours}h"
    days = hours // 24
    rem_hours = hours % 24
    return f"{days}d {rem_hours}h" if rem_hours else f"{days}d"


def format_window_seconds(seconds):
    """Formats a rate-limit window duration in seconds into a concise label."""
    if not seconds:
        return "Window"
    secs = int(seconds)
    if secs % 86400 == 0:
        return f"{secs // 86400}d"
    if secs % 3600 == 0:
        return f"{secs // 3600}h"
    if secs >= 3600:
        return f"{round(secs / 3600)}h"
    return f"{max(1, round(secs / 60))}m"


def mask_secret(secret):
    """Returns a non-sensitive hint for UI display without exposing the token."""
    if not secret:
        return ""
    s = str(secret).strip()
    if len(s) <= 8:
        return "••••"
    return f"{s[:4]}…{s[-4:]}"


def extract_dot_path(data, path):
    """Walks a dot-separated path, supporting both dict keys and integer indices."""
    cur = data
    for part in (path or "").split("."):
        part = part.strip()
        if not part:
            continue
        if isinstance(cur, dict):
            if part not in cur:
                return None
            cur = cur[part]
        elif isinstance(cur, list):
            try:
                cur = cur[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return cur


def make_window(label, used_pct, detail=None, reset_at=None):
    """Builds one normalised usage window record."""
    return {
        "label": str(label),
        "used_pct": clamp_pct(used_pct),
        "detail": detail,
        "reset_at": reset_at,
    }


def make_result(account, plan=None, windows=None, status="ok", note=None, url=None, retry_after=None):
    """Builds a uniform provider usage snapshot for rendering across all surfaces."""
    kind = account.get("kind", "custom")
    meta = PROVIDER_META.get(kind, PROVIDER_META["custom"])
    win_list = list(windows or [])
    pcts = [w["used_pct"] for w in win_list if w.get("used_pct") is not None]
    now = time.time()
    return {
        "id": account["id"],
        "kind": kind,
        "name": account.get("name") or meta["name"],
        "badge": (account.get("badge") or meta["badge"])[:1].upper(),
        "icon": meta["icon"],
        "brand_color": account.get("brand_color") or meta["brand_color"],
        "custom_icon": account.get("custom_icon"),
        "plan": plan,
        "windows": win_list,
        "primary_pct": max(pcts) if pcts else None,
        "status": status,
        "note": note,
        "retry_until": (now + float(retry_after)) if retry_after else None,
        "url": url or account.get("url") or meta["url"],
        "checked_at": now,
    }
