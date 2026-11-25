"""Fenn panel, popup, and compact surface view orchestrator."""

import time

import fenn

from ai_usage import models, ui_canvas
from ai_usage.account_views import (
    account_usage_card as _account_usage_card,
    accounts_body,
    connect_body,
    detected_cards,
    flash_card,
    overview_body,
)
from ai_usage.compact_views import (
    badge_value,
    bar_menu,
    bar_segments,
    bar_view,
    fmt_pct,
    preview_view,
    tightest_first,
    tightest_result,
    tile_tooltip,
    tile_view,
    usage_bars_canvas,
    with_display,
)
from ai_usage.settings_views import settings_body as _settings_body

ACTIONS = [
    {"id": "refresh", "icon": "arrow-clockwise", "tooltip": "Refresh all"},
    {"id": "go_accounts", "icon": "plug", "tooltip": "Link accounts"},
]

__all__ = [
    "ACTIONS",
    "badge_value",
    "bar_menu",
    "bar_segments",
    "bar_view",
    "panel_view",
    "preview_view",
    "tile_tooltip",
    "tile_view",
    "usage_bars_canvas",
    "_account_usage_card",
    "_settings_body",
]


def _expanded_overview_body(state, content_w, accent_hex, accent_hi, ink_hex, prefix="pg"):
    """Builds the hero CTA, 3 tinted stat cards, and usage overview grid."""
    canvases = state.ui.setdefault("canvases", {})
    body = []
    flash = flash_card(state)
    if flash:
        body.append(flash)

    results = with_display(state, state.ordered_results())
    unlinked_n = state.unlinked_detected_count()
    if unlinked_n > 0:
        cta_lbl = f"Link {unlinked_n} Detected CLI" + ("s" if unlinked_n > 1 else "")
        cta_icon, cta_act = "plug", "cta:link_all"
    elif state.accounts:
        cta_lbl = "Refreshing Limits…" if state.ui["busy"] else "Refresh All Limits"
        cta_icon, cta_act = "arrow-clockwise", "cta:refresh"
    else:
        cta_lbl, cta_icon, cta_act = "Connect AI Account", "plus-circle", "nav:connect"

    top_res = tightest_result(results)
    alerts = state.alert_results()
    healthy = max(0, len(results) - sum(1 for r in results if r.get("status") in ("auth", "error")))
    if top_res and top_res.get("shown_primary") is not None:
        hw = state.headline_window(top_res)
        win_tag = hw["label"] if hw else ""
        t1_val = fmt_pct(top_res["shown_primary"])
        t1_sub = f"{top_res['name']} {win_tag}".strip()
    else:
        t1_val, t1_sub = "—", "No % meters"

    if alerts:
        t2_val, t2_sub = str(len(alerts)), "Need attention"
    else:
        resets = [
            w["reset_at"]
            for r in results
            for w in (r.get("windows") or [])
            if w.get("reset_at") and w["reset_at"] > time.time()
        ]
        if resets:
            t2_val, t2_sub = models.format_countdown(min(resets)) or "Soon", "Next reset"
        else:
            t2_val, t2_sub = "0", "All clear"

    tiles = [
        (t1_val, t1_sub, ui_canvas.BLUE, "nav:limits"),
        (t2_val, t2_sub, ui_canvas.AMBER, "nav:alerts"),
        (
            f"{healthy}/{len(results)}" if results else "0",
            "Active accounts" if results else "Not linked",
            ui_canvas.GREEN,
            "nav:accounts",
        ),
    ]
    top_canvas = ui_canvas.hero_and_stats_canvas(
        content_w, cta_lbl, cta_icon, cta_act, tiles, state.app.dark, accent_hex, ink_hex,
    )
    canvases[f"{prefix}_top"] = top_canvas
    body.append(top_canvas.node(f"{prefix}_top"))

    if results:
        flip_lbl = "Show left" if state.settings["metric"] == "used" else "Show used"
        head_canvas = ui_canvas.section_heading_canvas(
            content_w, "Usage Overview", count=len(results),
            link_text=flip_lbl, link_action="cta:flip_metric", accent_hi=accent_hi,
        )
        canvases[f"{prefix}_head"] = head_canvas
        body.append(head_canvas.node(f"{prefix}_head"))

        if content_w >= 360 and len(results) >= 2:
            col_w = max(172, (content_w - 8) // 2)
            body.append(fenn.grid(2, [_account_usage_card(state, r, col_w) for r in results], gap=8))
        else:
            body.extend([_account_usage_card(state, r, content_w) for r in results])
    else:
        det = detected_cards(state)
        if det:
            body.append(fenn.card(
                [fenn.text("Detected on this PC", size="small", color="dim")] + det, padding=10, gap=8,
            ))
        else:
            body.append(fenn.text(
                "Connect Claude, ChatGPT, Antigravity, Gemini, OpenRouter, Copilot, or any custom JSON API.",
                size="small", color="dim", wrap=True,
            ))
    return body


def _expanded_body(state, content_w, page_h, accent_hex, accent_hi, ink_hex, prefix="pg"):
    """Routes the active sidebar section to its expanded page or popup content builder."""
    nav = state.ui.get("nav") or "overview"
    canvases = state.ui.setdefault("canvases", {})

    if nav == "overview":
        return _expanded_overview_body(state, content_w, accent_hex, accent_hi, ink_hex, prefix=prefix)

    if nav == "limits":
        results = [r for r in tightest_first(with_display(state, state.ordered_results())) if r.get("windows")]
        flip_lbl = "Show left" if state.settings["metric"] == "used" else "Show used"
        hc = ui_canvas.section_heading_canvas(
            content_w, "Live Limits", count=len(results),
            link_text=flip_lbl, link_action="cta:flip_metric", accent_hi=accent_hi,
        )
        canvases[f"{prefix}_head"] = hc
        if not results:
            ec = ui_canvas.empty_state_canvas(
                content_w, page_h - 60, "chart-bar", "No limit meters yet",
                "Link an AI provider or enable statusLine to track live windows.",
                btn_label="Connect AI", btn_action="nav:connect",
                accent_hex=accent_hex, ink_hex=ink_hex,
            )
            canvases[f"{prefix}_empty"] = ec
            return [hc.node(f"{prefix}_head"), ec.node(f"{prefix}_empty")]
        return [hc.node(f"{prefix}_head")] + [_account_usage_card(state, r, content_w) for r in results]

    if nav == "alerts":
        flagged = with_display(state, state.alert_results())
        hc = ui_canvas.section_heading_canvas(
            content_w, "Session & Limit Alerts", count=len(flagged),
            link_text="Overview", link_action="nav:overview", accent_hi=accent_hi,
        )
        canvases[f"{prefix}_head"] = hc
        if not flagged:
            ec = ui_canvas.empty_state_canvas(
                content_w, page_h - 60, "check-circle", "All accounts healthy",
                "All linked sessions are signed in and within your warning thresholds.",
                btn_label="Back to Overview", btn_action="nav:overview",
                accent_hex=accent_hex, ink_hex=ink_hex,
            )
            canvases[f"{prefix}_empty"] = ec
            return [hc.node(f"{prefix}_head"), ec.node(f"{prefix}_empty")]
        return [hc.node(f"{prefix}_head")] + [_account_usage_card(state, r, content_w) for r in flagged]

    if nav == "accounts":
        hc = ui_canvas.section_heading_canvas(
            content_w, "Accounts", count=len(state.accounts),
            link_text="Add new", link_action="nav:connect", accent_hi=accent_hi,
        )
        canvases[f"{prefix}_head"] = hc
        return [hc.node(f"{prefix}_head")] + accounts_body(state, include_connect=False)

    if nav == "connect":
        hc = ui_canvas.section_heading_canvas(
            content_w, "Connect AI", link_text="All accounts", link_action="nav:accounts", accent_hi=accent_hi,
        )
        canvases[f"{prefix}_head"] = hc
        return [hc.node(f"{prefix}_head")] + connect_body(state, show_flash=True, include_rescan=True)

    hc = ui_canvas.section_heading_canvas(
        content_w, "Settings", link_text="Overview", link_action="nav:overview", accent_hi=accent_hi,
    )
    canvases[f"{prefix}_head"] = hc
    return [hc.node(f"{prefix}_head")] + _settings_body(state, max_w=content_w)


def panel_view(state, total_w, surface="page"):
    """Builds the main sidebar + content surface for both expanded notch and taskbar popup."""
    prefix = "pop" if surface == "popup" else "pg"
    canvases = state.ui.setdefault("canvases", {})
    for k in [key for key in canvases if key.startswith(prefix + "_")]:
        canvases.pop(k, None)

    if int(total_w) < 480:
        inner_w = max(180, int(total_w) - 20)
        top = [fenn.chips("tab", ["Usage", "Accounts", "Settings"], selected=state.ui["tab"])]
        if state.ui["tab"] == 0:
            body = overview_body(state, inner_w)
        elif state.ui["tab"] == 1:
            body = accounts_body(state, include_connect=True)
        else:
            body = _settings_body(state, max_w=inner_w)
        return fenn.column(top + [fenn.column(body, gap=8, scroll=True, id=f"{prefix}-narrow")], gap=8)

    accent_hex, accent_hi, ink_hex = ui_canvas.resolve_accent(state.app.accent)
    panel_h = int(state.ui.get("page_h") or 428) if surface == "page" else 420
    nav = state.ui.get("nav") or "overview"
    side_c = ui_canvas.sidebar_canvas(
        ui_canvas.SIDE_W, panel_h - 10, nav, state.nav_badges(),
        state.app.dark, accent_hex, ink_hex,
    )
    canvases[f"{prefix}_side"] = side_c

    content_w = max(290, int(total_w) - ui_canvas.SIDE_W - ui_canvas.SIDE_GAP - 32)
    main_kids = _expanded_body(state, content_w, panel_h, accent_hex, accent_hi, ink_hex, prefix=prefix)
    return fenn.row([
        side_c.node(f"{prefix}_side"),
        fenn.column(main_kids, gap=8, scroll=True, id=f"{prefix}-{nav}"),
    ], gap=ui_canvas.SIDE_GAP, align="start")
