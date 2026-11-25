"""Brand icon resolution and custom icon upload/import helpers."""

import os
from pathlib import Path
import shutil
import subprocess
import sys

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
BUILTIN_ICONS_DIR = PLUGIN_ROOT / "assets" / "icons"
SUPPORTED_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".ico", ".webp"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

PICK_ICON_PS = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    "Add-Type -AssemblyName System.Windows.Forms;"
    "$o=New-Object System.Windows.Forms.Form -Property @{TopMost=$true};"
    "$d=New-Object System.Windows.Forms.OpenFileDialog;"
    "$d.Title='Choose a provider icon';"
    "$d.Filter='Images|*.png;*.jpg;*.jpeg;*.bmp;*.gif;*.ico;*.webp|All files|*.*';"
    "if($d.ShowDialog($o) -eq 'OK'){$d.FileName}"
)


def builtin_icon_path(kind, claude_icon="logo"):
    """Returns the bundled PNG path for a built-in provider kind."""
    if kind == "claude" and claude_icon == "pet":
        pet = BUILTIN_ICONS_DIR / "claude_pet.png"
        if pet.exists():
            return str(pet)
    candidate = BUILTIN_ICONS_DIR / f"{kind}.png"
    if candidate.exists():
        return str(candidate)
    fallback = BUILTIN_ICONS_DIR / "custom.png"
    return str(fallback) if fallback.exists() else None


def account_icon_path(account, claude_icon="logo"):
    """Returns the custom uploaded icon path if present, else the provider's brand PNG."""
    custom = account.get("custom_icon") if isinstance(account, dict) else None
    if custom and os.path.exists(custom):
        return str(custom)
    kind = account.get("kind", "custom") if isinstance(account, dict) else "custom"
    return builtin_icon_path(kind, claude_icon=claude_icon)


def pick_icon_file():
    """Opens the native Windows image picker and returns the chosen path or empty string."""
    if sys.platform != "win32":
        return ""
    try:
        done = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", PICK_ICON_PS],
            capture_output=True, timeout=300, creationflags=NO_WINDOW, check=False,
        )
        return done.stdout.decode("utf-8", errors="replace").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def import_custom_icon(src_path, data_dir, icon_id):
    """Imports and normalises a user-chosen image to a 64x64 PNG inside data_dir/icons.

    Returns the saved file path, or None if the source cannot be imported.
    """
    src = Path(src_path)
    if not src.is_file() or src.suffix.lower() not in SUPPORTED_EXTS:
        return None
    icons_dir = Path(data_dir) / "icons"
    icons_dir.mkdir(parents=True, exist_ok=True)
    safe_id = "".join(ch for ch in str(icon_id) if ch.isalnum() or ch in ("-", "_")) or "custom"
    dest_png = icons_dir / f"{safe_id}.png"

    if sys.platform == "win32":
        # Resize to 64x64 PNG via .NET System.Drawing so large user images never bloat Fenn.
        ps_src = str(src).replace("'", "''")
        ps_dst = str(dest_png).replace("'", "''")
        script = (
            "Add-Type -AssemblyName System.Drawing;"
            f"$img=[System.Drawing.Image]::FromFile('{ps_src}');"
            "$bmp=New-Object System.Drawing.Bitmap 64,64;"
            "$g=[System.Drawing.Graphics]::FromImage($bmp);"
            "$g.InterpolationMode=[System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic;"
            "$g.SmoothingMode=[System.Drawing.Drawing2D.SmoothingMode]::AntiAlias;"
            "$g.Clear([System.Drawing.Color]::Transparent);"
            "$s=[Math]::Min(64.0/$img.Width, 64.0/$img.Height);"
            "$w=[int][Math]::Round($img.Width*$s); $h=[int][Math]::Round($img.Height*$s);"
            "$x=[int]((64-$w)/2); $y=[int]((64-$h)/2);"
            "$g.DrawImage($img,$x,$y,$w,$h);"
            f"$bmp.Save('{ps_dst}',[System.Drawing.Imaging.ImageFormat]::Png);"
            "$g.Dispose(); $bmp.Dispose(); $img.Dispose()"
        )
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                capture_output=True, timeout=10, creationflags=NO_WINDOW, check=False,
            )
            if res.returncode == 0 and dest_png.exists():
                return str(dest_png)
        except (OSError, subprocess.SubprocessError):
            pass

    if src.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp"}:
        fallback = icons_dir / f"{safe_id}{src.suffix.lower()}"
        shutil.copy2(src, fallback)
        return str(fallback)
    return None
