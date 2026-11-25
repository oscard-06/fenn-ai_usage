"""Stateless quota and usage payload parsers for all supported providers."""

import time

from ai_usage.models import (
    clamp_pct,
    extract_dot_path,
    format_countdown,
    format_window_seconds,
    make_window,
    parse_iso_epoch,
)


def parse_claude_oauth_usage(data, plan=None):
    """Parses api.anthropic.com/api/oauth/usage JSON into normalised windows."""
    windows = []
    for key, label in (
        ("five_hour", "5h"),
        ("seven_day", "7d"),
        ("seven_day_sonnet", "Sonnet"),
        ("seven_day_opus", "Opus"),
        ("seven_day_cowork", "Cowork"),
        ("seven_day_oauth_apps", "Apps"),
    ):
        bucket = data.get(key)
        if not isinstance(bucket, dict):
            continue
        pct = clamp_pct(bucket.get("utilization"))
        if pct is None:
            continue
        reset_at = parse_iso_epoch(bucket.get("resets_at"))
        cd = format_countdown(reset_at)
        windows.append(make_window(label, pct, f"resets {cd}" if cd else None, reset_at))

    # OAuth extra_usage reports monthly_limit and used_credits in minor currency units (cents).
    extra = data.get("extra_usage")
    if isinstance(extra, dict) and extra.get("is_enabled"):
        limit = extra.get("monthly_limit")
        used = extra.get("used_credits")
        pct = clamp_pct(extra.get("utilization"))
        if pct is None and isinstance(limit, (int, float)) and limit > 0 and isinstance(used, (int, float)):
            pct = clamp_pct(used / limit * 100.0)
        if pct is not None:
            detail = (
                f"${used / 100:.2f} / ${limit / 100:.2f}"
                if isinstance(used, (int, float)) and isinstance(limit, (int, float))
                else None
            )
            windows.append(make_window("Extra", pct, detail))
    return windows, (str(plan).capitalize() if plan else None)


def _parse_wham_window(label, window):
    """Normalises a single ChatGPT WHAM rate-limit window dictionary."""
    if not isinstance(window, dict):
        return None
    pct = clamp_pct(window.get("used_percent"))
    if pct is None:
        return None
    reset_at = window.get("reset_at")
    if reset_at is None and window.get("reset_after_seconds") is not None:
        reset_at = time.time() + float(window["reset_after_seconds"])
    cd = format_countdown(reset_at)
    return make_window(label, pct, f"resets {cd}" if cd else None, reset_at)


def parse_chatgpt_wham_usage(data):
    """Parses chatgpt.com/backend-api/wham/usage JSON into normalised windows."""
    plan = data.get("plan_type")
    nice_plan = str(plan).capitalize() if plan else None
    rl = data.get("rate_limit") or {}
    windows = []
    for key in ("primary_window", "secondary_window"):
        w = rl.get(key)
        if isinstance(w, dict):
            lbl = format_window_seconds(w.get("limit_window_seconds"))
            parsed = _parse_wham_window(lbl, w)
            if parsed:
                windows.append(parsed)

    cr = data.get("code_review_rate_limit") or {}
    cr_pw = cr.get("primary_window") if isinstance(cr, dict) else None
    parsed_cr = _parse_wham_window("Review", cr_pw)
    if parsed_cr:
        windows.append(parsed_cr)

    for extra in data.get("additional_rate_limits") or []:
        if not isinstance(extra, dict):
            continue
        ew = (extra.get("rate_limit") or {}).get("primary_window")
        base_lbl = str(extra.get("limit_name") or extra.get("model_name") or "").strip()
        if ew and base_lbl:
            parsed_ex = _parse_wham_window(base_lbl[:12], ew)
            if parsed_ex:
                windows.append(parsed_ex)

    credits_obj = data.get("credits") or {}
    note = None
    if isinstance(credits_obj, dict) and credits_obj.get("has_credits"):
        if credits_obj.get("unlimited"):
            note = "Unlimited credits"
        elif credits_obj.get("balance") is not None:
            note = f"Credits: ${credits_obj['balance']}"
    return windows, nice_plan, note


def _short_gemini_label(bucket):
    """Formats a concise Gemini model or token bucket label."""
    raw = str(bucket.get("displayName") or bucket.get("bucketId") or bucket.get("modelId") or "Quota")
    cleaned = (
        raw.replace(" Limit Remaining", "")
        .replace("gemini-", "")
        .replace("-preview", "")
        .strip()
    )
    tok_type = str(bucket.get("tokenType") or "").upper()
    if tok_type == "TOKENS" and "tok" not in cleaned.lower():
        cleaned = f"{cleaned[:10]} tok"
    return cleaned[:14] or "Quota"


def parse_gemini_quota(data, plan=None):
    """Parses cloudcode-pa retrieveUserQuota buckets, sorted tightest first."""
    buckets = data.get("buckets") or []
    windows = []
    seen = set()
    for bucket in buckets:
        if not isinstance(bucket, dict):
            continue
        rem = bucket.get("remainingFraction")
        if rem is None:
            continue
        used_pct = clamp_pct((1.0 - float(rem)) * 100.0)
        label = _short_gemini_label(bucket)
        if label in seen:
            continue
        seen.add(label)
        reset_at = parse_iso_epoch(bucket.get("resetTime"))
        cd = format_countdown(reset_at)
        windows.append(make_window(label, used_pct, f"resets {cd}" if cd else None, reset_at))
    windows.sort(key=lambda w: w["used_pct"] if w["used_pct"] is not None else -1.0, reverse=True)
    return windows[:4], plan


def _format_agy_bucket_label(raw_key):
    """Formats Antigravity quota bucket names into user-friendly counter labels."""
    text = str(raw_key or "").strip()
    if not text:
        return "Quota"
    low = text.lower()
    if low in ("gemini-5h", "gemini_5h"):
        return "Gemini 5h"
    if low in ("gemini-weekly", "gemini_weekly", "gemini-7d", "gemini_7d"):
        return "Gemini 7d"
    if low in ("3p-5h", "3p_5h"):
        return "3rd-Party 5h"
    if low in ("3p-weekly", "3p_weekly", "3p-7d", "3p_7d"):
        return "3rd-Party 7d"
    if low.startswith("gemini-"):
        return _short_gemini_label({"modelId": text})
    if "_" in text or "-" in text:
        parts = [p for p in text.replace("-", "_").split("_") if p]
        return " ".join(p.capitalize() if not any(c.isdigit() for c in p) else p for p in parts)[:14]
    return (text.capitalize() if text.islower() else text)[:14]


def _extract_agy_bucket(label, bucket, base_ts):
    """Extracts percentage used and reset countdown from an Antigravity bucket."""
    if not isinstance(bucket, dict):
        return None
    rem = bucket.get("remaining_fraction")
    if rem is None:
        rem = bucket.get("remainingFraction")
    used_raw = bucket.get("used_fraction")
    if used_raw is None:
        used_raw = bucket.get("usedFraction")
    if rem is not None:
        try:
            used_pct = clamp_pct((1.0 - float(rem)) * 100.0)
        except (TypeError, ValueError):
            return None
    elif used_raw is not None:
        try:
            used_pct = clamp_pct(float(used_raw) * 100.0)
        except (TypeError, ValueError):
            return None
    elif bucket.get("utilization") is not None:
        used_pct = clamp_pct(bucket.get("utilization"))
    else:
        return None
    if used_pct is None:
        return None
    reset_at = parse_iso_epoch(bucket.get("reset_time") or bucket.get("resetTime"))
    if reset_at is None:
        secs = bucket.get("reset_in_seconds")
        if secs is None:
            secs = bucket.get("resetInSeconds")
        if isinstance(secs, (int, float)) and secs > 0:
            reset_at = float(base_ts) + float(secs)
    cd = format_countdown(reset_at)
    return make_window(label, used_pct, f"resets {cd}" if cd else None, reset_at)


def _agy_bucket_sort_key(window):
    """Orders Gemini counters first, then 3rd-party counters, then 5h before 7d."""
    lbl = window["label"].lower()
    group = 0 if "gemini" in lbl else (1 if "3rd-party" in lbl or "3p" in lbl else 2)
    dur = 0 if "5h" in lbl else (1 if "7d" in lbl or "weekly" in lbl else 2)
    return (group, dur, -(window["used_pct"] if window["used_pct"] is not None else -1.0), lbl)


def parse_antigravity_status(data):
    """Parses Antigravity CLI statusLine JSON into normalised quota windows."""
    if not isinstance(data, dict):
        return [], "Antigravity", None
    quota = data.get("quota") or {}
    base_ts = data.get("updated_at") if isinstance(data.get("updated_at"), (int, float)) else time.time()
    windows = []
    if isinstance(quota, dict) and isinstance(quota.get("groups"), list):
        for grp in quota["groups"]:
            if not isinstance(grp, dict):
                continue
            gname = str(grp.get("displayName") or grp.get("name") or "").split()[0]
            for b in grp.get("buckets") or []:
                if not isinstance(b, dict):
                    continue
                lbl = str(b.get("window") or b.get("displayName") or gname or "Quota")[:14]
                if gname and gname.lower() not in lbl.lower():
                    lbl = f"{gname[:7]} {lbl}"[:14]
                win = _extract_agy_bucket(lbl, b, base_ts)
                if win:
                    windows.append(win)
    elif isinstance(quota, dict):
        for raw_key, bucket in quota.items():
            win = _extract_agy_bucket(_format_agy_bucket_label(raw_key), bucket, base_ts)
            if win:
                windows.append(win)
    elif isinstance(quota, list):
        for idx, b in enumerate(quota):
            if isinstance(b, dict):
                lbl = _format_agy_bucket_label(
                    b.get("displayName") or b.get("window") or b.get("model") or f"Pool {idx + 1}"
                )
                win = _extract_agy_bucket(lbl, b, base_ts)
                if win:
                    windows.append(win)
    windows.sort(key=_agy_bucket_sort_key)
    tier = str(data.get("plan_tier") or "").strip()
    plan = tier.capitalize() if tier else "Antigravity"
    cost = data.get("cost_usd")
    note = f"Session ${float(cost):.2f}" if isinstance(cost, (int, float)) and cost > 0 else None
    return windows[:4], plan, note


def parse_openrouter_key(key_data, credits_data=None):
    """Parses OpenRouter /api/v1/key and optional /api/v1/credits responses."""
    d = key_data.get("data") if isinstance(key_data.get("data"), dict) else key_data
    windows = []
    limit = d.get("limit")
    rem = d.get("limit_remaining")
    usage = d.get("usage")
    plan = "Free tier" if d.get("is_free_tier") else "Pay-as-you-go"

    if isinstance(limit, (int, float)) and limit > 0:
        used_val = (limit - rem) if isinstance(rem, (int, float)) else (usage or 0.0)
        pct = clamp_pct(used_val / limit * 100.0)
        detail = (
            f"${rem:.2f} left of ${limit:.2f}"
            if isinstance(rem, (int, float))
            else f"${used_val:.2f} / ${limit:.2f}"
        )
        reset_tag = d.get("limit_reset")
        lbl = str(reset_tag).capitalize() if reset_tag else "Key cap"
        windows.append(make_window(lbl, pct, detail))
    elif isinstance(credits_data, dict):
        cd = credits_data.get("data") if isinstance(credits_data.get("data"), dict) else credits_data
        tot = cd.get("total_credits")
        used = cd.get("total_usage")
        if isinstance(tot, (int, float)) and tot > 0 and isinstance(used, (int, float)):
            pct = clamp_pct(used / tot * 100.0)
            windows.append(make_window("Credits", pct, f"${max(0.0, tot - used):.2f} left of ${tot:.2f}"))

    for field, lbl in (("usage_daily", "Today"), ("usage_weekly", "Week"), ("usage_monthly", "Month")):
        val = d.get(field)
        if isinstance(val, (int, float)):
            windows.append(make_window(lbl, None, f"${val:.3f}" if val < 1 else f"${val:.2f}"))
    if not windows and isinstance(usage, (int, float)):
        windows.append(make_window("Total", None, f"${usage:.2f} used"))
    return windows, plan


def parse_copilot_user(data):
    """Parses api.github.com/copilot_internal/user JSON into normalised windows."""
    plan = data.get("copilot_plan")
    nice_plan = str(plan).replace("_", " ").capitalize() if plan else "Copilot"
    reset_at = parse_iso_epoch(data.get("quota_reset_date_utc"))
    cd = format_countdown(reset_at)
    snaps = data.get("quota_snapshots") or {}
    windows = []
    known = (
        ("premium_interactions", "Premium"),
        ("chat", "Chat"),
        ("completions", "Complete"),
    )
    items = list(known) + [
        (k, k.replace("_", " ").capitalize()[:12])
        for k in snaps
        if k not in {kk for kk, _ in known}
    ]
    for key, label in items:
        snap = snaps.get(key)
        if not isinstance(snap, dict):
            continue
        if snap.get("unlimited"):
            windows.append(make_window(label, None, "Unlimited"))
            continue
        pct_rem = snap.get("percent_remaining")
        if pct_rem is None and snap.get("entitlement") and snap.get("remaining") is not None:
            pct_rem = float(snap["remaining"]) / float(snap["entitlement"]) * 100.0
        if pct_rem is None:
            continue
        used_pct = clamp_pct(100.0 - float(pct_rem))
        rem = snap.get("remaining")
        ent = snap.get("entitlement")
        if rem is not None and ent is not None:
            detail = f"{rem}/{ent} left" + (f" · {cd}" if cd else "")
        else:
            detail = f"resets {cd}" if cd else None
        windows.append(make_window(label, used_pct, detail, reset_at))
    return windows, nice_plan


def parse_custom_payload(data, account):
    """Evaluates user-configured dot paths from a custom JSON endpoint response."""
    val_path = str(account.get("value_path") or "").strip()
    raw_val = extract_dot_path(data, val_path) if val_path else data
    try:
        val = float(raw_val)
    except (TypeError, ValueError):
        return None, f"Path '{val_path or '.'}' is not numeric"

    unit_mode = account.get("value_mode", "used_pct")
    if unit_mode == "remaining_pct":
        used_pct = clamp_pct(100.0 - val)
        detail = f"{clamp_pct(val):.0f}% left"
    elif unit_mode == "used_of_limit":
        lim_path = str(account.get("limit_path") or "").strip()
        lim_raw = extract_dot_path(data, lim_path) if lim_path else None
        try:
            lim = float(lim_raw)
        except (TypeError, ValueError):
            return None, f"Limit path '{lim_path}' is not numeric"
        if lim <= 0:
            return None, "Limit must be > 0"
        used_pct = clamp_pct(val / lim * 100.0)
        detail = f"{val:g} / {lim:g}"
    else:
        used_pct = clamp_pct(val)
        detail = None

    label = account.get("window_label") or "Quota"
    return [make_window(label, used_pct, detail)], None
