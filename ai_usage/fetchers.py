"""Live usage fetchers for AI providers, local CLIs, and custom JSON endpoints."""

from datetime import datetime, timezone
import time

from ai_usage.cli_auth import (
    antigravity_statusline_state,
    extract_chatgpt_jwt_meta,
    read_antigravity_auth,
    read_antigravity_local_meta,
    read_antigravity_status,
    read_claude_cli,
    read_codex_cli,
    read_gemini_cli,
    read_gh_cli_token,
    refresh_antigravity_cli,
    refresh_claude_cli,
)
from ai_usage.http_client import HttpApiError, http_json
from ai_usage.models import clamp_pct, format_countdown, make_result, make_window
from ai_usage.parsers import (
    parse_antigravity_status,
    parse_chatgpt_wham_usage,
    parse_claude_oauth_usage,
    parse_copilot_user,
    parse_custom_payload,
    parse_gemini_quota,
    parse_openrouter_key,
)


def _nice_plan(plan):
    """Formats a raw subscription tier identifier cleanly for UI display."""
    return str(plan).capitalize() if plan else None


def _api_error_result(account, exc, auth_statuses=(401, 403)):
    """Maps an HTTP API error into a normalised plugin result dictionary."""
    return make_result(
        account,
        status="auth" if exc.status in auth_statuses else "error",
        note=exc.message,
    )


# Claude -----------------------------------------------------------------------

def _claude_oauth_usage_req(token):
    """Queries Anthropic's OAuth usage endpoint for Claude Pro/Max quotas."""
    return http_json(
        "https://api.anthropic.com/api/oauth/usage",
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
        },
    )


def fetch_claude(account):
    """Fetches usage for Claude Code OAuth, Admin API keys, or standard API keys."""
    mode = account.get("mode", "cli")
    plan = None
    if mode == "cli":
        cli = read_claude_cli()
        if not cli:
            return make_result(
                account, status="auth",
                note="Claude Code credentials not found (~/.claude/.credentials.json)",
            )
        if cli.get("expired"):
            refreshed = refresh_claude_cli()
            if refreshed and not refreshed.get("expired"):
                cli = refreshed
        token = cli["access_token"]
        plan = cli.get("plan")
        if cli.get("expired"):
            return make_result(
                account, plan=_nice_plan(plan),
                status="auth", note="Session expired — open 'claude' once to refresh",
            )
    else:
        token = str(account.get("token") or "").strip()
        if not token:
            return make_result(account, status="auth", note="Missing Claude token or API key")

    if mode == "admin_key" or token.startswith("sk-ant-admin"):
        start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end = datetime.now(timezone.utc)
        url = (
            "https://api.anthropic.com/v1/organizations/cost_report"
            f"?starting_at={start.strftime('%Y-%m-%dT%H:%M:%SZ')}"
            f"&ending_at={end.strftime('%Y-%m-%dT%H:%M:%SZ')}&bucket_width=1d"
        )
        try:
            data = http_json(url, headers={"x-api-key": token, "anthropic-version": "2023-06-01"})
        except HttpApiError as exc:
            return _api_error_result(account, exc)
        # cost_report amounts are decimal USD strings (e.g. "142.50").
        total_usd = 0.0
        for bucket in data.get("data") or []:
            for item in bucket.get("results") or []:
                try:
                    total_usd += float(item.get("amount", 0))
                except (TypeError, ValueError):
                    pass
        return make_result(
            account, plan="Admin API",
            windows=[make_window("Month", None, f"${total_usd:.2f} USD")],
            status="info", url="https://console.anthropic.com/settings/cost",
        )

    if token.startswith("sk-ant-api"):
        try:
            http_json(
                "https://api.anthropic.com/v1/models?limit=1",
                headers={"x-api-key": token, "anthropic-version": "2023-06-01"},
            )
            return make_result(
                account, plan="API Key",
                windows=[make_window("Status", None, "Active · use Admin key for org spend")],
                status="info", url="https://console.anthropic.com/settings/cost",
            )
        except HttpApiError as exc:
            return _api_error_result(account, exc)

    try:
        data = _claude_oauth_usage_req(token)
    except HttpApiError as exc:
        if exc.status == 401 and mode == "cli":
            refreshed = refresh_claude_cli()
            if refreshed and not refreshed.get("expired") and refreshed.get("access_token") != token:
                token = refreshed["access_token"]
                plan = refreshed.get("plan") or plan
                try:
                    data = _claude_oauth_usage_req(token)
                    exc = None
                except HttpApiError as exc2:
                    exc = exc2
        if exc is not None:
            if exc.status == 403 and "user:profile" in exc.message:
                return make_result(
                    account, status="auth",
                    note="Token lacks user:profile scope — sign in interactively via 'claude' (/login)",
                )
            if exc.status in (401, 403):
                return make_result(
                    account, plan=_nice_plan(plan),
                    status="auth", note="Session expired — open Claude Code (/login) to refresh",
                )
            if exc.status == 429:
                wait_secs = max(60, min(900, exc.retry_after or 180))
                cd = format_countdown(time.time() + wait_secs)
                return make_result(
                    account, plan=_nice_plan(plan),
                    status="error",
                    note=f"Rate limited (429) · cooling down ({cd})",
                    retry_after=wait_secs,
                )
            return make_result(account, status="error", note=exc.message)

    windows, nice_plan = parse_claude_oauth_usage(data, plan)
    if not windows:
        return make_result(account, plan=nice_plan, status="info", note="No active usage windows")
    return make_result(account, plan=nice_plan, windows=windows)


# ChatGPT / OpenAI -------------------------------------------------------------

def _fetch_openai_api_key(account, token):
    """Fetches monthly org spend for OpenAI admin keys or validates project keys."""
    if token.startswith("sk-proj-"):
        try:
            http_json("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {token}"})
            return make_result(
                account, plan="Project Key",
                windows=[make_window("Status", None, "Active · Admin key needed for org spend")],
                status="info", url="https://platform.openai.com/usage",
            )
        except HttpApiError as exc:
            return _api_error_result(account, exc)

    start = int(datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
    try:
        data = http_json(
            f"https://api.openai.com/v1/organization/costs?start_time={start}&limit=31",
            headers={"Authorization": f"Bearer {token}"},
        )
        total = 0.0
        for bucket in data.get("data") or []:
            for res in bucket.get("results") or []:
                amt = (res.get("amount") or {}).get("value")
                if amt is not None:
                    total += float(amt)
        return make_result(
            account, plan="OpenAI Admin",
            windows=[make_window("Month", None, f"${total:.2f} USD")],
            status="info", url="https://platform.openai.com/usage",
        )
    except HttpApiError as exc:
        if exc.status in (401, 403):
            try:
                http_json("https://api.openai.com/v1/models", headers={"Authorization": f"Bearer {token}"})
                return make_result(
                    account, plan="API Key",
                    windows=[make_window("Status", None, "Active · view spend on platform")],
                    status="info", url="https://platform.openai.com/usage",
                )
            except HttpApiError as exc2:
                return _api_error_result(account, exc2)
        return make_result(account, status="error", note=exc.message)


def fetch_chatgpt(account):
    """Fetches usage for ChatGPT Plus/Pro (Codex CLI or JWT) or OpenAI API keys."""
    mode = account.get("mode", "cli")
    account_id = account.get("account_id")
    token = str(account.get("token") or "").strip()
    plan_hint = None

    if mode == "cli":
        cli = read_codex_cli()
        if not cli:
            return make_result(
                account, status="auth",
                note="Codex credentials not found (~/.codex/auth.json)",
            )
        if cli.get("access_token"):
            token = cli["access_token"]
            account_id = cli.get("account_id")
            plan_hint = cli.get("plan")
            if cli.get("expired"):
                return make_result(
                    account, plan=_nice_plan(plan_hint),
                    status="auth", note="Codex token expired — run 'codex login' to refresh",
                )
        elif cli.get("api_key"):
            token = cli["api_key"]
            mode = "api_key"
    else:
        jwt_meta = extract_chatgpt_jwt_meta(token)
        account_id = account_id or jwt_meta["account_id"]
        plan_hint = jwt_meta["plan"]
        if jwt_meta["expired"]:
            return make_result(
                account, plan=_nice_plan(plan_hint),
                status="auth", note="Bearer token expired — paste a fresh token or link Codex CLI",
            )

    if not token:
        return make_result(account, status="auth", note="Missing ChatGPT token or OpenAI key")

    if mode == "api_key" or token.startswith("sk-"):
        return _fetch_openai_api_key(account, token)

    headers = {"Authorization": f"Bearer {token}"}
    if account_id:
        headers["ChatGPT-Account-Id"] = str(account_id)
    try:
        data = http_json("https://chatgpt.com/backend-api/wham/usage", headers=headers)
    except HttpApiError as exc:
        if exc.status in (401, 403):
            return make_result(
                account, status="auth",
                note="Session expired — run 'codex login' to refresh",
            )
        return make_result(account, status="error", note=exc.message)

    windows, nice_plan, note = parse_chatgpt_wham_usage(data)
    nice_plan = nice_plan or _nice_plan(plan_hint)
    if not windows:
        return make_result(account, plan=nice_plan, status="info", note=note or "No active usage windows")
    return make_result(account, plan=nice_plan, windows=windows, note=note)


# Gemini -----------------------------------------------------------------------

def _verify_gemini_api_key(account, key, plan_label="AI Studio Key"):
    """Verifies a Google AI Studio API key via generativelanguage.googleapis.com."""
    try:
        http_json(
            "https://generativelanguage.googleapis.com/v1beta/models?pageSize=1",
            headers={"x-goog-api-key": key},
        )
        return make_result(
            account,
            plan=plan_label,
            windows=[make_window("Status", None, "Active · open AI Studio for quota")],
            status="info",
            note="Google API keys do not expose remaining quota via REST",
        )
    except HttpApiError as exc:
        return _api_error_result(account, exc, auth_statuses=(400, 401, 403))


def _fetch_code_assist_quota(account, access_token, project=None, plan_default="Code Assist", ide_type="GEMINI_CLI"):
    """Queries Cloud Code Assist loadCodeAssist and retrieveUserQuota endpoints."""
    headers = {"Authorization": f"Bearer {access_token}"}
    plan = plan_default
    ineligible_reason = None
    try:
        assist = http_json(
            "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist",
            headers=headers,
            method="POST",
            body={"metadata": {"ideType": ide_type, "pluginType": "GEMINI"}},
        )
        if not project:
            project = assist.get("cloudaicompanionProject")
        tier = (assist.get("currentTier") or {}).get("name")
        if tier:
            plan = str(tier)
        inelig = assist.get("ineligibleTiers")
        if isinstance(inelig, list) and inelig and isinstance(inelig[0], dict):
            ineligible_reason = inelig[0].get("reasonCode")
    except HttpApiError:
        pass

    try:
        body = {"project": project} if project else {}
        quota = http_json(
            "https://cloudcode-pa.googleapis.com/v1internal:retrieveUserQuota",
            headers=headers,
            method="POST",
            body=body,
        )
    except HttpApiError as exc:
        if exc.status in (401, 403):
            if ineligible_reason == "UNSUPPORTED_CLIENT" or "UNSUPPORTED_CLIENT" in exc.message:
                note = "Google migrated individual quotas to Antigravity — link Antigravity or add a GCP Project ID"
            else:
                note = "Quota requires Cloud Code Assist project or Gemini CLI entitlement"
            return make_result(account, plan=plan, status="auth", note=note)
        return make_result(account, plan=plan, status="error", note=exc.message)

    windows, nice_plan = parse_gemini_quota(quota, plan)
    if not windows:
        return make_result(account, plan=nice_plan, status="info", note="No quota buckets returned")
    return make_result(account, plan=nice_plan, windows=windows)


def _refresh_google_oauth_token(client_id, client_secret, refresh_token):
    """Exchanges a Google OAuth refresh_token for a fresh access_token."""
    refreshed = http_json(
        "https://oauth2.googleapis.com/token",
        method="POST",
        body={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    return refreshed.get("access_token")


def fetch_gemini(account):
    """Fetches Gemini CLI / Code Assist quotas or verifies an AI Studio API key."""
    mode = account.get("mode", "cli")
    if mode == "cli":
        cli = read_gemini_cli()
        if not cli:
            return make_result(
                account,
                status="auth",
                note="Gemini CLI credentials not found (~/.gemini/oauth_creds.json). If using agy, link Antigravity instead.",
            )
        source = cli.get("source", "oauth")
        if source == "env_key":
            return _verify_gemini_api_key(account, cli["api_key"], plan_label="GEMINI_API_KEY")
        if source == "adc":
            try:
                access_token = _refresh_google_oauth_token(
                    cli["client_id"], cli["client_secret"], cli["refresh_token"],
                )
            except HttpApiError as exc:
                return make_result(
                    account,
                    plan="gcloud ADC",
                    status="auth" if exc.status in (400, 401, 403) else "error",
                    note=f"gcloud ADC refresh failed: {exc.message}",
                )
            if not access_token:
                return make_result(account, plan="gcloud ADC", status="auth", note="Run 'gcloud auth application-default login'")
            return _fetch_code_assist_quota(
                account, access_token, project=cli.get("quota_project"), plan_default="gcloud ADC",
            )
        access_token = cli["access_token"]
        if cli.get("expired"):
            fresh = None
            if cli.get("refresh_token") and cli.get("client_id") and cli.get("client_secret"):
                try:
                    fresh = _refresh_google_oauth_token(
                        cli["client_id"], cli["client_secret"], cli["refresh_token"],
                    )
                except HttpApiError:
                    fresh = None
            if not fresh:
                return make_result(
                    account, plan="Gemini CLI", status="auth",
                    note="Token expired — run 'gemini' to refresh",
                )
            access_token = fresh
        return _fetch_code_assist_quota(
            account, access_token, project=cli.get("project"), plan_default="Code Assist",
        )

    key = str(account.get("token") or "").strip()
    if not key:
        return make_result(account, status="auth", note="Missing Gemini API key or GCP project ID")
    if not key.startswith("AIza"):
        cli = read_gemini_cli()
        tok = None
        if cli and cli.get("source") == "adc":
            try:
                tok = _refresh_google_oauth_token(cli["client_id"], cli["client_secret"], cli["refresh_token"])
            except HttpApiError as exc:
                return make_result(account, plan="Code Assist", status="auth", note=exc.message)
        elif cli and cli.get("access_token") and not cli.get("expired"):
            tok = cli["access_token"]
        if tok:
            return _fetch_code_assist_quota(account, tok, project=key, plan_default="Code Assist")
    return _verify_gemini_api_key(account, key, plan_label="AI Studio Key")


# Antigravity ------------------------------------------------------------------

def fetch_antigravity(account, data_dir=None):
    """Reads Antigravity statusLine quota telemetry, keyring auth, or Cloud Assist project."""
    status_snap = read_antigravity_status(data_dir)
    if status_snap and status_snap.get("quota"):
        windows, plan, note = parse_antigravity_status(status_snap)
        if windows:
            return make_result(account, plan=plan, windows=windows, note=note)

    raw_token = str(account.get("token") or "").strip()
    manual_access = raw_token if raw_token.startswith("ya29.") else None
    project = raw_token if (raw_token and not manual_access) else None

    auth = None if manual_access else read_antigravity_auth()
    if auth and auth.get("expired"):
        refreshed = refresh_antigravity_cli()
        if refreshed and not refreshed.get("expired"):
            auth = refreshed
    meta = read_antigravity_local_meta()
    access_token = manual_access or ((auth or {}).get("access_token") if not (auth or {}).get("expired") else None)

    if access_token and (project or (auth and auth.get("auth_method") not in (None, "consumer"))):
        res = _fetch_code_assist_quota(
            account, access_token, project=project, plan_default="Antigravity", ide_type="ANTIGRAVITY",
        )
        if res["status"] == "ok" or project:
            return res

    if auth and auth.get("expired"):
        return make_result(
            account,
            plan=_nice_plan(auth.get("auth_method") or "Antigravity"),
            status="auth",
            note="Antigravity session expired — click Retry or open 'agy' to refresh",
        )

    if auth or meta.get("installed") or status_snap:
        plan = _nice_plan((auth or {}).get("auth_method") or (status_snap or {}).get("plan_tier") or "Consumer")
        sl_state = antigravity_statusline_state()
        info_wins = [make_window("Session", None, "Signed in" if auth else "agy detected")]
        if status_snap and isinstance(status_snap.get("context_pct"), (int, float)):
            info_wins.append(make_window("Context", clamp_pct(status_snap["context_pct"]), "Last turn"))
        if sl_state == "enabled":
            note = "statusLine ready — send a prompt in 'agy' CLI to stream % quotas"
        elif sl_state == "custom":
            note = "Custom statusLine in settings.json — automatic sync skipped"
        else:
            note = "Click Enable statusLine below, then run a turn in 'agy' CLI"
        return make_result(
            account,
            plan=plan,
            windows=info_wins,
            status="info",
            note=note,
        )

    return make_result(
        account,
        status="auth",
        note="Antigravity CLI/App session not found — sign in via 'agy' first",
    )


# OpenRouter -------------------------------------------------------------------

def fetch_openrouter(account):
    """Fetches key limit, credit balance, and spend from OpenRouter."""
    key = str(account.get("token") or "").strip()
    if not key:
        return make_result(account, status="auth", note="Missing OpenRouter API key")
    headers = {"Authorization": f"Bearer {key}"}
    try:
        key_data = http_json("https://openrouter.ai/api/v1/key", headers=headers)
    except HttpApiError as exc:
        return _api_error_result(account, exc)
    credits_data = None
    try:
        credits_data = http_json("https://openrouter.ai/api/v1/credits", headers=headers)
    except HttpApiError:
        pass
    windows, plan = parse_openrouter_key(key_data, credits_data)
    has_meter = any(w["used_pct"] is not None for w in windows)
    return make_result(account, plan=plan, windows=windows, status="ok" if has_meter else "info")


# GitHub Copilot ---------------------------------------------------------------

def fetch_copilot(account):
    """Fetches GitHub Copilot quota snapshots via gh CLI or pasted GitHub token."""
    mode = account.get("mode", "cli")
    token = read_gh_cli_token() if mode == "cli" else str(account.get("token") or "").strip()
    if not token:
        note = "Run 'gh auth login' or paste a GitHub token" if mode == "cli" else "Missing GitHub token"
        return make_result(account, status="auth", note=note)
    try:
        data = http_json(
            "https://api.github.com/copilot_internal/user",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
            },
        )
    except HttpApiError as exc:
        return _api_error_result(account, exc)
    windows, plan = parse_copilot_user(data)
    return make_result(account, plan=plan, windows=windows, status="ok" if windows else "info")


# Custom JSON endpoint ---------------------------------------------------------

def fetch_custom(account):
    """Fetches any HTTPS/localhost JSON endpoint using a user-configured dot path."""
    url = str(account.get("url") or "").strip()
    if not url:
        return make_result(account, status="error", note="Missing JSON endpoint URL")
    headers = {}
    auth = str(account.get("token") or "").strip()
    if auth:
        if ":" in auth and not auth.lower().startswith("bearer "):
            k, v = auth.split(":", 1)
            headers[k.strip()] = v.strip()
        else:
            headers["Authorization"] = auth if auth.lower().startswith("bearer ") else f"Bearer {auth}"
    try:
        data = http_json(url, headers=headers)
    except HttpApiError as exc:
        return _api_error_result(account, exc)

    windows, err_note = parse_custom_payload(data, account)
    if err_note:
        return make_result(account, status="error", note=err_note)
    return make_result(account, plan="Custom JSON", windows=windows)


FETCHERS = {
    "claude": fetch_claude,
    "chatgpt": fetch_chatgpt,
    "gemini": fetch_gemini,
    "antigravity": fetch_antigravity,
    "openrouter": fetch_openrouter,
    "copilot": fetch_copilot,
    "custom": fetch_custom,
}


def fetch_account(account, data_dir=None):
    """Dispatches a usage fetch for any configured provider account."""
    kind = account.get("kind")
    fn = FETCHERS.get(kind)
    if not fn:
        return make_result(account, status="error", note=f"Unknown provider: {kind}")
    try:
        if kind == "antigravity":
            return fn(account, data_dir=data_dir)
        return fn(account)
    except Exception as exc:
        return make_result(account, status="error", note=f"{type(exc).__name__}: {exc}")
