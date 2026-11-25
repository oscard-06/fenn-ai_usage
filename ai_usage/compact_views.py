"""Compact Fenn UI builders: taskbar pills, hover preview, notch tile, menu, and badge."""

import math

import fenn

from ai_usage import icons

BAR_MAX = 5
BAR_ROW = 22
TRACK_FILL = "#8080802E"


def row(children, gap=None, align=None, tooltip=None):
    """Wraps fenn.row, appending a spacer on centred rows that lack one to prevent vertical stretch."""
    kids = list(children)
    if align == "center" and not any(c.get("type") == "spacer" and "size" not in c for c in kids):
        kids.append(fenn.spacer())
    return fenn.row(kids, gap=gap, align=align, tooltip=tooltip)


def fmt_pct(pct):
    """Formats a numeric percentage as a whole number, or an em-dash when unset."""
    return "—" if pct is None else f"{round(pct)}%"


def short(text, limit=18):
    """Truncates a label with a trailing ellipsis when it exceeds limit characters."""
    s = str(text)
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


def tightest_first(results, key="headline_pct"):
    """Orders account results from highest usage (tightest) to lowest."""
    return sorted(
        results,
        key=lambda r: (
            r.get(key, r.get("primary_pct")) is None,
            -(r.get(key, r.get("primary_pct")) or 0.0),
        ),
    )


def tightest_result(results, key="headline_pct"):
    """Returns the single tightest account result, or None if results is empty."""
    ranked = tightest_first(results, key=key)
    return ranked[0] if ranked else None


def icon_node(state, res, size=18, rounding=5):
    """Renders an account's brand PNG image or falls back to a Fenn Phosphor icon."""
    claude_mode = state.settings.get("claude_icon", "logo")
    path = icons.account_icon_path(res, claude_icon=claude_mode)
    if path:
        return fenn.image(path=path, width=size, height=size, rounding=rounding)
    return fenn.icon(state.taskbar_icon(res), size=size, color=res.get("brand_color") or "accent")


def with_display(state, results):
    """Attaches display percentages, filtered visible counters, and live reset strings."""
    enriched = []
    for res in results:
        copy_res = dict(res)
        stale = bool(res.get("stale"))
        wins = []
        for w in state.visible_windows(res):
            cw = dict(w)
            cw["shown_pct"] = state.display_pct(w.get("used_pct"))
            cw["live_detail"] = state.window_detail(w, stale=stale)
            wins.append(cw)
        copy_res["windows"] = wins
        headline = state.headline_pct(res)
        n_headline = state.notch_headline_pct(res)
        copy_res["headline_pct"] = headline
        copy_res["shown_primary"] = state.display_pct(headline)
        copy_res["notch_headline_pct"] = n_headline
        copy_res["notch_shown_primary"] = state.display_pct(n_headline)
        copy_res["bar_countdown"] = state.tightest_countdown(res)
        enriched.append(copy_res)
    return enriched


def tile_tooltip(state, results):
    """Builds the multi-line hover tooltip for a notch tile."""
    if not results:
        return "AI Usage — click to link accounts"
    lines = []
    for r in results:
        opts = state.account_window_options(r)
        selected = state.selected_window_label(r) if len(opts) >= 2 else None
        if r["windows"]:
            parts = []
            for w in r["windows"][:2]:
                if w["used_pct"] is not None:
                    shown = w.get("shown_pct")
                    if shown is None:
                        shown = state.display_pct(w["used_pct"])
                    cd = state.window_countdown(w)
                    tag = f"{w['label']}*" if selected and w["label"].lower() == selected.lower() else w["label"]
                    seg = f"{tag} {fmt_pct(shown)}"
                    if cd:
                        seg += f" ({cd})"
                    parts.append(seg)
                elif w.get("detail"):
                    parts.append(w["detail"])
            lines.append(f"{r['name']}: " + ", ".join(parts))
        else:
            lines.append(f"{r['name']}: {r.get('note') or r['status']}")
    return "\n".join(lines)


# Canvas helpers ---------------------------------------------------------------

def usage_bars_canvas(state, rows, width, label_w=54, brand_color=None, stale=False):
    """Draws aligned rounded progress bars on one canvas.

    rows: list of (label, used_pct_or_None, detail_str_or_None, status, row_brand_color[, row_stale])
    """
    shapes = []
    pct_w = 38
    x0 = label_w
    x1 = max(x0 + 24, width - pct_w)
    for i, item in enumerate(rows):
        lbl, used_pct, detail, st = item[:4]
        r_brand = item[4] if len(item) > 4 else brand_color
        r_stale = item[5] if len(item) > 5 else stale
        y = i * BAR_ROW + BAR_ROW / 2
        shapes.append(fenn.label(0, y, short(lbl, 14), size=12, color="dim", anchor="start"))
        if used_pct is None:
            shapes.append(fenn.label(
                x0, y, short(detail or "—", 28), size=11, color="faint", anchor="start",
            ))
        else:
            shown = state.display_pct(used_pct)
            bar_col = state.status_color(used_pct, st, r_brand, stale=r_stale)
            pct_col = state.pct_color(used_pct, st, stale=r_stale)
            bar_w = x1 - x0
            shapes.append(fenn.rect(x0, y - 2.5, bar_w, 5, radius=2.5, fill=TRACK_FILL))
            if shown > 0:
                shapes.append(fenn.rect(
                    x0, y - 2.5, max(5.0, bar_w * shown / 100.0), 5, radius=2.5, fill=bar_col,
                ))
            shapes.append(fenn.label(
                width, y, fmt_pct(shown), size=12, bold=True, color=pct_col, anchor="end",
            ))
    return fenn.canvas(len(rows) * BAR_ROW, shapes, width=width)


def _arc_dial(state, cx, cy, r, used_pct, stroke, caption=None, status="ok", brand_color=None):
    """Builds Fenn canvas primitives for a compact circular gauge dial."""
    shown = state.display_pct(used_pct)
    shapes = [fenn.arc(cx, cy, r, -135, 135, color=TRACK_FILL, width=stroke)]
    if shown is not None and shown > 0:
        col = state.status_color(used_pct, status, brand_color)
        end = -135 + 270.0 * shown / 100.0
        shapes.append(fenn.arc(cx, cy, r, -135, end, color=col, width=stroke))
        for deg in (-135, end):
            a = math.radians(deg - 90)
            shapes.append(fenn.circle(cx + r * math.cos(a), cy + r * math.sin(a), stroke / 2, fill=col))
    shapes.append(fenn.label(
        cx, cy, fmt_pct(shown).replace("%", "") if shown is not None else "—",
        size=max(11, r * 0.62), bold=True,
        color=state.pct_color(used_pct, status) if shown is not None else "faint",
        anchor="center",
    ))
    if caption:
        shapes.append(fenn.label(
            cx, cy + r * 0.86, caption, size=max(9, r * 0.28), color="dim", anchor="center",
        ))
    return shapes


# Notch tile -------------------------------------------------------------------

def tile_view(state):
    """Draws a notch tile adapted to dimensions, account count, and notch window settings."""
    results = with_display(state, state.ordered_results())
    w, h = state.app.tile_width or 104, state.app.tile_height or 36
    size = state.app.tile_size or ("large" if h >= 72 else ("small" if w < 64 else "wide"))
    if h >= 72:
        size = "large"
    tip = tile_tooltip(state, results)
    mode_word = "used" if state.settings["metric"] == "used" else "left"

    if not results:
        big = h > 60
        if not big and w >= 88:
            shapes = [
                fenn.glyph(18, h / 2, "gauge", size=18, color="accent"),
                fenn.label(34, h / 2, "Link AI", size=12, bold=True, anchor="start"),
            ]
        else:
            shapes = [fenn.glyph(w / 2, h / 2 - (9 if big else 0), "gauge",
                                 size=24 if big else 18, color="accent")]
            if big:
                shapes.append(fenn.label(w / 2, h / 2 + 16, "Link AI", size=11,
                                         color="dim", anchor="center"))
        return fenn.canvas(h, shapes, width=w, tooltip="Click to link Claude, ChatGPT, Gemini, or others")

    notch_ranked = tightest_first(results, key="notch_headline_pct")

    if size == "small":
        top = notch_ranked[0]
        side = min(w, h)
        roomy = side > 80
        r = side * (0.34 if roomy else 0.40)
        cy = h * (0.45 if roomy else 0.50)
        extra = f"+{len(results) - 1}" if len(results) > 1 else None
        caption = extra if (roomy and extra) else (mode_word if roomy else None)
        hpct = top.get("notch_headline_pct", top["primary_pct"])
        shapes = _arc_dial(state, w / 2, cy, r, hpct, max(3.0, r * 0.14),
                           caption, top["status"], top.get("brand_color"))
        if roomy:
            shapes.append(fenn.label(w / 2, h - 8, short(top["name"], 10),
                                     size=10, color="dim", anchor="center"))
        return fenn.canvas(h, shapes, width=w, tooltip=tip)

    tile_results = (
        [notch_ranked[0]]
        if (len(results) > 1 and state.settings.get("notch_mode") == "tightest")
        else results
    )

    if size == "wide":
        if len(tile_results) == 1:
            res = tile_results[0]
            meter_wins = [win for win in res["windows"] if win["used_pct"] is not None][:2]
            if meter_wins:
                brand = res.get("brand_color")
                hpct = res.get("notch_headline_pct", res["primary_pct"])
                shapes = [
                    fenn.glyph(14, h * 0.30, state.taskbar_icon(res), size=13,
                               color=state.status_color(hpct, res["status"], brand)),
                    fenn.label(26, h * 0.30, f"{short(res['name'], 9)} · {mode_word}", size=10,
                               bold=True, anchor="start"),
                ]
                slot_w = (w - 10) / len(meter_wins)
                for idx, win in enumerate(meter_wins):
                    x0 = 6 + idx * slot_w
                    cw = max(24.0, slot_w - 6)
                    shown = win["shown_pct"]
                    bar_col = state.status_color(win["used_pct"], res["status"], brand)
                    pct_col = state.pct_color(win["used_pct"], res["status"])
                    shapes.append(fenn.label(x0, h * 0.60, f"{win['label']} {fmt_pct(shown)}",
                                             size=10, color=pct_col, anchor="start"))
                    shapes.append(fenn.rect(x0, h - 7, cw, 3.5, radius=1.75, fill=TRACK_FILL))
                    if shown > 0:
                        shapes.append(fenn.rect(x0, h - 7, max(4.0, cw * shown / 100.0), 3.5,
                                                radius=1.75, fill=bar_col))
                return fenn.canvas(h, shapes, width=w, tooltip=tip)

        if len(tile_results) >= 4:
            top4 = tightest_first(tile_results, key="notch_headline_pct")[:4]
            shapes = []
            col_w = w / 2.0
            row_h = h / 2.0
            for idx, res in enumerate(top4):
                c, r_idx = idx % 2, idx // 2
                x0 = c * col_w + 5
                y_mid = r_idx * row_h + row_h / 2.0
                pct = res.get("notch_headline_pct", res["primary_pct"])
                col = state.status_color(pct, res["status"], res.get("brand_color"))
                shapes.append(fenn.label(x0, y_mid, res["badge"], size=10, color=col,
                                         bold=True, anchor="start"))
                shapes.append(fenn.label((c + 1) * col_w - 5, y_mid, fmt_pct(res.get("notch_shown_primary")),
                                         size=10, bold=True, color=state.pct_color(pct, res["status"]),
                                         anchor="end"))
            return fenn.canvas(h, shapes, width=w, tooltip=tip)

        cols = tile_results[:3]
        slot_w = w / len(cols)
        shapes = []
        for idx, res in enumerate(cols):
            x0 = idx * slot_w + 4
            cw = max(20.0, slot_w - 8)
            pct = res.get("notch_headline_pct", res["primary_pct"])
            shown = res.get("notch_shown_primary")
            bar_col = state.status_color(pct, res["status"], res.get("brand_color"))
            pct_col = state.pct_color(pct, res["status"]) if shown is not None else "faint"
            shapes.append(fenn.label(x0 + cw / 2, h * 0.28, short(res["name"], 7),
                                     size=10, color="dim", anchor="center"))
            shapes.append(fenn.label(x0 + cw / 2, h * 0.58, fmt_pct(shown),
                                     size=12, bold=True, color=pct_col, anchor="center"))
            shapes.append(fenn.rect(x0 + 2, h - 7, cw - 4, 3.5, radius=1.75, fill=TRACK_FILL))
            if shown is not None and shown > 0:
                shapes.append(fenn.rect(x0 + 2, h - 7, max(4.0, (cw - 4) * shown / 100.0), 3.5,
                                        radius=1.75, fill=bar_col))
        return fenn.canvas(h, shapes, width=w, tooltip=tip)

    # Large tile (2x2+): native column with real brand PNGs, % used/left header, and crisp bars.
    bar_w = max(64, int(w) - 18)

    def _tile_bar(shown, bar_col):
        rects = [fenn.rect(0, 0, bar_w, 3, radius=1.5, fill=TRACK_FILL)]
        if shown is not None and shown > 0:
            rects.append(fenn.rect(0, 0, max(4.0, bar_w * shown / 100.0), 3, radius=1.5, fill=bar_col))
        return fenn.canvas(3, rects, width=bar_w)

    if len(tile_results) == 1:
        res = tile_results[0]
        brand = res.get("brand_color")
        kids = [
            fenn.row([
                icon_node(state, res, size=14, rounding=3),
                fenn.text(short(res["name"], 10), size="small", bold=True),
                fenn.spacer(),
                fenn.text(f"% {mode_word}", size="small", color="faint"),
            ], gap=4, align="center"),
        ]
        for win in res["windows"][:3]:
            pct = win["used_pct"]
            shown = win["shown_pct"]
            if shown is None:
                kids.append(fenn.row([
                    fenn.text(short(win["label"], 10), size="small", color="dim"),
                    fenn.spacer(),
                    fenn.text(short(win.get("detail") or "—", 12), size="small", color="faint"),
                ], align="center"))
            else:
                bar_col = state.status_color(pct, res["status"], brand)
                pct_col = state.pct_color(pct, res["status"])
                kids.append(fenn.column([
                    fenn.row([
                        fenn.text(short(win["label"], 10), size="small", color="dim"),
                        fenn.spacer(),
                        fenn.text(fmt_pct(shown), size="small", bold=True, color=pct_col),
                    ], align="center"),
                    _tile_bar(shown, bar_col),
                ], gap=2))
        return fenn.column(kids, gap=5, justify="center", tooltip=tip)

    ranked = tightest_first(tile_results, key="notch_headline_pct")
    kids = [
        fenn.row([
            fenn.text("AI Usage", size="small", color="faint", bold=True),
            fenn.spacer(),
            fenn.text(f"% {mode_word}", size="small", color="faint"),
        ], align="center"),
    ]
    for res in ranked[:3]:
        pct = res.get("notch_headline_pct", res["primary_pct"])
        shown = res.get("notch_shown_primary")
        bar_col = state.status_color(pct, res["status"], res.get("brand_color"))
        pct_col = state.pct_color(pct, res["status"]) if shown is not None else "faint"
        kids.append(fenn.column([
            fenn.row([
                icon_node(state, res, size=13, rounding=3),
                fenn.text(short(res["name"], 9), size="small", bold=True),
                fenn.spacer(),
                fenn.text(fmt_pct(shown), size="small", bold=True, color=pct_col),
            ], gap=4, align="center"),
            _tile_bar(shown, bar_col),
        ], gap=2))
    if len(tile_results) > 3:
        kids.append(fenn.text(f"+{len(tile_results) - 3} more", size="small", color="faint"))
    return fenn.column(kids, gap=5, justify="center", tooltip=tip)


# Taskbar bar & hover preview --------------------------------------------------

def _bar_text_and_sub(state, res, shown, mode_word, cd):
    """Returns (primary_label, subtitle_text) so info accounts never display vague 'OK'."""
    if shown is not None:
        return fmt_pct(shown), (f"{mode_word} · {cd}" if cd else mode_word)
    if res["status"] in ("auth", "error"):
        return "!", ("re-auth" if res["status"] == "auth" else "error")
    if res.get("checked_at") is None:
        return "…", "syncing"
    if res.get("kind") == "antigravity" and res.get("status") == "info":
        off = state.agy_statusline_state() == "off"
        return ("Setup", "statusLine") if off else ("Sync", "next turn")
    for w in res.get("windows") or []:
        det = str(w.get("detail") or "").strip()
        if det.startswith("$"):
            return det.split()[0], "spend"
        if det.lower() == "unlimited":
            return "Unlim", "plan"
    return "Info", "active"


def _bar_visible_slice(state, results):
    """Selects visible taskbar accounts and computes duplicate-kind counts and overflow."""
    kind_counts = {}
    for r in results:
        kind_counts[r["kind"]] = kind_counts.get(r["kind"], 0) + 1
    if state.settings["bar_mode"] == "tightest":
        top = tightest_result(results)
        return ([top] if top else []), 0, kind_counts
    if len(results) > BAR_MAX:
        visible = tightest_first(results)[:BAR_MAX - 1]
        return visible, len(results) - len(visible), kind_counts
    return results, 0, kind_counts


def bar_segments(state):
    """Builds native Fenn taskbar segments with brand colours, icons, and optional countdown."""
    if state.settings["bar_mode"] == "hidden":
        return []

    results = with_display(state, state.ordered_results())
    if not results:
        return [fenn.segment("Link AI", icon="gauge", dim=True)]

    visible, overflow, kind_counts = _bar_visible_slice(state, results)
    segs = []
    for res in visible:
        pct = res.get("headline_pct", res["primary_pct"])
        shown = res["shown_primary"]
        col = state.segment_color(pct, res["status"], res["kind"], res.get("brand_color"))
        cd = res.get("bar_countdown")
        text, _ = _bar_text_and_sub(state, res, shown, "used", cd)

        icon_name = state.taskbar_icon(res)
        fill_icon = True if (res["kind"] == "gemini" or icon_name in ("alien", "github-logo")) else None
        badge = res["badge"] if (kind_counts.get(res["kind"], 0) > 1 or res["kind"] == "custom") else None
        segs.append(fenn.segment(
            text,
            icon=icon_name,
            badge=badge,
            sub=cd,
            level=(shown / 100.0) if shown is not None else None,
            color=col,
            fill=fill_icon,
            dim=(shown is None and res["status"] in ("error", "info") and res.get("checked_at") is None),
        ))
    if overflow > 0:
        segs.append(fenn.segment(f"+{overflow}", icon="dots-three", color="dim"))
    return segs


def bar_view(state):
    """Builds a custom taskbar view displaying brand PNGs, % headline, bar, and used/left label."""
    if state.settings["bar_mode"] == "hidden":
        return None, 0

    results = with_display(state, state.ordered_results())
    if not results:
        view = row([
            icon_node(state, {"kind": "custom", "icon": "gauge"}, size=16, rounding=4),
            fenn.text("Link AI", size="body", color="dim"),
        ], gap=6, align="center")
        return view, 76

    visible, overflow, kind_counts = _bar_visible_slice(state, results)
    mode_word = "used" if state.settings["metric"] == "used" else "left"
    row_kids = []
    total_w = 8
    for idx, res in enumerate(visible):
        if idx > 0:
            row_kids.append(fenn.spacer(size=4))
            total_w += 14
        pct = res.get("headline_pct", res["primary_pct"])
        shown = res["shown_primary"]
        stale = bool(res.get("stale"))
        bar_col = state.status_color(pct, res["status"], res.get("brand_color"), stale=stale)
        pct_col = state.pct_color(pct, res["status"], stale=stale)
        cd = res.get("bar_countdown")
        base_txt, sub_txt = _bar_text_and_sub(state, res, shown, mode_word, cd)

        show_badge = kind_counts.get(res["kind"], 0) > 1 or res["kind"] == "custom"
        prefix = f"{res['badge']} " if show_badge else ""
        label_txt = f"{prefix}{base_txt}"
        top_w = sum(11 if c == "%" else (8 if c.isalnum() else 5) for c in label_txt) + 6
        sub_w = sum(7 if c.isalnum() else 4 for c in sub_txt) + 6
        col_w = max(34, top_w, sub_w)

        row_kids.append(icon_node(state, res, size=16, rounding=4))
        if shown is not None:
            bar_rects = [fenn.rect(0, 0, col_w, 3, radius=1.5, fill=TRACK_FILL)]
            if shown > 0:
                bar_rects.append(fenn.rect(0, 0, max(4.0, col_w * shown / 100.0), 3, radius=1.5, fill=bar_col))
            row_kids.append(fenn.column([
                fenn.text(label_txt, size="body", bold=True, color=pct_col),
                fenn.canvas(3, bar_rects, width=col_w),
                fenn.text(sub_txt, size="small", color="faint"),
            ], gap=1))
        else:
            row_kids.append(fenn.column([
                fenn.text(label_txt, size="body", bold=True, color=pct_col),
                fenn.text(sub_txt, size="small", color="faint"),
            ], gap=0))
        total_w += 16 + 5 + col_w

    if overflow > 0:
        row_kids.append(fenn.spacer(size=4))
        row_kids.append(fenn.text(f"+{overflow}", size="small", color="dim"))
        total_w += 14 + 22

    return row(row_kids, gap=5, align="center"), total_w


def preview_view(state):
    """Builds the taskbar hover preview with brand PNGs, aligned bars, and live countdowns."""
    results = with_display(state, state.ordered_results())
    w = state.ui["preview_w"]
    if not results:
        return fenn.column([
            row([
                icon_node(state, {"kind": "custom", "icon": "gauge"}, size=18, rounding=4),
                fenn.text("AI Usage", bold=True),
            ], gap=6, align="center"),
            fenn.text("Click to link Claude, ChatGPT, Antigravity, Gemini, or custom APIs.",
                      size="small", color="dim", wrap=True),
        ], gap=6)

    mode_word = "used" if state.settings["metric"] == "used" else "left"
    body = [
        fenn.row([
            fenn.text("AI Usage", size="small", color="dim", bold=True),
            fenn.spacer(),
            fenn.text(f"% {mode_word}", size="small", color="faint"),
        ], align="center"),
    ]

    inner_w = max(160, w - 16)
    if len(results) <= 4:
        for res in results:
            brand = res.get("brand_color")
            stale = bool(res.get("stale"))
            head = [icon_node(state, res, size=14, rounding=3), fenn.text(res["name"], size="small", bold=True)]
            if res.get("plan"):
                head.append(fenn.text(str(res["plan"]), size="small", color="faint"))
            card_body = [row(head, gap=6, align="center")]

            wins = res.get("windows") or []
            meter_rows = [
                (win["label"], win["used_pct"], win.get("live_detail"), res["status"], brand, stale)
                for win in wins if win.get("used_pct") is not None
            ]
            info_rows = [win for win in wins if win.get("used_pct") is None]
            if meter_rows:
                max_lbl = max((len(str(r[0])) for r in meter_rows), default=6)
                lbl_w = max(52, min(int(inner_w * 0.44), int(max_lbl * 6.8 + 8)))
                card_body.append(usage_bars_canvas(
                    state, meter_rows, inner_w, label_w=lbl_w, brand_color=brand, stale=stale,
                ))
            for win in info_rows:
                card_body.append(fenn.row([
                    fenn.text(short(win["label"], 12), size="small", color="dim"),
                    fenn.spacer(),
                    fenn.text(short(win.get("detail") or "—", 16), size="small", bold=True),
                ], align="center"))
            cds = [f"{win['label']} {win['live_detail']}" for win in wins if win.get("live_detail")]
            if cds:
                card_body.append(fenn.text(" · ".join(cds), size="small", color="faint", wrap=True))
            elif not wins and res.get("note"):
                card_body.append(fenn.text(res["note"], size="small", color="orange", wrap=True))
            body.append(fenn.card(card_body, padding=8, gap=4))
    else:
        ranked = tightest_first(results)[:6]
        summary_rows = [
            (
                short(r["name"], 11),
                r.get("headline_pct", r["primary_pct"]),
                r.get("note") or "—",
                r["status"],
                r.get("brand_color"),
                bool(r.get("stale")),
            )
            for r in ranked
        ]
        body.append(fenn.card([
            usage_bars_canvas(state, summary_rows, inner_w, label_w=min(76, int(inner_w * 0.36))),
        ], padding=8, gap=4))

    lows = [r["name"] for r in results if state.is_critical(r.get("headline_pct", r.get("primary_pct")))]
    if lows:
        body.append(fenn.text("Near limit: " + ", ".join(lows), size="small", color="red", wrap=True))
    return fenn.column(body, gap=6)


def bar_menu(state):
    """Builds a dynamic taskbar right-click menu tailored to the user's linked models."""
    caps = state.capabilities()
    items = [
        fenn.menu_item("refresh", "Refresh usage", "arrow-clockwise"),
        fenn.menu_item("go_accounts", "Link / manage accounts", "plug"),
    ]
    for res, _ in caps["multi_window"][:4]:
        cur = state.selected_window_label(res)
        items.append(fenn.menu_item(f"cycle_acc:{res['id']}", f"{res['name']} window: {cur}", "clock"))
    if not state.accounts or caps["has_metered"] or caps["has_pending"]:
        metric_lbl = "Show Used %" if state.settings["metric"] == "remaining" else "Show Remaining %"
        items.append(fenn.menu_item("flip_metric", metric_lbl, "sliders"))
    return items


def badge_value(state):
    """Returns a warning glyph badge when an account needs re-auth or crosses warning threshold."""
    if not state.settings["pill_badge"] or not state.accounts:
        return None
    results = state.ordered_results()
    if any(r["status"] == "auth" or state.is_critical(state.notch_headline_pct(r)) for r in results):
        return {"kind": "glyph", "icon": "warning-circle"}
    return None
