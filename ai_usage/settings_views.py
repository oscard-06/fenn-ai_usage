"""Shared settings UI for counter visibility, display options, taskbar, and notch."""

import fenn

from ai_usage import models
from ai_usage.compact_views import icon_node, row
from ai_usage.state import INTERVALS, WARN_LEVELS


def setting_chips(label, cid, options, selected, icon_node=None, hint=None, max_w=320):
    """Renders chips in a single row when they fit, or wraps into multi-row pills without clipping."""
    top = []
    if icon_node is not None:
        top.append(icon_node)
    top.append(fenn.text(label, size="small", color="dim"))
    if hint:
        top.append(fenn.spacer())
        top.append(fenn.text(hint, size="small", color="faint"))
    opts = list(options)
    sel = max(0, min(len(opts) - 1, selected)) if opts else 0
    est_w = sum(max(44, len(str(o)) * 7 + 22) for o in opts)
    if est_w <= max_w:
        control = fenn.chips(cid, opts, selected=sel)
        return fenn.column([row(top, gap=6, align="center"), control], gap=4)

    rows = []
    cur = []
    cur_w = 0
    for idx, opt in enumerate(opts):
        bw = max(56, len(str(opt)) * 7 + 22)
        if cur and (cur_w + 6 + bw) > max_w:
            rows.append(row(cur, gap=6, align="center"))
            cur = []
            cur_w = 0
        cur.append(fenn.button(f"{cid}_opt:{idx}", label=str(opt), primary=(idx == sel)))
        cur_w += bw + (6 if cur_w else 0)
    if cur:
        rows.append(row(cur, gap=6, align="center"))
    return fenn.column([row(top, gap=6, align="center")] + rows, gap=4)


def settings_body(state, max_w=340):
    """Builds the settings cards shared across the taskbar popup and notch page."""
    s = state.settings
    caps = state.capabilities()
    warn_val = s["warn"] if s["warn"] in WARN_LEVELS else 20
    int_val = s["interval"] if s["interval"] in INTERVALS else 300
    show_pct = caps["account_count"] == 0 or caps["has_metered"] or caps["has_pending"]
    inner_max = max(220, int(max_w) - 22)
    cards = []

    if caps["account_count"] == 0:
        cards.append(fenn.card([
            fenn.row([
                fenn.column([
                    fenn.text("Connect accounts first", bold=True),
                    fenn.text(
                        "Counter toggles, taskbar items, and notch controls adapt once accounts are linked.",
                        size="small", color="dim", wrap=True,
                    ),
                ], gap=2),
                fenn.spacer(),
                fenn.button("go_accounts", label="Accounts", icon="plug", primary=True),
            ], gap=8, align="start"),
        ], padding=10))

    name_counts = {}
    for a in state.accounts:
        nm = a.get("name") or models.PROVIDER_META.get(a.get("kind", "custom"), models.PROVIDER_META["custom"])["name"]
        name_counts[nm] = name_counts.get(nm, 0) + 1

    def _acc_label(res):
        nm = res["name"]
        if name_counts.get(nm, 0) > 1:
            acc = next((a for a in state.accounts if a["id"] == res["id"]), None)
            sub = (acc or {}).get("hint") or res.get("plan")
            if sub:
                return f"{nm} ({sub})"
        return nm

    # 1. Per-counter visibility toggles for every discovered quota counter.
    counter_rows = []
    for acc in state.accounts:
        raw_res = state.result_for(acc)
        wins = raw_res.get("windows") or []
        if not wins:
            continue
        toggles = [
            fenn.toggle(
                f"cnt_vis:{acc['id']}:{idx}",
                state.is_counter_visible(acc["id"], w["label"]),
                label=w["label"],
            )
            for idx, w in enumerate(wins)
        ]
        counter_rows.append(row([
            icon_node(state, raw_res, size=14, rounding=3),
            fenn.text(_acc_label(raw_res), size="small", bold=True),
        ], gap=6, align="center"))
        if inner_max >= 260 and len(toggles) >= 2:
            counter_rows.append(fenn.grid(2, toggles, gap=6))
        else:
            counter_rows.extend(toggles)
    if counter_rows:
        cards.append(fenn.card([
            fenn.text("Visible Counters", size="small", color="dim"),
            fenn.text(
                "Choose which individual counters appear in hover preview and cards.",
                size="small", color="faint", wrap=True,
            ),
        ] + counter_rows, padding=10, gap=8))

    # 2. Shared display & polling configuration.
    appearance = [fenn.text("Shared Display & Refresh", size="small", color="dim")]
    if show_pct:
        appearance.append(setting_chips(
            "Numbers", "metric", ["Used %", "Remaining %"],
            0 if s["metric"] == "used" else 1, max_w=inner_max,
        ))
        color_idx = ("brand", "accent", "traffic").index(s["color_mode"]) if s["color_mode"] in ("brand", "accent", "traffic") else 0
        appearance.append(setting_chips(
            "Colours", "color_mode", ["Brand", "Fenn accent", "Traffic"], color_idx, max_w=inner_max,
        ))
    else:
        basic_idx = 1 if s["color_mode"] == "accent" else 0
        appearance.append(setting_chips(
            "Colours", "color_mode_basic", ["Brand", "Fenn accent"], basic_idx, max_w=inner_max,
        ))
    if caps["has_claude"]:
        claude_idx = 1 if s.get("claude_icon") == "pet" else 0
        appearance.append(setting_chips(
            "Claude icon", "claude_icon", ["Logo", "Clawd pet"], claude_idx,
            icon_node=icon_node(state, {"kind": "claude", "icon": "asterisk"}, size=14, rounding=3),
            max_w=inner_max,
        ))
    if show_pct:
        if s["metric"] == "used":
            warn_lbl, warn_opts = "Warn above used", [f"{100 - x}%" for x in WARN_LEVELS]
        else:
            warn_lbl, warn_opts = "Warn below left", [f"{x}%" for x in WARN_LEVELS]
        appearance.append(setting_chips(warn_lbl, "warn", warn_opts, WARN_LEVELS.index(warn_val), max_w=inner_max))
    appearance.append(setting_chips("Refresh every", "interval", ["2m", "5m", "15m"], INTERVALS.index(int_val), max_w=inner_max))
    cards.append(fenn.card(appearance, padding=10, gap=8))

    # 3. Taskbar-specific layout and headline window selection.
    taskbar_items = [fenn.text("Taskbar", size="small", color="dim")]
    if caps["account_count"] >= 2:
        bar_idx = ("all", "tightest", "hidden").index(s["bar_mode"]) if s["bar_mode"] in ("all", "tightest", "hidden") else 0
        taskbar_items.append(setting_chips(
            "Taskbar items", "bar_mode", ["All", "Tightest", "Hidden"], bar_idx, max_w=inner_max,
        ))
    else:
        simple_idx = 1 if s["bar_mode"] == "hidden" else 0
        taskbar_items.append(setting_chips(
            "Taskbar pill", "bar_mode_simple", ["Shown", "Hidden"], simple_idx, max_w=inner_max,
        ))
    if caps["has_timers"] and s["bar_mode"] != "hidden":
        taskbar_items.append(fenn.toggle(
            "countdown_bar", s["countdown"] == "bar",
            label="Show reset countdown beneath taskbar items",
        ))
    if caps["account_count"] > 0:
        for res, opts in caps["multi_window"]:
            sel = state.selected_window_label(res)
            choices = opts + ["Tightest"]
            sel_idx = next((i for i, c in enumerate(choices) if c.lower() == sel.lower()), 0)
            hint = "Default" if sel.lower() == opts[0].lower() else ("Auto (tightest)" if sel == "Tightest" else sel)
            taskbar_items.append(setting_chips(
                f"{_acc_label(res)} taskbar window",
                f"acc_win:{res['id']}",
                choices, sel_idx,
                icon_node=icon_node(state, res, size=14, rounding=3),
                hint=hint, max_w=inner_max,
            ))
        for res, only_lbl in caps["single_window"]:
            taskbar_items.append(fenn.row([
                icon_node(state, res, size=14, rounding=3),
                fenn.text(_acc_label(res), size="small", color="dim"),
                fenn.spacer(),
                fenn.text(f"{only_lbl} only", size="small", color="faint"),
            ], gap=6, align="center"))
        for res in caps["info_only"]:
            hint = "Checking…" if res.get("checked_at") is None else "Status only"
            taskbar_items.append(fenn.row([
                icon_node(state, res, size=14, rounding=3),
                fenn.text(_acc_label(res), size="small", color="dim"),
                fenn.spacer(),
                fenn.text(hint, size="small", color="faint"),
            ], gap=6, align="center"))
    cards.append(fenn.card(taskbar_items, padding=10, gap=8))

    # 4. Notch-specific layout and warning controls.
    notch_items = [fenn.text("Notch", size="small", color="dim")]
    if caps["account_count"] >= 2:
        notch_idx = 1 if s.get("notch_mode") == "tightest" else 0
        notch_items.append(setting_chips(
            "Notch tile items", "notch_mode", ["All", "Tightest"], notch_idx, max_w=inner_max,
        ))
    if caps["has_timers"]:
        notch_items.append(fenn.toggle(
            "countdown_cards", s["countdown"] != "off",
            label="Show reset countdowns on cards & hover preview",
        ))
    if caps["account_count"] > 0:
        for res, opts in caps["multi_window"]:
            sel = state.selected_notch_window_label(res)
            choices = ["Follow"] + opts + ["Tightest"]
            sel_idx = next((i for i, c in enumerate(choices) if c.lower() == sel.lower()), 0)
            hint = "Same as taskbar" if sel == "Follow" else sel
            notch_items.append(setting_chips(
                f"{_acc_label(res)} notch tile",
                f"notch_win:{res['id']}",
                choices, sel_idx,
                icon_node=icon_node(state, res, size=14, rounding=3),
                hint=hint, max_w=inner_max,
            ))
        notch_items.append(fenn.toggle(
            "pill_badge", s["pill_badge"],
            label="Show warning glyph in collapsed notch pill",
        ))
        if show_pct:
            notch_items.append(fenn.toggle(
                "auto_open", s["auto_open"],
                label="Open notch page when a limit crosses warning",
            ))
    cards.append(fenn.card(notch_items, padding=10, gap=8))
    return cards
