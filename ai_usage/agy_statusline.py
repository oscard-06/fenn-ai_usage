"""Official Antigravity CLI statusLine receiver that forwards quota telemetry to AI Usage.

Reads status JSON from stdin per https://antigravity.google/docs/cli/statusline,
strips user email/transcript paths, and writes antigravity_status.json atomically.
"""

import argparse
import json
import os
from pathlib import Path
import sys
import time


def default_data_dir():
    env_dir = os.environ.get("FENN_DATA_DIR")
    if env_dir:
        return Path(env_dir)
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "Fenn" / "plugin-data" / "ai-usage"
    return Path.home() / ".local" / "share" / "fenn" / "plugin-data" / "ai-usage"


def sanitise_payload(raw):
    """Keeps only quota, tier, model display name, context usage, and estimated cost."""
    if not isinstance(raw, dict):
        return None
    model = raw.get("model") if isinstance(raw.get("model"), dict) else {}
    cost = raw.get("cost") if isinstance(raw.get("cost"), dict) else {}
    ctx = raw.get("context_window") if isinstance(raw.get("context_window"), dict) else {}
    quota = raw.get("quota") if isinstance(raw.get("quota"), (dict, list)) else {}
    out = {
        "product": raw.get("product") or "antigravity",
        "plan_tier": raw.get("plan_tier"),
        "model": model.get("display_name") or model.get("id"),
        "quota": quota,
        "updated_at": time.time(),
    }
    if isinstance(cost.get("total_usd"), (int, float)):
        out["cost_usd"] = float(cost["total_usd"])
    if isinstance(ctx.get("used_percentage"), (int, float)):
        out["context_pct"] = float(ctx["used_percentage"])
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description="Forward Antigravity statusLine quota JSON.")
    parser.add_argument("--data-dir", default="", help="Destination Fenn plugin data dir")
    parser.add_argument("--quiet", action="store_true", help="Suppress stdout status line")
    args = parser.parse_args(argv)

    raw_text = sys.stdin.read()
    if not raw_text.strip():
        return 0
    try:
        payload = json.loads(raw_text)
    except ValueError:
        return 0
    clean = sanitise_payload(payload)
    if not clean:
        return 0

    dest_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        target = dest_dir / "antigravity_status.json"
        tmp = dest_dir / f"antigravity_status.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(clean, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
