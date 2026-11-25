"""Sleek frosted-glass canvas UI primitives modelled after Fenn's Grab layout."""

import re

import fenn

SIDE_W = 152
SIDE_GAP = 12

BLUE = "#4E8DF5"
AMBER = "#FF9F0A"
GREEN = "#30D158"
RED = "#FF453A"
WHITE = "#FFFFFF"

_WIDTHS = {
    " ": 0.27, ".": 0.27, ",": 0.27, ":": 0.28, ";": 0.28, "%": 0.82, "/": 0.36, "-": 0.36,
    "·": 0.27, "(": 0.33, ")": 0.33, "'": 0.22, "i": 0.25, "l": 0.25, "j": 0.25, "t": 0.34,
    "f": 0.33, "r": 0.36, "m": 0.83, "w": 0.75, "I": 0.28, "M": 0.84, "W": 0.92,
}


def text_width(text, size, bold=False):
    """Estimates proportional pixel width for canvas labels cleanly."""
    w = 0.0
    for ch in str(text):
        if ch in _WIDTHS:
            w += _WIDTHS[ch]
        elif ch.isdigit():
            w += 0.58
        elif ch.isupper():
            w += 0.66
        else:
            w += 0.53
    return w * size * (1.05 if bold else 1.0)


def fit_text(text, size, room, bold=False):
    """Truncates text with an ellipsis when exceeding room pixels."""
    s = str(text)
    if text_width(s, size, bold) <= room:
        return s
    while s and text_width(s + "…", size, bold) > room:
        s = s[:-1]
    return (s.rstrip() + "…") if s else "…"


def hex6(color):
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})([0-9a-fA-F]{2})?", str(color or "").strip())
    return "#" + m.group(1).upper() if m else None


def resolve_accent(app_accent):
    """Returns (accent_hex, accent_hi_hex, contrasting_ink_hex)."""
    base = hex6(app_accent) or "#7C5CFF"
    r, g, b = (int(base[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
    hi = "#" + "".join(
        f"{round(int(base[i:i + 2], 16) * 0.82 + 255 * 0.18):02X}" for i in (1, 3, 5)
    )
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    ink = "#1C1C1E" if lum > 0.52 else WHITE
    return base, hi, ink


def glass_colors(dark):
    return ("#FFFFFF0C", "#FFFFFF16") if dark else ("#0000000A", "#00000014")


class HitCanvas:
    """Declarative Fenn canvas builder with top-down hit region tracking."""

    def __init__(self, width, height):
        self.w = width
        self.h = height
        self.shapes = []
        self.regions = []

    def text(self, x, y, value, size=13, color="text", anchor="start", bold=False):
        self.shapes.append(fenn.label(x, y, value, size=size, color=color, anchor=anchor, bold=bold))

    def rect(self, x, y, w, h, fill=None, radius=0, stroke=None, width=1):
        self.shapes.append(fenn.rect(x, y, w, h, fill=fill, radius=radius, stroke=stroke,
                                     width=width if stroke else None))

    def glyph(self, x, y, name, size=14, color="text", fill=False):
        self.shapes.append(fenn.glyph(x, y, name, size, color, fill or None))

    def region(self, left, top, right, bottom, action):
        self.regions.append((left, top, right, bottom, action))

    def hit(self, x, y):
        for x0, y0, x1, y1, action in reversed(self.regions):
            if x0 <= x <= x1 and y0 <= y <= y1:
                return action
        return None

    def node(self, cid, tooltip=None):
        return fenn.canvas(self.h, self.shapes, width=self.w, id=cid, tooltip=tooltip)


NAV_ITEMS = (
    ("overview", "house", "Overview", None),
    ("-", None, "Monitors", None),
    ("limits", "chart-bar", "Live Limits", BLUE),
    ("alerts", "warning-circle", "Alerts", AMBER),
    ("accounts", "plug", "Accounts", GREEN),
    ("-", None, "Setup", None),
    ("connect", "plus-circle", "Connect AI", None),
)


def sidebar_canvas(width, height, current, badges, dark, accent_hex, ink_hex):
    """Draws the translucent rounded Grab-style left sidebar with bottom-docked Settings."""
    h = max(350, int(height))
    c = HitCanvas(width, h)
    fill, rim = glass_colors(dark)
    c.rect(0, 0, width, h, fill=fill, radius=14, stroke=rim)
    y = 10
    for key, icon_name, label, badge_col in NAV_ITEMS:
        if key == "-":
            y += 6
            c.text(14, y + 7, label, size=10.5, color="faint", bold=True)
            y += 16
            continue
        on = key == current
        if on:
            c.rect(6, y, width - 12, 30, fill=accent_hex, radius=8)
        fg = ink_hex if on else "text"
        c.glyph(23, y + 15, icon_name, 15, fg)
        c.text(39, y + 15, label, size=12.5, color=fg, bold=on)
        n = badges.get(key, 0)
        if badge_col and n:
            b_txt = str(n) if n < 100 else "99+"
            bw = max(18, text_width(b_txt, 10, True) + 10)
            c.rect(width - 12 - bw, y + 6, bw, 18, fill=badge_col, radius=9)
            b_ink = "#1C1C1E" if badge_col == AMBER else WHITE
            c.text(width - 12 - bw / 2, y + 15, b_txt, size=10, color=b_ink, anchor="center", bold=True)
        c.region(0, y, width, y + 30, f"nav:{key}")
        y += 33

    on = current == "settings"
    sy = h - 40
    c.rect(6, sy, width - 12, 32, fill=accent_hex if on else rim, radius=9)
    c.glyph(23, sy + 16, "gear-six", 15, ink_hex if on else "text")
    c.text(39, sy + 16, "Settings", size=12.5, color=ink_hex if on else "text", bold=on)
    c.region(0, sy, width, sy + 32, "nav:settings")
    return c


def hero_and_stats_canvas(width, cta_label, cta_icon, cta_action, tiles, dark, accent_hex, ink_hex):
    """Draws the full-width capsule CTA and 3 colour-tinted summary tiles matching Grab."""
    c = HitCanvas(width, 118)
    # Capsule CTA pill
    btn_h = 44
    c.rect(0, 0, width, btn_h, fill=accent_hex, radius=btn_h / 2)
    lbl_w = text_width(cta_label, 13, True)
    tx = width / 2.0 - (lbl_w + 22) / 2.0
    c.glyph(tx + 8, btn_h / 2.0, cta_icon, 16, ink_hex, True)
    c.text(tx + 22, btn_h / 2.0, cta_label, size=13, color=ink_hex, bold=True)
    c.region(0, 0, width, btn_h, cta_action)

    # Three colour-tinted summary cards
    gap = 10
    tw = (width - gap * 2) / 3.0
    y = 54
    th = 62
    for i, (value_txt, sub_txt, col, action) in enumerate(tiles[:3]):
        x = i * (tw + gap)
        tint = col + ("22" if dark else "1C")
        border = col + "36"
        c.rect(x, y, tw, th, fill=tint, radius=12, stroke=border)
        c.text(x + 13, y + 23, fit_text(value_txt, 20, tw - 24, True), size=20, color=col, bold=True)
        c.text(x + 13, y + 46, fit_text(sub_txt, 10.5, tw - 24), size=10.5, color="dim")
        c.region(x, y, x + tw, y + th, action)
    return c


def section_heading_canvas(width, title, count=None, link_text=None, link_action=None, accent_hi=None):
    """Draws a Grab-style section header with optional item count and right-aligned action link."""
    c = HitCanvas(width, 24)
    c.text(0, 13, title, size=13.5, color="text", bold=True)
    if count is not None:
        c.text(text_width(title, 13.5, True) + 8, 13, str(count), size=12, color="faint", bold=True)
    if link_text and link_action:
        col = accent_hi or BLUE
        c.text(width - 14, 13, link_text, size=11.5, color=col, anchor="end", bold=True)
        c.glyph(width - 6, 13, "caret-right", 11, col)
        c.region(max(0, width - 26 - text_width(link_text, 11.5, True)), 0, width, 24, link_action)
    return c


def empty_state_canvas(width, height, icon_name, title, subtitle, btn_label=None, btn_action=None,
                       accent_hex=None, ink_hex=WHITE):
    """Draws a clean centred empty-state canvas matching Grab."""
    h = max(210, int(height))
    c = HitCanvas(width, h)
    cy = max(36, h / 2.0 - 60)
    c.glyph(width / 2.0, cy, icon_name, 38, "faint")
    c.text(width / 2.0, cy + 42, title, size=14.5, color="text", anchor="center", bold=True)
    c.text(width / 2.0, cy + 64, fit_text(subtitle, 12, width - 24), size=12, color="dim", anchor="center")
    if btn_label and btn_action and accent_hex:
        bw = max(170, text_width(btn_label, 12.5, True) + 46)
        bx = width / 2.0 - bw / 2.0
        by = cy + 88
        c.rect(bx, by, bw, 36, fill=accent_hex, radius=18)
        c.text(width / 2.0, by + 18, btn_label, size=12.5, color=ink_hex, anchor="center", bold=True)
        c.region(bx, by, bx + bw, by + 36, btn_action)
    return c
