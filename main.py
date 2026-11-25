"""Entry point for the AI Usage Fenn plugin.

Wires Fenn's stdin/stdout JSON event loop (`fenn.Plugin`) to `PluginState`
and the dynamic taskbar / notch views in `ai_usage`.
"""

import json
import os
import re
import time

import fenn
from ai_usage import models, views
from ai_usage.state import INTERVALS, PluginState, WARN_LEVELS

_DEFAULT_DIMS = {"small": (44, 44), "wide": (104, 36), "large": (104, 104)}


def _seed_tile_layout(plugin):
    """Restores persisted notch tile size across hot reloads before layout messages arrive."""
    saved = plugin.load("tile_layout", None)
    if isinstance(saved, dict) and saved.get("size") in _DEFAULT_DIMS:
        plugin.tile_size = saved["size"]
        dw, dh = _DEFAULT_DIMS[saved["size"]]
        plugin.tile_width = saved.get("width") or dw
        plugin.tile_height = saved.get("height") or dh
        return
    cfg_path = os.path.join(os.path.dirname(os.path.dirname(plugin.data_dir)), "config.toml")
    try:
        with open(cfg_path, encoding="utf-8") as f:
            txt = f.read()
        m = re.search(rf'"plugin:{re.escape(plugin.id)}"\s*=\s*"(small|wide|large)"', txt)
        if m:
            sz = m.group(1)
            plugin.tile_size = sz
            plugin.tile_width, plugin.tile_height = _DEFAULT_DIMS[sz]
    except OSError:
        pass


app = fenn.Plugin()
_seed_tile_layout(app)
state = PluginState(app)


def draw(force=False):
    """Emits updated tile, page, badge, and taskbar payloads when state changes."""
    page = views.panel_view(state, state.ui["page_w"], surface="page")
    for key, val, send in (
        ("tile", views.tile_view(state), app.tile),
        ("page", page, lambda v: app.page(v, views.ACTIONS)),
        ("badge", views.badge_value(state), lambda b: fenn.emit({"type": "badge", "badge": b})),
    ):
        serialised = json.dumps(val, sort_keys=True)
        if force or serialised != state.last_sent[key]:
            send(val)
            state.last_sent[key] = serialised

    raw_results = state.ordered_results()
    warn_now = {r["id"] for r in raw_results if state.is_critical(state.notch_headline_pct(r))}
    if state.settings["auto_open"] and state.ui["warned"] is not None and (warn_now - state.ui["warned"]):
        app.show()
    state.ui["warned"] = warn_now
    state.ui["last_redraw"] = time.time()

    if not app.taskbar:
        return
    bar_v, bar_w = views.bar_view(state)
    if bar_v is not None:
        # Omit "segments" instead of calling app.bar(view=...), because fenn.py forces
        # "segments": [] alongside "view", which makes Fenn hide the taskbar item.
        app._once("bar", fenn._clean({
            "type": "bar",
            "view": bar_v,
            "width": bar_w,
            "tooltip": views.tile_tooltip(state, raw_results),
            "menu": views.bar_menu(state),
        }))
        app.preview(views.preview_view(state))
    else:
        app.clear_bar()
        app.clear_preview()
    app.popup(views.panel_view(state, state.ui["popup_w"], surface="popup"), title="AI Usage", actions=views.ACTIONS)


@app.on_message("x-usage")
def on_usage(msg):
    state.apply_usage_message(msg)
    draw()


@app.on_message("x-icon-picked")
def on_icon_picked(msg):
    state.apply_icon_path(msg.get("path"), msg.get("target_id"))
    draw()


@app.on_message("files")
def on_files(msg):
    paths = msg.get("paths") or []
    if not paths:
        return
    in_connect = (state.ui["tab"] == 1 or state.ui.get("nav") == "connect")
    if in_connect and models.PROVIDER_ORDER[state.ui["add_kind"]] == "custom":
        state.apply_icon_path(paths[0], target_id=None)
    else:
        custom_acc = next((a for a in state.accounts if a.get("kind") == "custom"), None)
        state.apply_icon_path(paths[0], target_id=custom_acc["id"] if custom_acc else None)
    draw()


def _set_nav(nav):
    state.ui["nav"] = nav
    if nav in ("overview", "limits", "alerts"):
        state.ui["tab"] = 0
    elif nav in ("accounts", "connect"):
        state.ui["tab"] = 1
        state.rescan_clis()
    elif nav == "settings":
        state.ui["tab"] = 2
    state.ui["flash"] = None


def _handle_canvas_action(act):
    if act.startswith("nav:"):
        _set_nav(act[4:])
    elif act == "cta:refresh":
        state.refresh_all(force=True)
    elif act == "cta:link_all":
        state.link_all_detected()
    elif act == "cta:flip_metric":
        state.settings["metric"] = "used" if state.settings["metric"] == "remaining" else "remaining"
        state.save_settings()


def _apply_window_choice(surface, acc_id, idx=0, label=None):
    """Updates taskbar or notch window selection by explicit label or option index."""
    if not label:
        acc = next((a for a in state.accounts if a["id"] == acc_id), None)
        base = state.account_window_options(state.result_for(acc)) if acc else []
        opts = (["Follow"] if surface == "notch" else []) + base + ["Tightest"]
        label = opts[max(0, min(len(opts) - 1, idx))]
    if surface == "notch":
        state.set_notch_window(acc_id, label)
    else:
        state.set_account_window(acc_id, label)


@app.on_message("event")
def on_event(event):
    eid, kind, val = event.get("id", ""), event.get("kind"), event.get("value")

    canv = (state.ui.get("canvases") or {}).get(eid)
    if canv is not None and isinstance(val, dict) and "x" in val and "y" in val:
        act = canv.hit(val["x"], val["y"])
        if act:
            _handle_canvas_action(act)
            draw()
        return

    if eid == "tab" and isinstance(val, dict):
        idx = max(0, min(2, int(val.get("index", 0))))
        _set_nav(("overview", "accounts", "settings")[idx])
    elif eid in ("go_accounts", "welcome_accounts"):
        _set_nav("accounts")
    elif eid == "go_connect":
        _set_nav("connect")
    elif eid == "go_settings":
        _set_nav("settings")
    elif eid == "rescan":
        state.rescan_clis()
        state.ui["flash"] = ("Scanned PC for local CLI logins", "dim")
    elif eid.startswith("guide_link:"):
        parts = eid.split(":", 2)
        if len(parts) == 3:
            pkind, link_key = parts[1], parts[2]
            for k, _, url in models.PROVIDER_META.get(pkind, {}).get("links", []):
                if k == link_key:
                    app.open(url)
                    break
    elif eid.startswith("preset:"):
        pkey = eid.split(":", 1)[1]
        preset = models.CUSTOM_PRESETS.get(pkey)
        if preset:
            state.ui["add_name"] = preset["name"]
            state.ui["custom_url"] = preset["url"]
            state.ui["custom_path"] = preset["path"]
            state.ui["custom_mode"] = preset["mode"]
            state.ui["custom_label"] = preset["label"]
            state.ui["custom_color_idx"] = preset["color_idx"]
            state.ui["flash"] = (f"Loaded {preset['name']} template", "dim")
    elif eid == "refresh":
        state.refresh_all(force=True)
    elif eid.startswith("retry:"):
        state.refresh_all(force=True, target_id=eid[6:])
    elif eid == "flip_metric":
        state.settings["metric"] = "used" if state.settings["metric"] == "remaining" else "remaining"
        state.save_settings()
    elif eid.startswith("cycle_acc:"):
        state.cycle_account_window(eid[len("cycle_acc:"):])
    elif eid.startswith("acc_win:") and isinstance(val, dict):
        _apply_window_choice("taskbar", eid[len("acc_win:"):], int(val.get("index", 0)), val.get("label"))
    elif eid.startswith("notch_win:") and isinstance(val, dict):
        _apply_window_choice("notch", eid[len("notch_win:"):], int(val.get("index", 0)), val.get("label"))
    elif "_opt:" in eid:
        base_cid, idx_s = eid.rsplit("_opt:", 1)
        try:
            opt_idx = int(idx_s)
        except ValueError:
            opt_idx = 0
        if base_cid.startswith("acc_win:"):
            _apply_window_choice("taskbar", base_cid[len("acc_win:"):], opt_idx)
        elif base_cid.startswith("notch_win:"):
            _apply_window_choice("notch", base_cid[len("notch_win:"):], opt_idx)
        else:
            on_event({"id": base_cid, "kind": "change", "value": {"index": opt_idx}})
            return
    elif eid.startswith("cnt_vis:"):
        acc_id, idx_s = eid[len("cnt_vis:"):].rsplit(":", 1)
        acc = next((a for a in state.accounts if a["id"] == acc_id), None)
        if acc:
            wins = state.result_for(acc).get("windows") or []
            try:
                w_idx = int(idx_s)
            except ValueError:
                w_idx = -1
            if 0 <= w_idx < len(wins):
                state.set_counter_visible(acc_id, wins[w_idx]["label"], bool(val))
    elif eid == "link_all":
        state.link_all_detected()
    elif eid == "agy_sl_enable" or eid.startswith("agy_sl_enable:"):
        if state.enable_agy_statusline():
            agy_acc = next((a for a in state.accounts if a["kind"] == "antigravity"), None)
            if not agy_acc:
                agy_id = state.add_cli_account("antigravity")
                state.refresh_all(force=True, target_id=agy_id)
            else:
                state.refresh_all(force=True, target_id=agy_acc["id"])
    elif eid == "agy_sl_disable":
        state.disable_agy_statusline()
    elif eid.startswith("link_cli:") or eid.startswith("add_cli:"):
        pkind = eid.split(":", 1)[1]
        if pkind in models.PROVIDER_META:
            acc_id = state.add_cli_account(pkind)
            state.refresh_all(force=True, target_id=acc_id)
    elif eid.startswith("rm:"):
        state.remove_account(eid[3:])
    elif eid.startswith("icon_pick:"):
        state.start_icon_picker(target_id=eid[10:])
    elif eid == "pick_new_icon":
        state.start_icon_picker(target_id=None)
    elif eid.startswith("open:"):
        target = eid[5:]
        res = next((r for r in state.ordered_results() if r["id"] == target), None)
        if res and res.get("url"):
            app.open(res["url"])
    elif eid.startswith("fix:"):
        _set_nav("accounts")
    elif eid == "add_kind" and isinstance(val, dict):
        lbl = str(val.get("label") or "").strip().lower()
        idx = next(
            (i for i, k in enumerate(models.PROVIDER_ORDER) if models.PROVIDER_META[k]["name"].lower() == lbl),
            None,
        ) if lbl else None
        if idx is None:
            idx = max(0, min(len(models.PROVIDER_ORDER) - 1, int(val.get("index", 0))))
        state.ui["add_kind"] = idx
        state.ui["flash"] = None
    elif eid == "add_token":
        state.ui["add_token"] = str(val or "")
        if kind == "submit" and models.PROVIDER_ORDER[state.ui["add_kind"]] != "custom":
            acc_id = state.add_token_account(
                models.PROVIDER_ORDER[state.ui["add_kind"]], state.ui["add_token"], state.ui["add_name"],
            )
            if acc_id:
                state.refresh_all(force=True, target_id=acc_id)
    elif eid == "add_name":
        state.ui["add_name"] = str(val or "")
    elif eid == "custom_url":
        state.ui["custom_url"] = str(val or "")
    elif eid == "custom_path":
        state.ui["custom_path"] = str(val or "")
    elif eid == "custom_limit_path":
        state.ui["custom_limit_path"] = str(val or "")
    elif eid == "custom_label":
        state.ui["custom_label"] = str(val or "")
    elif eid == "custom_mode" and isinstance(val, dict):
        state.ui["custom_mode"] = max(0, min(2, int(val.get("index", 0))))
    elif eid == "custom_color" and isinstance(val, dict):
        state.ui["custom_color_idx"] = max(
            0, min(len(models.CUSTOM_COLOR_CHOICES) - 1, int(val.get("index", 0))),
        )
    elif eid == "add_submit":
        acc_id = state.add_token_account(
            models.PROVIDER_ORDER[state.ui["add_kind"]], state.ui["add_token"], state.ui["add_name"],
        )
        if acc_id:
            state.refresh_all(force=True, target_id=acc_id)
    elif eid == "add_custom":
        acc_id = state.add_custom_account()
        if acc_id:
            state.refresh_all(force=True, target_id=acc_id)
    elif eid == "metric" and isinstance(val, dict):
        lbl = str(val.get("label") or "").lower()
        if "remaining" in lbl:
            state.settings["metric"] = "remaining"
        elif "used" in lbl:
            state.settings["metric"] = "used"
        else:
            state.settings["metric"] = "remaining" if val.get("index") == 1 else "used"
        state.save_settings()
    elif eid == "color_mode" and isinstance(val, dict):
        modes = ("brand", "accent", "traffic")
        state.settings["color_mode"] = modes[max(0, min(2, int(val.get("index", 0))))]
        state.save_settings()
    elif eid == "color_mode_basic" and isinstance(val, dict):
        state.settings["color_mode"] = "accent" if val.get("index") == 1 else "brand"
        state.save_settings()
    elif eid == "claude_icon" and isinstance(val, dict):
        state.settings["claude_icon"] = "pet" if val.get("index") == 1 else "logo"
        state.save_settings()
    elif eid == "countdown" and isinstance(val, dict):
        modes = ("cards", "bar", "off")
        state.settings["countdown"] = modes[max(0, min(2, int(val.get("index", 0))))]
        state.save_settings()
    elif eid == "countdown_compact" and isinstance(val, dict):
        state.settings["countdown"] = "off" if val.get("index") == 1 else "cards"
        state.save_settings()
    elif eid == "countdown_bar":
        state.settings["countdown"] = "bar" if bool(val) else "cards"
        state.save_settings()
    elif eid == "countdown_cards":
        if bool(val):
            state.settings["countdown"] = "bar" if state.settings["countdown"] == "bar" else "cards"
        else:
            state.settings["countdown"] = "off"
        state.save_settings()
    elif eid == "bar_mode" and isinstance(val, dict):
        modes = ("all", "tightest", "hidden")
        state.settings["bar_mode"] = modes[max(0, min(2, int(val.get("index", 0))))]
        state.save_settings()
    elif eid == "notch_mode" and isinstance(val, dict):
        state.settings["notch_mode"] = "tightest" if val.get("index") == 1 else "all"
        state.save_settings()
    elif eid == "bar_mode_simple" and isinstance(val, dict):
        state.settings["bar_mode"] = "hidden" if val.get("index") == 1 else "all"
        state.save_settings()
    elif eid == "warn" and isinstance(val, dict):
        state.settings["warn"] = WARN_LEVELS[max(0, min(len(WARN_LEVELS) - 1, int(val.get("index", 1))))]
        state.save_settings()
    elif eid == "interval" and isinstance(val, dict):
        state.settings["interval"] = INTERVALS[max(0, min(len(INTERVALS) - 1, int(val.get("index", 1))))]
        state.save_settings()
    elif eid in ("pill_badge", "auto_open"):
        state.settings[eid] = bool(val)
        state.save_settings()

    draw()


@app.on_message("hello")
@app.on_message("taskbar")
@app.on_message("theme")
def on_shell(_):
    # Reset Fenn SDK's deduplication cache so toggling the taskbar off/on re-sends bar/popup/preview.
    app._sent.clear()
    draw(force=True)


@app.on_message("popup_open")
@app.on_message("page_open")
def on_open(_):
    state.refresh_all(force=False)
    draw()


@app.on_message("layout")
def on_layout(msg):
    target = msg.get("target")
    w, h = msg.get("width"), msg.get("height")
    if target == "tile":
        if app.tile_size:
            app.save("tile_layout", {
                "size": app.tile_size,
                "width": app.tile_width,
                "height": app.tile_height,
            })
        draw(force=True)
    elif target in ("page", "popup", "preview") and w:
        state.ui[f"{target}_w"] = w
        if target == "page" and h:
            state.ui["page_h"] = h
        draw()


def maybe_tick():
    if not state.accounts:
        return
    now = time.time()
    if not state.ui["last_fetch"] or (now - state.ui["last_fetch"] >= state.settings["interval"]):
        state.refresh_all(force=False, min_age=state.settings["interval"])
    elif state.settings["countdown"] != "off" and (now - state.ui["last_redraw"] >= 55):
        # Keep reset countdowns ticking smoothly on screen between network fetches.
        draw()


if __name__ == "__main__":
    draw(force=True)
    if state.accounts:
        state.refresh_all(force=False)
    app.every(30, maybe_tick)
    app.run()
