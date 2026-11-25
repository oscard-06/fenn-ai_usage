"""Account cards, onboarding/detected-CLI views, and provider connect forms."""

import time

import fenn

from ai_usage import icons, models
from ai_usage.compact_views import (
    fmt_pct,
    icon_node,
    row,
    short,
    tightest_result,
    usage_bars_canvas,
    with_display,
)


def flash_card(state):
    """Renders a transient banner for action confirmations or validation errors."""
    if not state.ui["flash"]:
        return None
    msg, col = state.ui["flash"]
    return fenn.card([
        fenn.row([
            fenn.icon("check-circle" if col == "green" else "warning-circle", size=16, color=col),
            fenn.text(msg, size="small", color=col, wrap=True),
        ], gap=6, align="start"),
    ], padding=8)


def detected_cards(state):
    """Builds one-click link rows for unlinked local AI CLIs discovered on this machine."""
    cards = []
    for kind in ("claude", "chatgpt", "gemini", "antigravity", "copilot"):
        info = state.local_clis.get(kind) or {}
        if not info.get("found"):
            continue
        if any(a["kind"] == kind and a.get("mode") == "cli" for a in state.accounts):
            continue
        meta = models.PROVIDER_META[kind]
        cards.append(fenn.row([
            icon_node(state, {"kind": kind, "icon": meta["icon"]}, size=20, rounding=5),
            fenn.column([
                fenn.text(meta["name"], bold=True),
                fenn.text(f"Detected: {info.get('label', 'CLI')}", size="small", color="dim"),
            ], gap=0),
            fenn.spacer(),
            fenn.button(f"link_cli:{kind}", label="Link", icon="link", primary=True),
        ], gap=8, align="center"))
    return cards


def account_usage_card(state, res, card_outer_w):
    """Builds a single account quota card with usage bars, countdowns, and recovery buttons."""
    canvas_w = max(120, card_outer_w - 20)
    brand = res.get("brand_color")
    stale = bool(res.get("stale"))
    subtitle_parts = []
    if res.get("plan"):
        subtitle_parts.append(str(res["plan"]))
    ts_epoch = (res.get("cached_at") or res.get("checked_at")) if stale else res.get("checked_at")
    if res.get("note") and res["status"] == "ok":
        subtitle_parts.append(str(res["note"]))
    elif ts_epoch:
        ts = time.strftime("%H:%M", time.localtime(ts_epoch))
        subtitle_parts.append(f"{ts} cached" if stale else ts)
    if len(state.account_window_options(res)) >= 2:
        sel_win = state.selected_window_label(res)
        def_win = state.default_window_label(res)
        if def_win and sel_win.lower() != def_win.lower():
            subtitle_parts.append(f"bar: {sel_win}")

    head = [
        icon_node(state, res, size=20, rounding=5),
        fenn.column([
            fenn.text(short(res["name"], 18), bold=True),
            fenn.text(" · ".join(subtitle_parts) or "Linked", size="small", color="dim"),
        ], gap=0),
        fenn.spacer(),
    ]
    if res.get("url"):
        head.append(fenn.button(f"open:{res['id']}", icon="arrow-square-out",
                                tooltip="Open usage dashboard"))

    body = [fenn.row(head, gap=8, align="center")]
    windows = res.get("windows") or []
    meter_rows = [
        (w["label"], w["used_pct"], w.get("live_detail"), res["status"], brand, stale)
        for w in windows if w.get("used_pct") is not None
    ]
    info_rows = [w for w in windows if w.get("used_pct") is None]

    if meter_rows:
        max_lbl = max((len(str(r[0])) for r in meter_rows), default=6)
        lbl_w = max(58, min(int(canvas_w * 0.44), int(max_lbl * 6.8 + 8)))
        body.append(usage_bars_canvas(
            state, meter_rows, canvas_w, label_w=lbl_w, brand_color=brand, stale=stale,
        ))
        details = [
            f"{w['label']} {w['live_detail']}" for w in windows
            if w.get("used_pct") is not None and w.get("live_detail")
        ]
        if details:
            body.append(fenn.text(" · ".join(details), size="small", color="dim", wrap=True))

    for w in info_rows:
        max_det = max(10, int((canvas_w - 64) / 6.5))
        det_txt = short(w.get("detail") or "—", max_det)
        body.append(fenn.row([
            fenn.text(short(w["label"], 10), size="small", color="dim"),
            fenn.spacer(),
            fenn.text(det_txt, size="small", bold=True),
        ], gap=6, align="center"))

    if res["status"] in ("auth", "error") and res.get("note"):
        note_col = "orange" if (stale or res["status"] == "error") else "red"
        note_text = str(res["note"])
        retry_until = res.get("retry_until")
        if res["status"] == "error" and isinstance(retry_until, (int, float)) and retry_until > time.time():
            note_text = f"Rate limited (429) · retry in {models.format_countdown(retry_until)}"
        body.append(fenn.text(note_text, size="small", color=note_col, wrap=True))
        if res["status"] == "auth":
            body.append(fenn.row([
                fenn.spacer(),
                fenn.button(f"retry:{res['id']}", icon="arrow-clockwise", tooltip="Retry / refresh session"),
                fenn.button(f"fix:{res['id']}", label="Accounts", icon="plug"),
            ], gap=6, align="center"))
        else:
            body.append(fenn.row([
                fenn.spacer(),
                fenn.button(f"retry:{res['id']}", label="Retry", icon="arrow-clockwise"),
            ], align="center"))
    elif not meter_rows and res.get("note"):
        body.append(fenn.text(res["note"], size="small", color="dim", wrap=True))
    if (
        res.get("kind") == "antigravity"
        and not meter_rows
        and res.get("status") == "info"
        and state.agy_statusline_state() == "off"
    ):
        body.append(fenn.row([
            fenn.spacer(),
            fenn.button(f"agy_sl_enable:{res['id']}", label="Enable statusLine", icon="plug", primary=True),
        ], align="center"))

    return fenn.card(body, padding=10, gap=6)


def welcome_body(state):
    """Builds the first-run onboarding view when no accounts are linked yet."""
    body = [
        fenn.card([
            fenn.column([
                fenn.icon("gauge", size=28, color="accent"),
                fenn.text("Link your AI accounts", size="title", bold=True),
                fenn.text(
                    "Track live limits for Claude, ChatGPT, Antigravity, Gemini, OpenRouter, Copilot, or custom APIs.",
                    size="small", color="dim", wrap=True,
                ),
            ], gap=6, align="center"),
        ], padding=14),
    ]
    det = detected_cards(state)
    if det:
        header = [fenn.text("Ready on this PC", size="small", color="dim"), fenn.spacer()]
        if len(det) > 1:
            header.append(fenn.button("link_all", label=f"Link all ({len(det)})", icon="sparkle", primary=True))
        body.append(fenn.card([fenn.row(header, align="center")] + det, padding=12, gap=8))
    body.append(fenn.button(
        "welcome_accounts", label="Connect provider, token, or custom endpoint", icon="plus", width=0,
    ))
    return body


def overview_body(state, inner_w):
    """Builds the compact usage overview grid and status footer for narrow containers."""
    if not state.accounts:
        return welcome_body(state)
    results = with_display(state, state.ordered_results())

    body = []
    if len(results) >= 2:
        top = tightest_result(results)
        mode_word = "used" if state.settings["metric"] == "used" else "left"
        summary = f"{len(results)} linked"
        if top and top.get("shown_primary") is not None:
            summary += f" · Tightest: {top['name']} ({fmt_pct(top['shown_primary'])} {mode_word})"
        body.append(fenn.row([
            fenn.text(summary, size="small", color="dim"),
            fenn.spacer(),
        ], align="center"))

    if inner_w >= 460 and len(results) >= 2:
        col_w = max(180, (inner_w - 8) // 2)
        cards = [account_usage_card(state, r, col_w) for r in results]
        body.append(fenn.grid(2, cards, gap=8))
    else:
        body.extend([account_usage_card(state, r, inner_w) for r in results])

    caps = state.capabilities()
    mode_lbl = "Used %" if state.settings["metric"] == "used" else "Remaining %"
    status = "Refreshing…" if state.ui["busy"] else (
        f"{mode_lbl} · updated {time.strftime('%H:%M', time.localtime(state.ui['last_fetch']))}"
        if state.ui["last_fetch"] else mode_lbl
    )
    footer_btns = []
    if len(caps["multi_window"]) == 1:
        m_res, m_opts = caps["multi_window"][0]
        cur_win = state.selected_window_label(m_res)
        btn_lbl = f"{m_res['name']}: {cur_win}" if len(results) > 1 else cur_win
        footer_btns.append(fenn.button(
            f"cycle_acc:{m_res['id']}", label=btn_lbl, icon="clock",
            tooltip=f"Switch {m_res['name']} window ({' / '.join(m_opts + ['Tightest'])})",
        ))
    elif len(caps["multi_window"]) >= 2:
        footer_btns.append(fenn.button(
            "go_settings", label="Windows", icon="clock",
            tooltip="Customise taskbar window per model in Settings",
        ))
    if caps["has_metered"] or caps["has_pending"]:
        footer_btns.append(fenn.button(
            "flip_metric",
            label="Show used" if state.settings["metric"] == "remaining" else "Show left",
            icon="sliders",
        ))
    footer_row = [fenn.text(status, size="small", color="faint"), fenn.spacer()]
    if len(footer_btns) == 1:
        footer_row.append(footer_btns[0])
    elif footer_btns:
        footer_row.append(fenn.row(footer_btns, gap=6))
    body.append(fenn.row(footer_row, gap=6, align="center"))
    return body


def accounts_body(state, include_connect=True):
    """Builds the account management view for detected CLIs and linked accounts."""
    body = []
    flash = flash_card(state)
    if flash:
        body.append(flash)

    det = detected_cards(state)
    if det:
        body.append(fenn.card([
            fenn.row([
                fenn.text("Detected on this PC", size="small", color="dim"),
                fenn.spacer(),
                fenn.button("rescan", icon="arrow-clockwise", tooltip="Re-scan PC for CLI logins"),
            ], align="center"),
        ] + det, padding=10, gap=8))

    if state.accounts:
        rows = []
        for acc in state.accounts:
            src = (
                f"{acc.get('mode', 'cli').upper()} · {acc.get('hint', 'local')}"
                if acc.get("mode") != "cli"
                else "Local CLI (auto)"
            )
            res = state.result_for(acc)
            trailing_btns = []
            if acc.get("kind") == "custom":
                trailing_btns.append(fenn.button(
                    f"icon_pick:{acc['id']}", icon="image", tooltip="Upload custom icon",
                ))
            trailing_btns.append(fenn.button(f"rm:{acc['id']}", icon="trash", tooltip="Unlink"))
            row_kids = [
                icon_node(state, res, size=18, rounding=4),
                fenn.column([
                    fenn.text(acc["name"], bold=True),
                    fenn.text(src, size="small", color="faint"),
                ], gap=0),
                fenn.spacer(),
                trailing_btns[0] if len(trailing_btns) == 1 else fenn.row(trailing_btns, gap=4),
            ]
            rows.append(fenn.row(row_kids, gap=8, align="center"))
        body.append(fenn.card(
            [fenn.text("Linked accounts", size="small", color="dim")] + rows, padding=10, gap=8,
        ))

    if not include_connect:
        if state.local_clis.get("antigravity", {}).get("found") or state.has_kind("antigravity"):
            sl_state = state.agy_statusline_state()
            if sl_state == "enabled":
                body.append(fenn.card([fenn.row([
                    fenn.text("Antigravity statusLine sync active", size="small", color="green"),
                    fenn.spacer(),
                    fenn.button("agy_sl_disable", label="Disable", icon="plug"),
                ], align="center")], padding=10))
            elif sl_state == "off":
                body.append(fenn.card([fenn.row([
                    fenn.text("Enable agy statusLine for live % quotas", size="small", color="dim"),
                    fenn.spacer(),
                    fenn.button("agy_sl_enable", label="Enable statusLine", icon="plug", primary=True),
                ], align="center")], padding=10))
        if not det and not state.accounts:
            body.append(fenn.card([
                fenn.text("No local CLI sessions found yet.", size="small", color="dim"),
                fenn.button("go_connect", label="Connect a provider", icon="plus-circle", primary=True),
            ], padding=12, gap=8))
        return body

    body.extend(connect_body(state, show_flash=False, include_rescan=not det))
    return body


def connect_body(state, show_flash=True, include_rescan=True):
    """Builds the provider connection form for OAuth CLIs, API keys, and custom JSON."""
    body = []
    if show_flash:
        flash = flash_card(state)
        if flash:
            body.append(flash)

    kind = models.PROVIDER_ORDER[state.ui["add_kind"]]
    meta = models.PROVIDER_META[kind]
    form_head = [
        icon_node(state, {"kind": kind, "icon": meta["icon"]}, size=18, rounding=4),
        fenn.text(f"Connect {meta['name']}", bold=True),
        fenn.spacer(),
    ]
    if include_rescan:
        form_head.append(fenn.button("rescan", icon="arrow-clockwise", tooltip="Re-scan PC for CLI logins"))

    form = [
        fenn.row(form_head, gap=8, align="center"),
        fenn.chips(
            "add_kind",
            [models.PROVIDER_META[k]["name"] for k in models.PROVIDER_ORDER],
            selected=state.ui["add_kind"],
        ),
    ]

    for step_line in meta.get("steps") or []:
        form.append(fenn.text(step_line, size="small", color="dim", wrap=True))

    links = meta.get("links") or []
    if links:
        form.append(row([
            fenn.button(f"guide_link:{kind}:{key}", label=lbl, icon="arrow-square-out")
            for key, lbl, _ in links
        ], gap=6, align="center"))

    cli_info = state.local_clis.get(kind)
    if cli_info and cli_info.get("found"):
        form.append(fenn.row([
            fenn.text(f"{cli_info['label']} detected", size="small", color="green"),
            fenn.spacer(),
            fenn.button(f"add_cli:{kind}", label=f"Link {cli_info['label']}", icon="link", primary=True),
        ], align="center"))

    if kind == "antigravity":
        sl_state = state.agy_statusline_state()
        if sl_state == "enabled":
            form.append(fenn.row([
                fenn.text("agy statusLine sync active", size="small", color="green"),
                fenn.spacer(),
                fenn.button("agy_sl_disable", label="Disable", icon="plug"),
            ], align="center"))
        elif sl_state == "custom":
            form.append(fenn.text(
                "Custom statusLine already configured in ~/.gemini/antigravity-cli/settings.json",
                size="small", color="dim", wrap=True,
            ))
        else:
            form.append(fenn.row([
                fenn.text("Stream live % quotas from agy statusLine", size="small", color="dim"),
                fenn.spacer(),
                fenn.button("agy_sl_enable", label="Enable statusLine", icon="plug", primary=True),
            ], align="center"))

    if kind == "custom":
        form.append(row([
            fenn.text("Templates:", size="small", color="faint"),
            fenn.button("preset:deepseek", label="DeepSeek USD"),
            fenn.button("preset:pct_used", label="Local LLM %"),
        ], gap=6, align="center"))
        preview_icon = state.ui["custom_icon_path"] or icons.builtin_icon_path("custom")
        icon_row = []
        if preview_icon:
            icon_row.append(fenn.image(path=preview_icon, width=22, height=22, rounding=5))
        icon_row += [
            fenn.button("pick_new_icon", label="Choose icon…", icon="upload"),
            fenn.text("or drop an image here", size="small", color="faint"),
        ]
        form += [
            fenn.text_input("add_name", state.ui["add_name"], hint="Name (e.g. DeepSeek, Ollama)", live=True),
            fenn.text_input("custom_url", state.ui["custom_url"], hint="JSON URL (https://…)", live=True),
            fenn.text_input("add_token", state.ui["add_token"], hint=meta["hint"], live=True),
            fenn.text_input(
                "custom_path", state.ui["custom_path"],
                hint="JSON path to number (e.g. data.used_pct)", live=True,
            ),
            fenn.chips("custom_mode", ["% used", "% left", "Used / Limit"], selected=state.ui["custom_mode"]),
        ]
        if state.ui["custom_mode"] == 2:
            form.append(fenn.text_input(
                "custom_limit_path", state.ui["custom_limit_path"],
                hint="JSON path to limit (e.g. data.limit)", live=True,
            ))
        form += [
            fenn.chips(
                "custom_color",
                [lbl for lbl, _ in models.CUSTOM_COLOR_CHOICES],
                selected=state.ui["custom_color_idx"],
            ),
            row(icon_row, gap=8, align="center"),
            fenn.row([
                fenn.text_input("custom_label", state.ui["custom_label"], hint="Bar label", width=120, live=True),
                fenn.spacer(),
                fenn.button("add_custom", label="Add custom", icon="plus", primary=True),
            ], gap=6, align="center"),
        ]
    else:
        submit_lbl = "Link project / token" if kind == "antigravity" else "Link token"
        form += [
            fenn.text_input("add_token", state.ui["add_token"], hint=meta["hint"], live=True),
            fenn.row([
                fenn.text_input(
                    "add_name", state.ui["add_name"], hint="Optional label (e.g. Work)", width=165, live=True,
                ),
                fenn.spacer(),
                fenn.button("add_submit", label=submit_lbl, icon="key", primary=True),
            ], gap=6, align="center"),
        ]
    body.append(fenn.card(form, padding=10, gap=8))
    return body
