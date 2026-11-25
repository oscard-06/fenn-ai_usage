"""Plugin state, account management, metric rules, custom icons, and persistence."""

import os
import re
import threading
import time
import uuid

from ai_usage import cli_auth, fetchers, icons, models

_DUR_RE = re.compile(r"(?:^|\s)(\d+(?:\.\d+)?)\s*(m|h|d|w)$", re.I)
_DUR_UNIT_SECS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def _window_duration_seconds(label):
    """Returns duration in seconds for time-window labels like '5h' or 'Gemini 5h', else None."""
    m = _DUR_RE.search(str(label or "").strip())
    if not m:
        return None
    return float(m.group(1)) * _DUR_UNIT_SECS[m.group(2).lower()]

DEFAULT_SETTINGS = {
    "metric": "used",            # "used" | "remaining"
    "color_mode": "brand",       # "brand" | "accent" | "traffic"
    "claude_icon": "logo",       # "logo" | "pet"
    "countdown": "cards",        # "cards" | "bar" | "off"
    "bar_mode": "all",           # "all" | "tightest" | "hidden"
    "notch_mode": "all",         # "all" | "tightest"
    "account_windows": {},       # {account_id: window_label}; absent/None = default lower window
    "notch_windows": {},         # {account_id: window_label}; absent = follow taskbar window
    "hidden_counters": {},       # {account_id: [lower_window_label, ...]}
    "warn": 20,                  # 10 | 20 | 30 (% remaining threshold)
    "interval": 300,             # 120 | 300 | 900 seconds
    "pill_badge": True,
    "auto_open": False,
}

WARN_LEVELS = (10, 20, 30)
INTERVALS = (120, 300, 900)
STALE_OPEN_SECONDS = 120


class PluginState:
    """Holds persisted settings, linked accounts, cached results, and UI state."""

    def __init__(self, app):
        self.app = app
        self.settings = dict(DEFAULT_SETTINGS)
        self.settings["account_windows"] = {}
        self.settings["notch_windows"] = {}
        self.settings["hidden_counters"] = {}
        saved_settings = app.load("settings", {})
        if isinstance(saved_settings, dict):
            for k, v in saved_settings.items():
                if k in ("account_windows", "notch_windows") and isinstance(v, dict):
                    self.settings[k] = {
                        str(ak): str(av) for ak, av in v.items() if isinstance(av, str) and av
                    }
                elif k == "hidden_counters" and isinstance(v, dict):
                    self.settings["hidden_counters"] = {
                        str(ak): [str(x).lower() for x in av if isinstance(x, str) and x]
                        for ak, av in v.items() if isinstance(av, list)
                    }
                elif k in self.settings:
                    self.settings[k] = v

        accounts = app.load("accounts", [])
        self.accounts = accounts if isinstance(accounts, list) else []

        cache = app.load("cache", {})
        self.cache = cache if isinstance(cache, dict) else {}

        # Migrate legacy global window_mode into per-account selections on upgrade.
        if (
            isinstance(saved_settings, dict)
            and "account_windows" not in saved_settings
            and saved_settings.get("window_mode") in ("5h", "7d")
        ):
            legacy = saved_settings["window_mode"]
            for acc in self.accounts:
                cached = self.cache.get(acc.get("id"))
                wins = [w for w in ((cached or {}).get("windows") or []) if w.get("used_pct") is not None]
                match = next((w["label"] for w in wins if w["label"].lower() == legacy), None)
                if match:
                    self.settings["account_windows"][acc["id"]] = match

        cli_auth.repair_legacy_antigravity_statusline(self.app.data_dir)
        self.local_clis = cli_auth.detect_local_clis(data_dir=self.app.data_dir)
        self.last_sent = {"tile": None, "page": None, "badge": None}
        cached_times = [
            float(self.cache[a["id"]]["checked_at"])
            for a in self.accounts
            if isinstance(self.cache.get(a["id"]), dict) and self.cache[a["id"]].get("checked_at")
        ]
        initial_fetch = min(cached_times) if (self.accounts and len(cached_times) == len(self.accounts)) else 0.0
        self.ui = {
            "tab": 0,                  # 0=Usage, 1=Accounts, 2=Settings
            "nav": "overview",         # Grab-style expanded page nav: overview|limits|alerts|accounts|connect|settings
            "busy": False,
            "pending_refresh": False,
            "picking_icon": False,
            "last_fetch": initial_fetch,
            "last_redraw": 0.0,
            "warned": None,
            "flash": None,             # (message, colour)
            "add_kind": 0,
            "add_token": "",
            "add_name": "",
            "custom_url": "",
            "custom_path": "",
            "custom_limit_path": "",
            "custom_label": "Quota",
            "custom_mode": 0,          # 0=% used, 1=% left, 2=Used/Limit
            "custom_color_idx": 0,
            "custom_icon_path": "",
            "page_w": 640,
            "page_h": 428,
            "popup_w": 540,
            "preview_w": 290,
            "canvases": {},            # id -> ui_canvas.Canvas for hit-testing
        }

    # Persistence --------------------------------------------------------------

    def save_settings(self):
        self.app.save("settings", self.settings)

    def save_accounts(self):
        self.app.save("accounts", self.accounts)
        valid_ids = {a["id"] for a in self.accounts}
        for k in list(self.cache.keys()):
            if k not in valid_ids:
                self.cache.pop(k, None)
        self.app.save("cache", self.cache)
        changed = False
        for skey in ("account_windows", "notch_windows", "hidden_counters"):
            d = self.settings.get(skey)
            if isinstance(d, dict):
                stale = [k for k in d if k not in valid_ids]
                if stale:
                    for k in stale:
                        d.pop(k, None)
                    changed = True
        if changed:
            self.save_settings()

    def rescan_clis(self):
        self.local_clis = cli_auth.detect_local_clis(data_dir=self.app.data_dir)

    def agy_statusline_state(self):
        return cli_auth.antigravity_statusline_state()

    def enable_agy_statusline(self):
        ok, msg = cli_auth.enable_antigravity_statusline(self.app.data_dir)
        self.ui["flash"] = (msg, "green" if ok else "orange")
        if ok:
            self.rescan_clis()
        return ok

    def disable_agy_statusline(self):
        ok, msg = cli_auth.disable_antigravity_statusline()
        self.ui["flash"] = (msg, "green" if ok else "orange")
        if ok:
            self.rescan_clis()
        return ok

    # Metric, colour & countdown rules -----------------------------------------

    def display_pct(self, used_pct):
        """Converts internal used_pct (0..100) into Remaining or Used percentage."""
        if used_pct is None:
            return None
        val = float(used_pct)
        if self.settings["metric"] == "used":
            return max(0.0, min(100.0, val))
        return max(0.0, min(100.0, 100.0 - val))

    def remaining_pct(self, used_pct):
        if used_pct is None:
            return None
        return max(0.0, min(100.0, 100.0 - float(used_pct)))

    def is_critical(self, used_pct):
        rem = self.remaining_pct(used_pct)
        return rem is not None and rem <= self.settings["warn"]

    def is_warning(self, used_pct):
        rem = self.remaining_pct(used_pct)
        return rem is not None and rem <= min(50, self.settings["warn"] + 15)

    def pct_color(self, used_pct, status="ok", stale=False):
        """Returns alert-aware colour for percentage text."""
        if status == "auth" and not stale:
            return "red"
        if self.is_critical(used_pct):
            return "red"
        if self.is_warning(used_pct):
            return "orange"
        if stale and status == "auth":
            return "dim"
        return "text"

    def status_color(self, used_pct, status="ok", brand_color=None, stale=False):
        """Returns bar/accent colour following Brand, Fenn accent, or Traffic mode."""
        if status == "auth" and not stale:
            return "red"
        if used_pct is None and status == "error" and not stale:
            return "dim"
        mode = self.settings["color_mode"]
        if mode == "brand":
            return brand_color or "accent"
        if self.is_critical(used_pct):
            return "red"
        if self.is_warning(used_pct):
            return "orange"
        return "green" if mode == "traffic" else "accent"

    def segment_color(self, used_pct, status="ok", kind="custom", brand_color=None):
        """Returns a named Fenn colour for taskbar segments (Fenn segments use named colours)."""
        if status == "auth" or self.is_critical(used_pct):
            return "red"
        if used_pct is None and status == "error":
            return "dim"
        mode = self.settings["color_mode"]
        if mode == "brand":
            hex_to_named = {
                "#D97757": "orange",
                "#10A37F": "teal",
                "#4E8DF5": "blue",
                "#5B6EFF": "blue",
                "#8B5CF6": "purple",
                "#38BDF8": "teal",
                "#F59E0B": "yellow",
            }
            if brand_color in hex_to_named:
                return hex_to_named[brand_color]
            kind_map = {
                "claude": "orange",
                "chatgpt": "teal",
                "gemini": "blue",
                "antigravity": "blue",
                "openrouter": "purple",
                "copilot": "teal",
            }
            return kind_map.get(kind, "accent")
        if self.is_warning(used_pct):
            return "orange"
        return "green" if mode == "traffic" else "accent"

    def window_countdown(self, win, stale=False):
        """Computes fresh countdown string from reset_at when countdowns are enabled."""
        if self.settings["countdown"] == "off":
            return None
        reset_at = win.get("reset_at")
        if reset_at is not None:
            if stale and reset_at <= time.time():
                return "elapsed"
            return models.format_countdown(reset_at)
        return None

    def window_detail(self, win, stale=False):
        """Returns live reset detail or non-reset detail (e.g. dollar spend)."""
        if win.get("reset_at") is not None:
            if self.settings["countdown"] == "off":
                return None
            if stale and win["reset_at"] <= time.time():
                return "window elapsed"
            cd = models.format_countdown(win["reset_at"])
            # Preserve prefix like "240/300 left" if present before " · "
            raw = str(win.get("detail") or "")
            if " left" in raw and " · " in raw:
                prefix = raw.split(" · ", 1)[0]
                return f"{prefix} · {cd}" if cd else prefix
            return f"resets {cd}" if cd else None
        return win.get("detail")

    def visible_windows(self, res):
        """Returns account windows filtered by user per-counter visibility toggles."""
        hidden = set((self.settings.get("hidden_counters") or {}).get(res.get("id"), []))
        wins = res.get("windows") or []
        if not hidden:
            return wins
        return [w for w in wins if str(w.get("label") or "").strip().lower() not in hidden]

    def is_counter_visible(self, acc_id, label):
        hidden = set((self.settings.get("hidden_counters") or {}).get(acc_id, []))
        return str(label or "").strip().lower() not in hidden

    def set_counter_visible(self, acc_id, label, show):
        lbl_low = str(label or "").strip().lower()
        if not lbl_low:
            return
        hc = self.settings.setdefault("hidden_counters", {})
        cur = [x for x in hc.get(acc_id, []) if x != lbl_low]
        if not show:
            cur.append(lbl_low)
        if cur:
            hc[acc_id] = cur
        else:
            hc.pop(acc_id, None)
        self.save_settings()

    def account_window_options(self, res):
        """Returns visible metered window labels sorted shortest time window first (e.g. 5h before 7d)."""
        raw = []
        seen = set()
        for idx, w in enumerate(self.visible_windows(res)):
            if w.get("used_pct") is None:
                continue
            lbl = str(w.get("label") or "").strip()
            if lbl and lbl.lower() not in seen:
                seen.add(lbl.lower())
                dur = _window_duration_seconds(lbl)
                raw.append((0 if dur is not None else 1, dur or 0.0, idx, lbl))
        raw.sort(key=lambda item: (item[0], item[1], item[2]))
        return [item[3] for item in raw]

    def default_window_label(self, res):
        """Returns default headline window label (lowest time window, or primary metered bucket)."""
        opts = self.account_window_options(res)
        return opts[0] if opts else None

    def selected_window_label(self, res):
        """Returns active headline window choice ('Tightest', custom label, or lower default)."""
        opts = self.account_window_options(res)
        if not opts:
            return "Tightest"
        wanted = (self.settings.get("account_windows") or {}).get(res.get("id"))
        if wanted:
            if wanted.lower() == "tightest":
                return "Tightest"
            for opt in opts:
                if opt.lower() == wanted.lower():
                    return opt
        return opts[0]

    def set_account_window(self, acc_id, label_or_none):
        """Sets per-account headline window; None or lower default clears explicit override."""
        acc_wins = self.settings.setdefault("account_windows", {})
        acc = next((a for a in self.accounts if a["id"] == acc_id), None)
        default_lbl = self.default_window_label(self.result_for(acc)) if acc else None
        if not label_or_none or (default_lbl and str(label_or_none).lower() == default_lbl.lower()):
            acc_wins.pop(acc_id, None)
        elif str(label_or_none).lower() == "tightest":
            acc_wins[acc_id] = "Tightest"
        else:
            acc_wins[acc_id] = str(label_or_none)
        self.save_settings()

    def cycle_account_window(self, acc_id):
        """Cycles a multi-window account through its options (starting at lower window) and Tightest."""
        acc = next((a for a in self.accounts if a["id"] == acc_id), None)
        if not acc:
            return
        res = self.result_for(acc)
        opts = self.account_window_options(res)
        if len(opts) < 2:
            return
        order = opts + ["Tightest"]
        cur = self.selected_window_label(res)
        cur_low = cur.lower()
        idx = next(((i + 1) % len(order) for i, o in enumerate(order) if o.lower() == cur_low), 0)
        self.set_account_window(acc_id, order[idx])

    def has_kind(self, kind):
        return any(a.get("kind") == kind for a in self.accounts)

    def capabilities(self):
        """Summarises active model capabilities and window structures across linked accounts."""
        results = self.ordered_results()
        multi_window = []
        single_window = []
        info_only = []
        has_metered = False
        has_timers = False
        has_pending = False
        for r in results:
            if r.get("checked_at") is None:
                has_pending = True
            wins = self.visible_windows(r)
            if any(w.get("reset_at") is not None for w in wins):
                has_timers = True
            opts = self.account_window_options(r)
            if opts:
                has_metered = True
            if len(opts) >= 2:
                multi_window.append((r, opts))
            elif len(opts) == 1:
                single_window.append((r, opts[0]))
            else:
                info_only.append(r)
        return {
            "account_count": len(self.accounts),
            "has_claude": self.has_kind("claude"),
            "has_metered": has_metered,
            "has_timers": has_timers or has_pending,
            "has_pending": has_pending,
            "multi_window": multi_window,
            "single_window": single_window,
            "info_only": info_only,
        }

    def selected_notch_window_label(self, res):
        """Returns Notch tile window choice ('Follow' if inheriting taskbar, 'Tightest', or custom)."""
        opts = self.account_window_options(res)
        if not opts:
            return "Follow"
        wanted = (self.settings.get("notch_windows") or {}).get(res.get("id"))
        if wanted:
            if wanted.lower() == "tightest":
                return "Tightest"
            for opt in opts:
                if opt.lower() == wanted.lower():
                    return opt
        return "Follow"

    def set_notch_window(self, acc_id, label_or_none):
        nw = self.settings.setdefault("notch_windows", {})
        if not label_or_none or str(label_or_none).lower() in ("follow", "same", "follow taskbar"):
            nw.pop(acc_id, None)
        elif str(label_or_none).lower() == "tightest":
            nw[acc_id] = "Tightest"
        else:
            nw[acc_id] = str(label_or_none)
        self.save_settings()

    def _pick_window_by_pref(self, res, wanted):
        metered = [w for w in self.visible_windows(res) if w.get("used_pct") is not None]
        if not metered:
            return None
        if wanted:
            if wanted.lower() == "tightest":
                return max(metered, key=lambda w: w["used_pct"])
            match = next((w for w in metered if w["label"].lower() == wanted.lower()), None)
            if match:
                return match
        default_lbl = self.default_window_label(res)
        if default_lbl:
            match = next((w for w in metered if w["label"].lower() == default_lbl.lower()), None)
            if match:
                return match
        return metered[0]

    def headline_window(self, res):
        """Picks active taskbar headline window (pinned choice, 'Tightest', or default lower window)."""
        wanted = (self.settings.get("account_windows") or {}).get(res.get("id"))
        return self._pick_window_by_pref(res, wanted)

    def _pct_from_window(self, res, win):
        """Extracts used_pct from a chosen window or falls back to primary_pct unless hidden."""
        if win is not None:
            return win["used_pct"]
        if (self.settings.get("hidden_counters") or {}).get(res.get("id")):
            return None
        return res.get("primary_pct")

    def headline_pct(self, res):
        """Returns raw used_pct for the selected headline window (or None if all hidden)."""
        return self._pct_from_window(res, self.headline_window(res))

    def notch_headline_window(self, res):
        """Picks Notch tile window (notch override if set, else falls back to taskbar headline)."""
        wanted = (self.settings.get("notch_windows") or {}).get(res.get("id"))
        if wanted:
            return self._pick_window_by_pref(res, wanted)
        return self.headline_window(res)

    def notch_headline_pct(self, res):
        """Returns raw used_pct for the selected notch tile window (or None if all hidden)."""
        return self._pct_from_window(res, self.notch_headline_window(res))

    def tightest_countdown(self, res):
        """Returns reset countdown for the selected headline window when bar countdown is enabled."""
        if self.settings["countdown"] != "bar":
            return None
        hw = self.headline_window(res)
        if hw and hw.get("reset_at") is not None:
            target = hw["reset_at"]
        else:
            candidates = [
                w["reset_at"] for w in (res.get("windows") or [])
                if w.get("used_pct") is not None and w.get("reset_at") is not None
            ]
            if not candidates:
                return None
            target = min(candidates)
        if res.get("stale") and target <= time.time():
            return "ended"
        return models.format_countdown(target)

    def unlinked_detected_count(self):
        return sum(
            1
            for kind in ("claude", "chatgpt", "gemini", "antigravity", "copilot")
            if (self.local_clis.get(kind) or {}).get("found")
            and not any(a["kind"] == kind and a.get("mode") == "cli" for a in self.accounts)
        )

    def alert_results(self):
        """Returns linked results requiring attention (expired, errored, or crossing warn threshold)."""
        out = []
        for r in self.ordered_results():
            hp = self.headline_pct(r)
            if r.get("status") in ("auth", "error") or r.get("stale") or self.is_warning(hp):
                out.append(r)
        return out

    def nav_badges(self):
        """Returns badge counters for the expanded Grab-style sidebar."""
        caps = self.capabilities()
        return {
            "limits": len(caps["multi_window"]) + len(caps["single_window"]),
            "alerts": len(self.alert_results()),
            "accounts": len(self.accounts),
        }

    def result_for(self, acc):
        kind = acc.get("kind", "custom")
        meta = models.PROVIDER_META.get(kind, models.PROVIDER_META["custom"])
        cached = self.cache.get(acc["id"])
        if isinstance(cached, dict):
            res = dict(cached)
            res["name"] = acc.get("name") or meta["name"]
            res["badge"] = (acc.get("badge") or meta["badge"])[:1].upper()
            res["icon"] = meta["icon"]
            res["brand_color"] = acc.get("brand_color") or meta["brand_color"]
            res["custom_icon"] = acc.get("custom_icon")
            return res
        return {
            "id": acc["id"],
            "kind": kind,
            "name": acc.get("name") or meta["name"],
            "badge": (acc.get("badge") or meta["badge"])[:1].upper(),
            "icon": meta["icon"],
            "brand_color": acc.get("brand_color") or meta["brand_color"],
            "custom_icon": acc.get("custom_icon"),
            "plan": None,
            "windows": [],
            "primary_pct": None,
            "status": "info",
            "note": "Waiting for refresh…",
            "url": acc.get("url") or meta["url"],
            "checked_at": None,
        }

    def taskbar_icon(self, res):
        """Returns the Phosphor icon name for taskbar/canvas, respecting Claude logo vs Clawd pet."""
        if res.get("kind") == "claude" and self.settings.get("claude_icon") == "pet":
            return models.PROVIDER_META["claude"]["pet_icon"]
        meta = models.PROVIDER_META.get(res.get("kind", "custom"), models.PROVIDER_META["custom"])
        return meta["icon"]

    def ordered_results(self):
        return [self.result_for(a) for a in self.accounts]

    # Account & custom icon management -----------------------------------------

    def add_cli_account(self, kind):
        meta = models.PROVIDER_META[kind]
        existing = next((a for a in self.accounts if a["kind"] == kind and a.get("mode") == "cli"), None)
        if existing:
            self.ui["flash"] = (f"{meta['name']} CLI already linked", "dim")
            return existing["id"]
        acc = {
            "id": f"{kind}:cli:{uuid.uuid4().hex[:6]}",
            "kind": kind,
            "mode": "cli",
            "name": meta["name"],
            "badge": meta["badge"],
        }
        self.accounts.append(acc)
        self.save_accounts()
        self.ui["flash"] = (f"Linked {meta['name']}", "green")
        return acc["id"]

    def link_all_detected(self):
        self.rescan_clis()
        added = []
        for kind in ("claude", "chatgpt", "gemini", "antigravity", "copilot"):
            if self.local_clis.get(kind, {}).get("found"):
                if not any(a["kind"] == kind and a.get("mode") == "cli" for a in self.accounts):
                    added.append(self.add_cli_account(kind))
        if added:
            self.ui["flash"] = (f"Linked {len(added)} local CLI account(s)", "green")
            self.refresh_all(force=True)
        else:
            self.ui["flash"] = ("No new local CLIs found", "dim")

    def add_token_account(self, kind, token, custom_name=""):
        token = token.strip()
        if not token:
            self.ui["flash"] = ("Paste a token or API key first", "orange")
            return None
        meta = models.PROVIDER_META[kind]
        mode = "token"
        hint = models.mask_secret(token)
        if kind in ("openrouter", "gemini") or (kind == "chatgpt" and token.startswith("sk-")):
            mode = "api_key"
        elif kind == "claude" and token.startswith("sk-ant-admin"):
            mode = "admin_key"
        elif kind == "antigravity" and not token.startswith("ya29."):
            mode = "project"
            hint = token[:18]
        name = custom_name.strip() or meta["name"]
        acc = {
            "id": f"{kind}:{uuid.uuid4().hex[:6]}",
            "kind": kind,
            "mode": mode,
            "name": name,
            "badge": name[:1].upper() if custom_name.strip() else meta["badge"],
            "token": token,
            "hint": hint,
        }
        self.accounts.append(acc)
        self.save_accounts()
        self.ui["add_token"] = ""
        self.ui["add_name"] = ""
        self.ui["flash"] = (f"Linked {name}", "green")
        return acc["id"]

    def add_custom_account(self):
        name = self.ui["add_name"].strip() or "Custom AI"
        url = self.ui["custom_url"].strip()
        val_path = self.ui["custom_path"].strip()
        if not url:
            self.ui["flash"] = ("Enter a JSON endpoint URL", "orange")
            return None
        if not (url.startswith("https://") or url.startswith("http://localhost") or url.startswith("http://127.0.0.1")):
            self.ui["flash"] = ("URL must start with https:// or http://localhost", "orange")
            return None
        modes = ("used_pct", "remaining_pct", "used_of_limit")
        vmode = modes[self.ui["custom_mode"]]
        if vmode == "used_of_limit" and not self.ui["custom_limit_path"].strip():
            self.ui["flash"] = ("Enter a limit JSON path for Used / Limit mode", "orange")
            return None
        acc_id = f"custom:{uuid.uuid4().hex[:6]}"
        _, color_hex = models.CUSTOM_COLOR_CHOICES[self.ui["custom_color_idx"]]
        acc = {
            "id": acc_id,
            "kind": "custom",
            "mode": "custom",
            "name": name,
            "badge": name[:1].upper(),
            "brand_color": color_hex,
            "custom_icon": self.ui["custom_icon_path"] or None,
            "url": url,
            "token": self.ui["add_token"].strip(),
            "hint": models.mask_secret(self.ui["add_token"].strip()) if self.ui["add_token"].strip() else "No auth",
            "value_path": val_path,
            "limit_path": self.ui["custom_limit_path"].strip(),
            "value_mode": vmode,
            "window_label": self.ui["custom_label"].strip() or "Quota",
        }
        self.accounts.append(acc)
        self.save_accounts()
        self.ui["add_token"] = ""
        self.ui["add_name"] = ""
        self.ui["custom_url"] = ""
        self.ui["custom_path"] = ""
        self.ui["custom_limit_path"] = ""
        self.ui["custom_icon_path"] = ""
        self.ui["flash"] = (f"Added {name}", "green")
        return acc["id"]

    def start_icon_picker(self, target_id=None):
        """Launches the native file dialog on a worker thread so Fenn stays responsive."""
        if self.ui["picking_icon"]:
            return
        self.ui["picking_icon"] = True

        def work():
            chosen = icons.pick_icon_file()
            self.app.post({"type": "x-icon-picked", "path": chosen, "target_id": target_id})

        threading.Thread(target=work, daemon=True).start()

    def apply_icon_path(self, src_path, target_id=None):
        """Imports a selected or dropped image for the draft custom form or an existing account."""
        self.ui["picking_icon"] = False
        if not src_path:
            return False
        icon_id = target_id or f"draft_{uuid.uuid4().hex[:6]}"
        saved = icons.import_custom_icon(src_path, self.app.data_dir, icon_id)
        if not saved:
            self.ui["flash"] = ("Unsupported image file (use PNG, JPG, BMP, ICO, or WebP)", "orange")
            return False
        if target_id:
            acc = next((a for a in self.accounts if a["id"] == target_id), None)
            if acc:
                acc["custom_icon"] = saved
                self.save_accounts()
                self.ui["flash"] = (f"Updated icon for {acc['name']}", "green")
                return True
        self.ui["custom_icon_path"] = saved
        self.ui["flash"] = ("Custom icon ready", "green")
        return True

    def remove_account(self, target_id):
        removed = next((a for a in self.accounts if a["id"] == target_id), None)
        if removed and removed.get("custom_icon"):
            try:
                if os.path.exists(removed["custom_icon"]):
                    os.remove(removed["custom_icon"])
            except OSError:
                pass
        self.accounts[:] = [a for a in self.accounts if a["id"] != target_id]
        self.save_accounts()
        self.ui["flash"] = ("Account unlinked", "dim")

    # Background polling -------------------------------------------------------

    def refresh_all(self, force=False, target_id=None, min_age=STALE_OPEN_SECONDS):
        if not self.accounts:
            return
        if self.ui["busy"]:
            if force:
                self.ui["pending_refresh"] = True
            return
        now = time.time()
        snapshot = []
        for acc in self.accounts:
            if target_id is not None and acc["id"] != target_id:
                continue
            if not force:
                cached = self.cache.get(acc["id"])
                if isinstance(cached, dict):
                    retry_until = cached.get("retry_until")
                    if isinstance(retry_until, (int, float)) and now < retry_until:
                        continue
                    checked_at = cached.get("checked_at")
                    if isinstance(checked_at, (int, float)) and (now - checked_at) < min_age:
                        continue
            snapshot.append(dict(acc))
        if not snapshot:
            return
        self.ui["busy"] = True
        self.ui["pending_refresh"] = False

        def work():
            out = {}
            for acc in snapshot:
                if acc.get("kind") == "antigravity":
                    out[acc["id"]] = fetchers.fetch_account(acc, data_dir=self.app.data_dir)
                else:
                    out[acc["id"]] = fetchers.fetch_account(acc)
            self.app.post({"type": "x-usage", "results": out})

        threading.Thread(target=work, daemon=True).start()

    def apply_usage_message(self, msg):
        self.ui["busy"] = False
        self.ui["last_fetch"] = time.time()
        for acc_id, res in (msg.get("results") or {}).items():
            prev = self.cache.get(acc_id)
            if (
                res.get("status") in ("error", "auth")
                and not res.get("windows")
                and isinstance(prev, dict)
                and prev.get("windows")
            ):
                # Persist last known quota windows/meters during transient errors or expired auth.
                kept = dict(prev)
                kept["status"] = res.get("status")
                kept["stale"] = True
                kept["cached_at"] = prev.get("cached_at") or prev.get("checked_at")
                kept["checked_at"] = res.get("checked_at") or time.time()
                kept["retry_until"] = res.get("retry_until")
                base_note = res.get("note") or ("Session expired" if res.get("status") == "auth" else "Refresh failed")
                kept["note"] = base_note if "(cached)" in base_note else f"{base_note} (cached)"
                self.cache[acc_id] = kept
            else:
                clean = dict(res)
                clean["stale"] = False
                clean["cached_at"] = clean.get("checked_at")
                self.cache[acc_id] = clean
            if res.get("status") == "error" and res.get("note"):
                self.app.log(f"{res.get('name', acc_id)}: {res['note']}", "warn")
        self.app.save("cache", self.cache)
        if self.ui["pending_refresh"]:
            self.ui["pending_refresh"] = False
            self.refresh_all(force=True)
