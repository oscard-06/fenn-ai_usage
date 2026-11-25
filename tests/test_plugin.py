"""Unit and Fenn protocol integration tests for the AI Usage plugin."""

import base64
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fenn  # noqa: E402
from ai_usage import agy_statusline, cli_auth, fetchers, icons, models, state as state_mod, views  # noqa: E402

VALID_COLORS = {
    "text", "dim", "faint", "accent", "on_accent",
    "red", "orange", "yellow", "green", "teal", "blue", "purple", "pink",
    "white", "black", "transparent",
}
CONTROLS = {"button", "toggle", "slider", "input", "chips"}
VALID_NODES = {
    "column", "row", "grid", "card", "spacer", "divider",
    "text", "icon", "image", "progress", "ring", "sparkline", "list", "canvas",
    "button", "toggle", "slider", "input", "chips",
}


def validate_view(node, seen_ids=None):
    """Validates a Fenn UI view tree against Fenn protocol rules."""
    if seen_ids is None:
        seen_ids = set()
    assert isinstance(node, dict), f"node must be dict, got {type(node)}"
    ntype = node.get("type")
    assert ntype in VALID_NODES, f"unknown node type: {ntype}"
    if ntype in CONTROLS:
        cid = node.get("id")
        assert isinstance(cid, str) and cid, f"{ntype} missing id"
        assert cid not in seen_ids, f"duplicate control id in view: {cid}"
        seen_ids.add(cid)
    if ntype == "text":
        assert isinstance(node.get("text"), str), "text node must have str text"
    if ntype == "input" and "value" in node:
        assert isinstance(node["value"], str), "input value must be str"
    if "color" in node and node["color"] is not None:
        c = node["color"]
        assert c in VALID_COLORS or (isinstance(c, str) and c.startswith("#")), f"bad color: {c}"
    for child in node.get("children", []):
        validate_view(child, seen_ids)


def _fake_jwt(claims):
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode("ascii").rstrip("=")
    body = base64.urlsafe_b64encode(json.dumps(claims).encode("utf-8")).decode("ascii").rstrip("=")
    return f"{header}.{body}.sig"


class ProviderParserTests(unittest.TestCase):
    def test_claude_oauth_parser(self):
        sample = {
            "five_hour": {"utilization": 37.5, "resets_at": "2026-11-24T22:00:00Z"},
            "seven_day": {"utilization": 22.0, "resets_at": "2026-11-29T12:00:00Z"},
            "seven_day_opus": None,
            "seven_day_sonnet": {"utilization": 5.0, "resets_at": "2026-11-29T12:00:00Z"},
            "extra_usage": {"is_enabled": True, "monthly_limit": 5000, "used_credits": 1250},
        }
        wins, plan = fetchers.parse_claude_oauth_usage(sample, plan="max")
        self.assertEqual(plan, "Max")
        self.assertEqual([w["label"] for w in wins], ["5h", "7d", "Sonnet", "Extra"])
        self.assertAlmostEqual(wins[0]["used_pct"], 37.5)
        self.assertAlmostEqual(wins[3]["used_pct"], 25.0)
        self.assertIn("$12.50 / $50.00", wins[3]["detail"])

    def test_claude_admin_cost_report_parses_usd_decimal_strings(self):
        with mock.patch("ai_usage.fetchers.http_json", return_value={
            "data": [{"results": [{"amount": "142.50"}, {"amount": "3.15"}]}]
        }):
            res = fetchers.fetch_claude({"id": "c-adm", "kind": "claude", "mode": "admin_key",
                                         "token": "sk-ant-admin-xyz"})
        self.assertEqual(res["plan"], "Admin API")
        self.assertEqual(res["windows"][0]["detail"], "$145.65 USD")

    def test_claude_oauth_scope_error_gives_clear_guidance(self):
        err = fetchers.HttpApiError(403, "HTTP 403: OAuth token does not meet scope requirement user:profile")
        with mock.patch("ai_usage.fetchers.http_json", side_effect=err):
            res = fetchers.fetch_claude({"id": "c-oat", "kind": "claude", "mode": "token",
                                         "token": "sk-ant-oat01-xyz"})
        self.assertEqual(res["status"], "auth")
        self.assertIn("user:profile", res["note"])

    def test_chatgpt_wham_parser_and_jwt_account_id_extraction(self):
        sample = {
            "plan_type": "plus",
            "rate_limit": {
                "primary_window": {"used_percent": 15, "limit_window_seconds": 18000, "reset_after_seconds": 3600},
                "secondary_window": {"used_percent": 42, "limit_window_seconds": 604800, "reset_after_seconds": 86400},
            },
            "credits": {"has_credits": True, "unlimited": False, "balance": 10},
        }
        wins, plan, note = fetchers.parse_chatgpt_wham_usage(sample)
        self.assertEqual(plan, "Plus")
        self.assertEqual([w["label"] for w in wins], ["5h", "7d"])
        self.assertEqual(wins[0]["used_pct"], 15.0)
        self.assertEqual(note, "Credits: $10")

        fresh_jwt = _fake_jwt({
            "exp": time.time() + 3600,
            "https://api.openai.com/auth": {"chatgpt_account_id": "acct-999", "chatgpt_plan_type": "pro"},
        })
        seen_headers = {}

        def fake_http(url, headers=None, **kwargs):
            seen_headers.update(headers or {})
            return sample

        with mock.patch("ai_usage.fetchers.http_json", side_effect=fake_http):
            res = fetchers.fetch_chatgpt({"id": "o1", "kind": "chatgpt", "mode": "token", "token": fresh_jwt})
        self.assertEqual(res["status"], "ok")
        self.assertEqual(seen_headers.get("ChatGPT-Account-Id"), "acct-999")

        expired_jwt = _fake_jwt({"exp": time.time() - 60})
        res_exp = fetchers.fetch_chatgpt({"id": "o2", "kind": "chatgpt", "mode": "token", "token": expired_jwt})
        self.assertEqual(res_exp["status"], "auth")
        self.assertIn("expired", res_exp["note"].lower())

    def test_gemini_quota_parser_handles_model_id_and_token_type(self):
        sample = {
            "buckets": [
                {"modelId": "gemini-2.5-flash", "tokenType": "REQUESTS", "remainingFraction": 0.9},
                {"modelId": "gemini-2.5-pro", "tokenType": "REQUESTS", "remainingFraction": 0.25},
                {"modelId": "gemini-2.5-pro", "tokenType": "TOKENS", "remainingFraction": 0.50},
            ]
        }
        wins, plan = fetchers.parse_gemini_quota(sample, plan="Standard")
        self.assertEqual(plan, "Standard")
        self.assertEqual(wins[0]["label"], "2.5-pro")
        self.assertAlmostEqual(wins[0]["used_pct"], 75.0)
        self.assertEqual(wins[1]["label"], "2.5-pro tok")
        self.assertAlmostEqual(wins[1]["used_pct"], 50.0)

    def test_gemini_env_key_adc_and_unsupported_client_guidance(self):
        # GEMINI_API_KEY fallback verifies against generativelanguage API cleanly.
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "AIza-env-secret"}, clear=False), \
             mock.patch("ai_usage.cli_auth._read_json_file", return_value=None), \
             mock.patch("ai_usage.fetchers.http_json", return_value={"models": []}) as http_mock:
            res = fetchers.fetch_gemini({"id": "g-env", "kind": "gemini", "mode": "cli"})
        self.assertEqual(res["status"], "info")
        self.assertEqual(res["plan"], "GEMINI_API_KEY")
        self.assertEqual(http_mock.call_args[1]["headers"]["x-goog-api-key"], "AIza-env-secret")

        # gcloud ADC refreshes access token and passes quota_project_id to Code Assist.
        adc_info = {
            "source": "adc",
            "client_id": "cid",
            "client_secret": "csec",
            "refresh_token": "rtok",
            "quota_project": "my-gcp-proj",
            "expired": False,
        }
        calls = []

        def fake_adc_http(url, headers=None, method="GET", body=None, **kwargs):
            calls.append((url, body))
            if "oauth2.googleapis.com/token" in url:
                return {"access_token": "ya29.refreshed"}
            if "loadCodeAssist" in url:
                return {"currentTier": {"name": "Standard"}}
            return {"buckets": [{"modelId": "gemini-2.5-pro", "tokenType": "REQUESTS", "remainingFraction": 0.4}]}

        with mock.patch("ai_usage.fetchers.read_gemini_cli", return_value=adc_info), \
             mock.patch("ai_usage.fetchers.http_json", side_effect=fake_adc_http):
            adc_res = fetchers.fetch_gemini({"id": "g-adc", "kind": "gemini", "mode": "cli"})
        self.assertEqual(adc_res["status"], "ok")
        self.assertAlmostEqual(adc_res["windows"][0]["used_pct"], 60.0)
        self.assertEqual(calls[-1][1], {"project": "my-gcp-proj"})

        # Individual Code Assist UNSUPPORTED_CLIENT surfaces clear Antigravity migration note.
        oauth_info = {"source": "oauth", "access_token": "ya29.indiv", "expired": False}

        def fake_migrated(url, **kwargs):
            if "loadCodeAssist" in url:
                return {"ineligibleTiers": [{"reasonCode": "UNSUPPORTED_CLIENT"}]}
            raise fetchers.HttpApiError(403, "HTTP 403: Permission denied")

        with mock.patch("ai_usage.fetchers.read_gemini_cli", return_value=oauth_info), \
             mock.patch("ai_usage.fetchers.http_json", side_effect=fake_migrated):
            mig_res = fetchers.fetch_gemini({"id": "g-old", "kind": "gemini", "mode": "cli"})
        self.assertEqual(mig_res["status"], "auth")
        self.assertIn("Antigravity", mig_res["note"])

    def test_antigravity_statusline_parser_fetcher_and_fallback(self):
        snap = {
            "product": "antigravity",
            "plan_tier": "pro",
            "model": "Gemini 3.1 Pro",
            "cost_usd": 1.35,
            "updated_at": time.time() - 60,
            "quota": {
                "gemini": {"remaining_fraction": 0.80, "reset_in_seconds": 3600},
                "claude": {"remainingFraction": 0.35, "resetTime": "2027-01-01T00:00:00Z"},
            },
        }
        wins, plan, note = fetchers.parse_antigravity_status(snap)
        self.assertEqual(plan, "Pro")
        self.assertEqual(note, "Session $1.35")
        self.assertEqual([w["label"] for w in wins], ["Gemini", "Claude"])
        self.assertAlmostEqual(wins[0]["used_pct"], 20.0)
        self.assertAlmostEqual(wins[1]["used_pct"], 65.0)

        with tempfile.TemporaryDirectory() as data_dir:
            (Path(data_dir) / "antigravity_status.json").write_text(json.dumps(snap), encoding="utf-8")
            acc = {"id": "agy:1", "kind": "antigravity", "mode": "cli", "name": "Antigravity", "badge": "A"}
            res = fetchers.fetch_account(acc, data_dir=data_dir)
            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["plan"], "Pro")
            self.assertAlmostEqual(res["primary_pct"], 65.0)

        # Real agy bucket keys (gemini-5h, gemini-weekly, 3p-5h, 3p-weekly) format cleanly without active model noise.
        real_snap = {
            "product": "antigravity",
            "plan_tier": "Google AI Pro",
            "model": "Gemini 3.8 Flash (Low)",
            "quota": {
                "gemini-weekly": {"remaining_fraction": 0.69, "reset_in_seconds": 500000},
                "gemini-5h": {"remaining_fraction": 0.70, "reset_in_seconds": 16000},
                "3p-5h": {"remaining_fraction": 1.0, "reset_in_seconds": 18000},
                "3p-weekly": {"remaining_fraction": 1.0, "reset_in_seconds": 604800},
            },
        }
        with tempfile.TemporaryDirectory() as ddir:
            (Path(ddir) / "antigravity_status.json").write_text(json.dumps(real_snap), encoding="utf-8")
            r2 = fetchers.fetch_antigravity(
                {"id": "agy:real", "kind": "antigravity", "mode": "cli", "name": "Antigravity", "badge": "A"},
                data_dir=ddir,
            )
        self.assertIsNone(r2["note"])
        self.assertEqual({w["label"] for w in r2["windows"]}, {"Gemini 5h", "Gemini 7d", "3rd-Party 5h", "3rd-Party 7d"})

        # Consumer keyring session without statusLine falls back cleanly to info card.
        with tempfile.TemporaryDirectory() as empty_dir, \
             mock.patch("ai_usage.fetchers.read_antigravity_auth", return_value={
                 "access_token": "ya29.agy", "auth_method": "consumer", "expired": False, "label": "Antigravity (consumer)"
             }), \
             mock.patch("ai_usage.fetchers.antigravity_statusline_state", return_value="enabled"), \
             mock.patch("ai_usage.fetchers.read_antigravity_local_meta", return_value={
                 "installed": True, "model": "gemini-3-pro"
             }):
            info_res = fetchers.fetch_antigravity(
                {"id": "agy:2", "kind": "antigravity", "mode": "cli", "name": "Antigravity", "badge": "A"},
                data_dir=empty_dir,
            )
            self.assertEqual(info_res["status"], "info")
            self.assertEqual(info_res["windows"][0]["detail"], "Signed in")
            self.assertIn("statusLine ready", info_res["note"])

        # Enterprise GCP Project ID invokes Cloud Assist quota lookup cleanly.
        with tempfile.TemporaryDirectory() as empty_dir, \
             mock.patch("ai_usage.fetchers.read_antigravity_auth", return_value={
                 "access_token": "ya29.ent", "auth_method": "consumer", "expired": False, "label": "Antigravity"
             }), \
             mock.patch("ai_usage.fetchers.read_antigravity_local_meta", return_value={"installed": True, "model": None}), \
             mock.patch("ai_usage.fetchers._fetch_code_assist_quota", return_value={
                 "id": "agy:p", "kind": "antigravity", "name": "Antigravity", "badge": "A",
                 "icon": "planet", "brand_color": "#5B6EFF", "url": None,
                 "plan": "Enterprise", "status": "ok", "note": None, "retry_until": None,
                 "checked_at": time.time(), "windows": [models.make_window("3.1-pro", 12.0)], "primary_pct": 12.0,
             }) as ca_mock:
            proj_res = fetchers.fetch_antigravity(
                {"id": "agy:p", "kind": "antigravity", "mode": "project", "name": "Antigravity", "badge": "A", "token": "my-gcp-project"},
                data_dir=empty_dir,
            )
            self.assertEqual(proj_res["status"], "ok")
            self.assertEqual(ca_mock.call_args[1]["project"], "my-gcp-project")

    def test_antigravity_statusline_enable_disable_and_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            data_dir = Path(tmp) / "plugin data dir"
            home.mkdir()
            agy_dir = home / ".gemini" / "antigravity-cli"
            agy_dir.mkdir(parents=True)
            cfg_path = agy_dir / "settings.json"
            cfg_path.write_text(json.dumps({"model": "gemini-3-pro", "theme": "dark"}), encoding="utf-8")

            with mock.patch.object(Path, "home", return_value=home):
                self.assertEqual(cli_auth.antigravity_statusline_state(), "off")
                ok, _ = cli_auth.enable_antigravity_statusline(str(data_dir))
                self.assertTrue(ok)
                self.assertEqual(cli_auth.antigravity_statusline_state(), "enabled")

                saved_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                self.assertEqual(saved_cfg["model"], "gemini-3-pro")
                cmd = saved_cfg["statusLine"]["command"]

                # Simulate agy running statusLine via platform shell and piping PII + quota telemetry.
                sample_stdin = json.dumps({
                    "product": "antigravity",
                    "email": "secret-user@example.com",
                    "transcript_path": "C:\\Users\\secret\\session.jsonl",
                    "plan_tier": "pro",
                    "model": {"id": "gemini-3-pro", "display_name": "Gemini 3 Pro"},
                    "quota": {"gemini": {"remaining_fraction": 0.4, "reset_in_seconds": 1200}},
                    "cost": {"total_usd": 0.75},
                })
                if sys.platform == "win32":
                    proc = subprocess.run(
                        ["cmd", "/c", cmd],
                        input=sample_stdin, text=True, capture_output=True, timeout=10, check=False,
                    )
                else:
                    proc = subprocess.run(
                        ["/bin/sh", "-c", cmd],
                        input=sample_stdin, text=True, capture_output=True, timeout=10, check=False,
                    )
                self.assertEqual(proc.returncode, 0, f"stderr: {proc.stderr}")

                snap_path = data_dir / "antigravity_status.json"
                self.assertTrue(snap_path.is_file())
                raw_snap = snap_path.read_text(encoding="utf-8")
                self.assertNotIn("secret-user@example.com", raw_snap)
                self.assertNotIn("transcript_path", raw_snap)
                snap = cli_auth.read_antigravity_status(str(data_dir))
                self.assertEqual(snap["plan_tier"], "pro")
                self.assertAlmostEqual(snap["quota"]["gemini"]["remaining_fraction"], 0.4)
                clean_direct = agy_statusline.sanitise_payload({
                    "email": "x@y.z",
                    "context_window": {"used_percentage": 42.5},
                    "quota": {"groups": [{"displayName": "Gemini Models", "buckets": [
                        {"window": "5h", "remainingFraction": 0.75, "resetTime": "2027-01-01T00:00:00Z"},
                    ]}]},
                })
                self.assertNotIn("email", clean_direct)
                self.assertAlmostEqual(clean_direct["context_pct"], 42.5)
                grp_wins, _, _ = fetchers.parse_antigravity_status(clean_direct)
                self.assertEqual(len(grp_wins), 1)
                self.assertAlmostEqual(grp_wins[0]["used_pct"], 25.0)

                # Disable removes statusLine while preserving model/theme.
                ok_dis, _ = cli_auth.disable_antigravity_statusline()
                self.assertTrue(ok_dis)
                self.assertEqual(cli_auth.antigravity_statusline_state(), "off")
                after_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                self.assertEqual(after_cfg["model"], "gemini-3-pro")
                self.assertNotIn("statusLine", after_cfg)

                # Refuse to overwrite an existing custom statusLine hook.
                after_cfg["statusLine"] = {"type": "command", "command": "echo custom"}
                cfg_path.write_text(json.dumps(after_cfg), encoding="utf-8")
                self.assertEqual(cli_auth.antigravity_statusline_state(), "custom")
                ok_custom, msg_custom = cli_auth.enable_antigravity_statusline(str(data_dir))
                self.assertFalse(ok_custom)
                self.assertIn("Custom statusLine", msg_custom)

    def test_openrouter_and_copilot_parsers(self):
        or_wins, or_plan = fetchers.parse_openrouter_key({
            "data": {"limit": 50.0, "limit_remaining": 35.0, "usage": 15.0, "usage_daily": 1.25, "is_free_tier": False}
        })
        self.assertEqual(or_plan, "Pay-as-you-go")
        self.assertAlmostEqual(or_wins[0]["used_pct"], 30.0)
        self.assertIn("$35.00 left", or_wins[0]["detail"])

        cp_wins, cp_plan = fetchers.parse_copilot_user({
            "copilot_plan": "individual",
            "quota_reset_date_utc": "2026-12-01T00:00:00Z",
            "quota_snapshots": {
                "premium_interactions": {"entitlement": 300, "remaining": 240, "percent_remaining": 80.0, "unlimited": False},
                "completions": {"unlimited": True},
            },
        })
        self.assertEqual(cp_plan, "Individual")
        self.assertAlmostEqual(cp_wins[0]["used_pct"], 20.0)
        self.assertEqual(cp_wins[1]["detail"], "Unlimited")

    def test_custom_json_fetcher_and_secret_safety(self):
        captured_auth = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                captured_auth.append(self.headers.get("Authorization"))
                if self.path == "/fail":
                    self.send_response(401)
                    self.end_headers()
                    self.wfile.write(b'{"error":"bad token"}')
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"stats": {"used": 30, "limit": 120}}).encode())

            def log_message(self, *args):
                pass

        srv = HTTPServer(("127.0.0.1", 0), Handler)
        port = srv.server_port
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            res = fetchers.fetch_account({
                "id": "c1", "kind": "custom", "name": "LocalLLM", "badge": "L",
                "url": f"http://127.0.0.1:{port}/ok",
                "token": "super-secret-xyz",
                "value_path": "stats.used", "limit_path": "stats.limit",
                "value_mode": "used_of_limit", "window_label": "Quota",
            })
            self.assertEqual(res["status"], "ok")
            self.assertAlmostEqual(res["primary_pct"], 25.0)
            self.assertEqual(captured_auth[-1], "Bearer super-secret-xyz")

            err = fetchers.fetch_account({
                "id": "c2", "kind": "custom", "name": "LocalLLM", "badge": "L",
                "url": f"http://127.0.0.1:{port}/fail",
                "token": "super-secret-xyz",
                "value_path": "stats.used",
            })
            self.assertEqual(err["status"], "auth")
            self.assertNotIn("super-secret-xyz", err["note"])
        finally:
            srv.shutdown()
            srv.server_close()

    def test_local_cli_discovery_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude").mkdir()
            (home / ".claude" / ".credentials.json").write_text(json.dumps({
                "claudeAiOauth": {"accessToken": "tok-c", "subscriptionType": "max", "expiresAt": (time.time() + 3600) * 1000}
            }), encoding="utf-8")
            (home / ".codex").mkdir()
            (home / ".codex" / "auth.json").write_text(json.dumps({
                "tokens": {"access_token": _fake_jwt({"exp": time.time() + 3600}), "account_id": "acc-1"}
            }), encoding="utf-8")
            with mock.patch.object(Path, "home", return_value=home), \
                 mock.patch("ai_usage.cli_auth.read_antigravity_auth", return_value=None), \
                 mock.patch("ai_usage.cli_auth._agy_binary_exists", return_value=False), \
                 mock.patch("ai_usage.cli_auth._win32_read_credential", return_value=None), \
                 mock.patch.dict(os.environ, {}, clear=False):
                for var in ("CODEX_HOME", "GEMINI_CLI_HOME", "CLOUDSDK_CONFIG", "APPDATA",
                            "GEMINI_API_KEY", "GOOGLE_API_KEY", "COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
                    os.environ.pop(var, None)
                found = cli_auth.detect_local_clis()
            self.assertTrue(found["claude"]["found"])
            self.assertEqual(found["claude"]["label"], "max")
            self.assertTrue(found["chatgpt"]["found"])
            self.assertFalse(found["gemini"]["found"])
            self.assertFalse(found["antigravity"]["found"])

            # Detects Antigravity when signed in or installed locally, and Gemini CLI via $GEMINI_CLI_HOME.
            custom_gemini = Path(tmp) / "custom_gemini"
            (custom_gemini / ".gemini").mkdir(parents=True)
            (custom_gemini / ".gemini" / "oauth_creds.json").write_text(json.dumps({
                "access_token": "ya29.custom", "expiry_date": (time.time() + 3600) * 1000,
            }), encoding="utf-8")
            with mock.patch.object(Path, "home", return_value=home), \
                 mock.patch("ai_usage.cli_auth.read_antigravity_auth", return_value={
                     "access_token": "ya29.agy", "auth_method": "consumer", "expired": False, "label": "Antigravity (consumer)"
                 }), \
                 mock.patch("ai_usage.cli_auth._agy_binary_exists", return_value=True), \
                 mock.patch.dict(os.environ, {"GEMINI_CLI_HOME": str(custom_gemini)}, clear=False):
                found_agy = cli_auth.detect_local_clis()
            self.assertTrue(found_agy["gemini"]["found"])
            self.assertTrue(found_agy["antigravity"]["found"])
            self.assertEqual(found_agy["antigravity"]["label"], "Antigravity (consumer)")

            # Copilot CLI env & hosts.yml fallback discovery.
            with mock.patch.dict(os.environ, {"COPILOT_GITHUB_TOKEN": "gho_env_tok"}, clear=False):
                self.assertEqual(cli_auth.read_gh_cli_token(), "gho_env_tok")
            gh_dir = home / ".config" / "gh"
            gh_dir.mkdir(parents=True)
            (gh_dir / "hosts.yml").write_text("github.com:\n  oauth_token: gho_hosts_tok\n", encoding="utf-8")
            with mock.patch.object(Path, "home", return_value=home), \
                 mock.patch("ai_usage.cli_auth.shutil.which", return_value=None), \
                 mock.patch.dict(os.environ, {}, clear=False):
                for var in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN", "APPDATA"):
                    os.environ.pop(var, None)
                self.assertEqual(cli_auth.read_gh_cli_token(), "gho_hosts_tok")

    def test_usage_persistence_on_expired_auth_and_safe_cli_refresh(self):
        # Safe CLI refresh re-reads refreshed Claude credentials when initial token expired.
        seq = [
            {"access_token": "old", "plan": "pro", "expired": True},
            {"access_token": "new-fresh", "plan": "pro", "expired": False},
        ]
        with mock.patch("ai_usage.fetchers.read_claude_cli", return_value=seq[0]), \
             mock.patch("ai_usage.fetchers.refresh_claude_cli", return_value=seq[1]), \
             mock.patch("ai_usage.fetchers.http_json", return_value={
                 "five_hour": {"utilization": 18.0, "resets_at": "2026-11-26T00:00:00Z"},
                 "seven_day_cowork": {"utilization": 5.0, "resets_at": "2026-11-29T00:00:00Z"},
             }):
            res = fetchers.fetch_claude({"id": "c-ref", "kind": "claude", "mode": "cli"})
        self.assertEqual(res["status"], "ok")
        self.assertEqual([w["label"] for w in res["windows"]], ["5h", "Cowork"])

        # Expired auth preserves previously cached windows instead of clearing meters.
        with tempfile.TemporaryDirectory() as data_dir, \
             mock.patch.dict(os.environ, {"FENN_DATA_DIR": data_dir}):
            app = fenn.Plugin()
            st = state_mod.PluginState(app)
            st.accounts.append({"id": "cl:p", "kind": "claude", "mode": "cli", "name": "Claude", "badge": "C"})
            good = models.make_result(st.accounts[0], plan="Pro", windows=[
                models.make_window("5h", 24.0, "resets 3h", reset_at=time.time() - 60),
                models.make_window("7d", 71.0, "resets 4d", reset_at=time.time() + 300000),
            ])
            st.apply_usage_message({"results": {"cl:p": good}})
            auth_fail = models.make_result(st.accounts[0], plan="Pro", status="auth",
                                           note="Session expired — open 'claude' once to refresh")
            st.apply_usage_message({"results": {"cl:p": auth_fail}})
            cached = st.cache["cl:p"]
            self.assertEqual(cached["status"], "auth")
            self.assertTrue(cached["stale"])
            self.assertEqual(len(cached["windows"]), 2)
            self.assertIn("(cached)", cached["note"])
            self.assertEqual(st.window_detail(cached["windows"][0], stale=True), "window elapsed")
            bv, _ = views.bar_view(st)
            self.assertIn("24%", json.dumps(bv))
            exp_view = views.panel_view(st, 640)
            validate_view(exp_view)
            exp_json = json.dumps(exp_view)
            self.assertIn("24%", exp_json)
            self.assertIn("cached", exp_json)


class DynamicSizingTests(unittest.TestCase):
    def test_brand_colours_countdown_and_custom_icon_import(self):
        with tempfile.TemporaryDirectory() as data_dir, \
             mock.patch.dict(os.environ, {"FENN_DATA_DIR": data_dir}):
            app = fenn.Plugin()
            st = state_mod.PluginState(app)
            self.assertEqual(st.settings["color_mode"], "brand")
            st.accounts.append({"id": "cl:1", "kind": "claude", "mode": "cli", "name": "Claude", "badge": "C"})

            # Default metric is Used % and default window selects shortest time window (5h -> 8% used); 7d/Tightest pick 57%.
            self.assertEqual(st.settings["metric"], "used")
            st.cache["cl:1"] = models.make_result(
                st.accounts[0],
                windows=[
                    models.make_window("5h", 8.0, "resets 4h", reset_at=time.time() + 14400),
                    models.make_window("7d", 57.0, "resets 4d", reset_at=time.time() + 345600),
                ],
            )
            st.set_account_window("cl:1", None)
            self.assertEqual(views.bar_segments(st)[0]["text"], "8%")
            st.set_account_window("cl:1", "7d")
            self.assertEqual(views.bar_segments(st)[0]["text"], "57%")
            st.set_account_window("cl:1", "Tightest")
            self.assertEqual(views.bar_segments(st)[0]["text"], "57%")
            st.set_account_window("cl:1", "5h")
            self.assertEqual(views.bar_segments(st)[0]["text"], "8%")

            pv = views.preview_view(st)
            validate_view(pv)
            pv_json = json.dumps(pv)
            self.assertIn('"type": "card"', pv_json)
            self.assertIn('"type": "canvas"', pv_json)
            self.assertIn("8%", pv_json)
            self.assertIn("57%", pv_json)

            st.settings["metric"] = "remaining"
            self.assertEqual(views.bar_segments(st)[0]["text"], "92%")
            st.settings["metric"] = "used"

            bv, bw = views.bar_view(st)
            validate_view(bv)
            self.assertGreater(bw, 40)
            bv_json = json.dumps(bv)
            self.assertIn('"type": "image"', bv_json)
            self.assertIn('"used"', bv_json)
            st.settings["metric"] = "remaining"
            self.assertIn('"left"', json.dumps(views.bar_view(st)[0]))
            st.settings["metric"] = "used"
            st.set_account_window("cl:1", None)

            # Verify Claude brand colour & critical % text colour
            st.cache["cl:1"] = models.make_result(
                st.accounts[0],
                windows=[models.make_window("5h", 95.0, "resets 15m", reset_at=time.time() + 900)],
            )
            self.assertEqual(st.status_color(95.0, "ok", "#D97757"), "#D97757")
            self.assertEqual(st.segment_color(95.0, "ok", "claude", "#D97757"), "red")
            self.assertEqual(st.segment_color(30.0, "ok", "claude", "#D97757"), "orange")
            self.assertEqual(st.pct_color(95.0, "ok"), "red")
            self.assertIn("claude.png", icons.account_icon_path(st.accounts[0], claude_icon="logo"))
            self.assertIn("claude_pet.png", icons.account_icon_path(st.accounts[0], claude_icon="pet"))
            st.settings["claude_icon"] = "pet"
            self.assertEqual(st.taskbar_icon(st.accounts[0]), "alien")
            st.settings["claude_icon"] = "logo"
            self.assertEqual(st.taskbar_icon(st.accounts[0]), "asterisk")

            # Countdown in "bar" mode sets `sub` countdown on taskbar segment
            st.settings["countdown"] = "cards"
            segs_cards = views.bar_segments(st)
            self.assertNotIn("sub", segs_cards[0])
            st.settings["countdown"] = "bar"
            segs_bar = views.bar_segments(st)
            self.assertIn("sub", segs_bar[0])

            # Custom icon import works from an existing PNG (e.g. bundled icon)
            src_png = icons.builtin_icon_path("claude")
            self.assertTrue(st.apply_icon_path(src_png, target_id="cl:1"))
            self.assertTrue(os.path.exists(st.accounts[0]["custom_icon"]))

            # Transient 429 error preserves previously cached windows and sets retry_until cooldown
            err_res = models.make_result(st.accounts[0], status="error", note="Rate limited (429)", retry_after=180)
            st.apply_usage_message({"results": {"cl:1": err_res}})
            self.assertEqual(len(st.cache["cl:1"]["windows"]), 1)
            self.assertIn("cached", st.cache["cl:1"]["note"])
            self.assertGreater(st.cache["cl:1"]["retry_until"], time.time())

            # Non-forced refresh skips accounts in 429 cooldown while still fetching others
            st.accounts.append({"id": "cg:1", "kind": "chatgpt", "mode": "token", "name": "ChatGPT", "badge": "O"})
            fetched_ids = []

            def fake_fetch(acc):
                fetched_ids.append(acc["id"])
                return models.make_result(acc, windows=[models.make_window("5h", 10.0)])

            with mock.patch("ai_usage.state.fetchers.fetch_account", side_effect=fake_fetch):
                st.refresh_all(force=False)
                deadline = time.time() + 2.0
                while st.ui["busy"] and time.time() < deadline:
                    time.sleep(0.02)
            self.assertEqual(fetched_ids, ["cg:1"])

    def test_multi_link_scales_bar_overflow_tile_and_bento_grid(self):
        with tempfile.TemporaryDirectory() as data_dir, \
             mock.patch.dict(os.environ, {"FENN_DATA_DIR": data_dir}):
            app = fenn.Plugin()
            st = state_mod.PluginState(app)
            for i in range(6):
                aid = f"custom:{i}"
                st.accounts.append({"id": aid, "kind": "custom", "mode": "custom",
                                    "name": f"Prov{i}", "badge": str(i)})
                st.cache[aid] = models.make_result(
                    st.accounts[-1],
                    windows=[models.make_window("5h", 10.0 * (i + 1)), models.make_window("7d", 5.0 * (i + 1))],
                )

            # Taskbar segments cap at 5 with +2 overflow and tightest account (60% used) first.
            segs = views.bar_segments(st)
            self.assertEqual(len(segs), 5)
            self.assertEqual(segs[0]["text"], "60%")
            self.assertEqual(segs[-1]["text"], "+2")

            # Wide page uses Grab-style sidebar + 2-column grid in main pane; popup keeps 1-column stack.
            page_view = views.panel_view(st, 640)
            validate_view(page_view)
            self.assertEqual(page_view.get("type"), "row")
            self.assertEqual(page_view["children"][0].get("id"), "pg_side")
            right_col = page_view["children"][1]
            self.assertTrue(any(c.get("type") == "grid" and c.get("columns") == 2 for c in right_col["children"]))

            popup_view = views.panel_view(st, 336)
            validate_view(popup_view)
            self.assertFalse(any(c.get("type") == "grid" for c in popup_view["children"]))

            # Tile adapts across small, wide (2x2 for 4+ links), and large (with PNG icons + % used header).
            for tsize, tw, th in (("small", 44, 44), ("wide", 104, 36), ("large", 104, 104)):
                app.tile_size, app.tile_width, app.tile_height = tsize, tw, th
                tv = views.tile_view(st)
                validate_view(tv)
                if tsize == "large":
                    tv_json = json.dumps(tv)
                    self.assertEqual(tv.get("type"), "column")
                    self.assertIn('"type": "image"', tv_json)
                    self.assertIn("% used", tv_json)
                    self.assertIn("+3 more", tv_json)

            # Even if tile_size isn't sent yet while height >= 72, large layout renders cleanly.
            app.tile_size, app.tile_width, app.tile_height = None, 104, 104
            self.assertEqual(views.tile_view(st).get("type"), "column")

            # Single-account wide tile displays up to 2 windows (5h & 7d).
            st.accounts = st.accounts[:1]
            app.tile_size, app.tile_width, app.tile_height = "wide", 104, 36
            single_wide = views.tile_view(st)
            validate_view(single_wide)
            texts = [s["text"] for s in single_wide["shapes"] if s.get("type") == "text"]
            self.assertTrue(any("5h" in t for t in texts))
            self.assertTrue(any("7d" in t for t in texts))

            # Single-account large tile displays PNG icon, % used header, and windows.
            app.tile_size, app.tile_width, app.tile_height = "large", 104, 104
            single_large = views.tile_view(st)
            validate_view(single_large)
            self.assertEqual(single_large.get("type"), "column")
            self.assertIn("% used", json.dumps(single_large))

    def test_dynamic_settings_and_custom_windows_per_model(self):
        with tempfile.TemporaryDirectory() as data_dir, \
             mock.patch.dict(os.environ, {"FENN_DATA_DIR": data_dir}):
            app = fenn.Plugin()
            st = state_mod.PluginState(app)
            st.ui["tab"] = 2

            # 0 accounts: adapts Settings cleanly without Claude mascot, reset timers, or window pickers.
            empty_settings = json.dumps(views.panel_view(st, 360))
            self.assertIn("bar_mode_simple", empty_settings)
            self.assertNotIn("claude_icon", empty_settings)
            self.assertNotIn("acc_win:", empty_settings)

            # Claude (7d listed before 5h to test duration sorting) + ChatGPT Free (30d only):
            # default must still pick lower time window (5h -> 81%) on both taskbar and notch tile.
            st.accounts = [
                {"id": "cl:1", "kind": "claude", "mode": "cli", "name": "Claude", "badge": "C"},
                {"id": "cg:1", "kind": "chatgpt", "mode": "cli", "name": "ChatGPT", "badge": "O"},
            ]
            st.cache["cl:1"] = models.make_result(
                st.accounts[0],
                windows=[
                    models.make_window("7d", 59.0, "resets 4d", reset_at=time.time() + 345600),
                    models.make_window("5h", 19.0, "resets 4h", reset_at=time.time() + 14400),
                ],
            )
            st.cache["cg:1"] = models.make_result(
                st.accounts[1],
                windows=[models.make_window("30d", 0.0, "resets 29d", reset_at=time.time() + 2505600)],
            )

            self.assertEqual(st.selected_window_label(st.result_for(st.accounts[0])), "5h")
            bv_default, _ = views.bar_view(st)
            self.assertIn("19%", json.dumps(bv_default))
            self.assertNotIn("59%", json.dumps(bv_default))
            app.tile_size, app.tile_width, app.tile_height = "small", 44, 44
            self.assertIn('"19"', json.dumps(views.tile_view(st)))

            settings_panel = views.panel_view(st, 360)
            validate_view(settings_panel)
            s_json = json.dumps(settings_panel)
            self.assertIn('"id": "claude_icon"', s_json)
            self.assertIn('"id": "acc_win:cl:1"', s_json)
            self.assertIn('"options": ["5h", "7d", "Tightest"]', s_json)
            self.assertIn("Warn above used", s_json)
            self.assertIn("90%", s_json)
            self.assertNotIn('"acc_win:cg:1"', s_json)
            self.assertIn("30d only", s_json)
            self.assertIn('"id": "bar_mode"', s_json)

            menu = views.bar_menu(st)
            menu_ids = [item["id"] for item in menu]
            self.assertIn("cycle_acc:cl:1", menu_ids)
            self.assertTrue(any(m["id"] == "cycle_acc:cl:1" and "5h" in m["label"] for m in menu))
            self.assertNotIn("cycle_acc:cg:1", menu_ids)

            # Selecting Tightest switches Claude to 59% (7d); hiding taskbar still keeps window selector for Notch.
            st.set_account_window("cl:1", "Tightest")
            bv_tight, _ = views.bar_view(st)
            self.assertIn("59%", json.dumps(bv_tight))
            st.settings["bar_mode"] = "hidden"
            self.assertIn('"id": "acc_win:cl:1"', json.dumps(views.panel_view(st, 360)))
            st.settings["bar_mode"] = "all"

            st.settings["metric"] = "remaining"
            rem_settings_json = json.dumps(views.panel_view(st, 360))
            self.assertIn("Warn below left", rem_settings_json)
            self.assertIn("20%", rem_settings_json)
            st.settings["metric"] = "used"

            # Unlink Claude, add Gemini (2.5-pro & 2.5-flash): hides Claude icon, prunes cl:1, builds Gemini window chips.
            st.remove_account("cl:1")
            self.assertNotIn("cl:1", st.settings["account_windows"])
            gm_acc = {"id": "gm:1", "kind": "gemini", "mode": "cli", "name": "Gemini", "badge": "G"}
            st.accounts.append(gm_acc)
            st.cache["gm:1"] = models.make_result(
                gm_acc,
                windows=[
                    models.make_window("2.5-pro", 70.0, "resets 5h", reset_at=time.time() + 18000),
                    models.make_window("2.5-flash", 20.0, "resets 5h", reset_at=time.time() + 18000),
                ],
            )
            gm_settings = views.panel_view(st, 360)
            validate_view(gm_settings)
            gm_json = json.dumps(gm_settings)
            self.assertNotIn("claude_icon", gm_json)
            self.assertIn('"id": "acc_win:gm:1"', gm_json)
            self.assertIn('"options": ["2.5-pro", "2.5-flash", "Tightest"]', gm_json)
            self.assertEqual(st.selected_window_label(st.result_for(gm_acc)), "2.5-pro")
            st.cycle_account_window("gm:1")  # 2.5-pro -> 2.5-flash (20% used)
            self.assertEqual(st.selected_window_label(st.result_for(gm_acc)), "2.5-flash")
            self.assertIn("20%", json.dumps(views.bar_view(st)[0]))

            # Spend-only account shows concise $ spend instead of generic "OK" in taskbar.
            st.accounts = [{"id": "or:1", "kind": "openrouter", "mode": "api_key", "name": "OpenRouter", "badge": "R"}]
            st.cache = {"or:1": models.make_result(st.accounts[0], status="info", windows=[models.make_window("Month", None, "$4.20")])}
            or_settings_json = json.dumps(views.panel_view(st, 360))
            self.assertNotIn('"id": "metric"', or_settings_json)
            self.assertNotIn('"id": "warn"', or_settings_json)
            self.assertNotIn("countdown", or_settings_json)
            self.assertIn('"id": "color_mode_basic"', or_settings_json)
            self.assertIn('"id": "bar_mode_simple"', or_settings_json)
            self.assertNotIn("flip_metric", [m["id"] for m in views.bar_menu(st)])
            or_bar_json = json.dumps(views.bar_view(st)[0])
            self.assertIn("$4.20", or_bar_json)
            self.assertNotIn('"OK"', or_bar_json)

            # Antigravity icon, blue brand segment, Setup/Sync label (not "OK"), and Grab expanded routes.
            self.assertIn("antigravity.png", str(icons.builtin_icon_path("antigravity") or ""))
            st.ui["tab"] = 0
            st.ui["nav"] = "overview"
            st.accounts = [{"id": "agy:1", "kind": "antigravity", "mode": "cli", "name": "Antigravity", "badge": "A"}]
            st.cache = {"agy:1": models.make_result(
                st.accounts[0], plan="Consumer", status="info",
                windows=[models.make_window("Session", None, "Signed in")],
                note="Enable statusLine sync",
            )}
            with mock.patch.object(st, "agy_statusline_state", return_value="off"):
                agy_overview = views.panel_view(st, 360)
                agy_bar_json = json.dumps(views.bar_view(st)[0])
            validate_view(agy_overview)
            self.assertIn('"id": "agy_sl_enable:agy:1"', json.dumps(agy_overview))
            self.assertIn('"Setup"', agy_bar_json)
            self.assertNotIn('"OK"', agy_bar_json)
            self.assertEqual(st.segment_color(45.0, "ok", "antigravity", "#5B6EFF"), "blue")

            # Expanded Grab-style layout renders valid views and hit regions for both page (640) and popup (540).
            for route in ("overview", "limits", "alerts", "accounts", "connect", "settings"):
                st.ui["nav"] = route
                validate_view(views.panel_view(st, 640, surface="page"))
                validate_view(views.panel_view(st, 540, surface="popup"))
            st.ui["nav"] = "overview"
            views.panel_view(st, 640, surface="page")
            views.panel_view(st, 540, surface="popup")
            self.assertEqual(st.ui["canvases"]["pg_side"].hit(30, 385), "nav:settings")
            self.assertEqual(st.ui["canvases"]["pop_side"].hit(30, 385), "nav:settings")
            self.assertEqual(st.ui["canvases"]["pg_top"].hit(20, 80), "nav:limits")

            # Hover preview shows ALL 4 Antigravity counters; per-counter toggles hide individual bars;
            # Taskbar and Notch tile windows configure independently and wide chips wrap via _opt buttons.
            st.cache["agy:1"] = models.make_result(
                st.accounts[0], plan="Google AI Pro", status="ok",
                windows=[
                    models.make_window("Gemini 5h", 30.0, "resets 4h", reset_at=time.time() + 14400),
                    models.make_window("Gemini 7d", 31.0, "resets 6d", reset_at=time.time() + 500000),
                    models.make_window("3rd-Party 5h", 10.0, "resets 5h", reset_at=time.time() + 17000),
                    models.make_window("3rd-Party 7d", 5.0, "resets 7d", reset_at=time.time() + 600000),
                ],
            )
            pv_all = json.dumps(views.preview_view(st))
            for lbl in ("Gemini 5h", "Gemini 7d", "3rd-Party 5h", "3rd-Party 7d"):
                self.assertIn(lbl, pv_all)
            st.set_counter_visible("agy:1", "3rd-Party 7d", False)
            pv_filtered = json.dumps(views.preview_view(st))
            self.assertIn("3rd-Party 5h", pv_filtered)
            self.assertNotIn("3rd-Party 7d", pv_filtered)

            st.set_account_window("agy:1", "Gemini 5h")
            st.set_notch_window("agy:1", "Gemini 7d")
            self.assertIn("30%", json.dumps(views.bar_view(st)[0]))
            app.tile_size, app.tile_width, app.tile_height = "small", 44, 44
            self.assertIn('"31"', json.dumps(views.tile_view(st)))

            st.ui["nav"] = "settings"
            s_pop = views.panel_view(st, 540, surface="popup")
            validate_view(s_pop)
            s_pop_json = json.dumps(s_pop)
            self.assertIn("cnt_vis:agy:1:3", s_pop_json)
            self.assertIn("acc_win:agy:1_opt:", s_pop_json)
            self.assertIn("notch_win:agy:1_opt:", s_pop_json)
            st.set_counter_visible("agy:1", "3rd-Party 7d", True)

            # Legacy window_mode migration test.
            app.save("settings", {"window_mode": "5h"})
            app.save("accounts", [{"id": "cl:legacy", "kind": "claude", "mode": "cli", "name": "Claude", "badge": "C"}])
            app.save("cache", {"cl:legacy": {"windows": [{"label": "5h", "used_pct": 10.0}, {"label": "7d", "used_pct": 50.0}]}})
            migrated = state_mod.PluginState(app)
            self.assertEqual(migrated.settings["account_windows"].get("cl:legacy"), "5h")


class ProtocolIntegrationTests(unittest.TestCase):
    def test_live_protocol_and_taskbar_messages(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = os.path.join(tmp, "data")
            home_dir = os.path.join(tmp, "home")
            os.makedirs(data_dir, exist_ok=True)
            os.makedirs(home_dir, exist_ok=True)
            env = dict(os.environ)
            env["HOME"] = home_dir
            env["USERPROFILE"] = home_dir
            env["FENN_DATA_DIR"] = data_dir
            env["FENN_PLUGIN_ID"] = "ai-usage"
            env["FENN_THEME"] = "dark"
            env["FENN_ACCENT"] = "#A29F9F"
            env["PYTHONUNBUFFERED"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"

            proc = subprocess.Popen(
                [sys.executable, str(ROOT / "main.py")],
                cwd=str(ROOT),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                env=env,
            )

            events = [
                {"type": "hello", "protocol": 1, "fenn_version": "0.6.0", "plugin_id": "ai-usage",
                 "data_dir": data_dir, "taskbar": True, "theme": {"dark": True, "accent": "#A29F9F"}},
                {"type": "layout", "target": "tile", "size": "small", "width": 44, "height": 44},
                {"type": "layout", "target": "tile", "size": "wide", "width": 104, "height": 36},
                {"type": "layout", "target": "tile", "size": "large", "width": 104, "height": 104},
                {"type": "layout", "target": "popup", "width": 360, "height": 420},
                {"type": "layout", "target": "page", "width": 640, "height": 428},
                {"type": "layout", "target": "preview", "width": 270, "height": 180},
                {"type": "event", "id": "pg_side", "kind": "click", "value": {"x": 30, "y": 395}},
                {"type": "event", "id": "tab", "kind": "change", "value": {"index": 1, "label": "Accounts"}},
                {"type": "event", "id": "add_kind", "kind": "change", "value": {"index": 6, "label": "Custom"}},
                {"type": "event", "id": "rescan", "kind": "click"},
                {"type": "event", "id": "tab", "kind": "change", "value": {"index": 2, "label": "Settings"}},
                {"type": "event", "id": "metric", "kind": "change", "value": {"index": 1, "label": "Remaining %"}},
                {"type": "event", "id": "bar_mode", "kind": "change", "value": {"index": 2, "label": "Hidden"}},
                {"type": "event", "id": "bar_mode", "kind": "change", "value": {"index": 0, "label": "All"}},
                {"type": "taskbar", "on": False},
                {"type": "taskbar", "on": True},
                {"type": "theme", "dark": False, "accent": "#3B82F6"},
                {"type": "shutdown"},
            ]
            stdin_payload = "\n".join(json.dumps(m) for m in events) + "\n"
            stdout, stderr = proc.communicate(input=stdin_payload, timeout=10)
            self.assertEqual(proc.returncode, 0, f"stderr: {stderr}")

            messages = [json.loads(line) for line in stdout.splitlines() if line.strip()]
            types = {m["type"] for m in messages}
            self.assertTrue({"tile", "page", "badge", "bar", "preview", "popup"} <= types,
                            f"missing message types in {types}")

            for m in messages:
                self.assertFalse(m.get("type") == "log" and m.get("level") == "error", f"plugin logged error: {m}")
                if m["type"] in ("tile", "page", "preview", "popup") and m.get("view") is not None:
                    validate_view(m["view"])
                if m["type"] == "bar":
                    if m.get("view") is not None:
                        validate_view(m["view"])
                        self.assertGreater(m.get("width", 0), 0)
                        self.assertNotIn("segments", m)
                    for seg in m.get("segments", []):
                        self.assertIn("text", seg)
                        self.assertIsInstance(seg["text"], str)

            self.assertTrue(any(m["type"] == "bar" and m.get("segments") == [] for m in messages))
            # Verify toggling taskbar off then back on re-emits the custom bar view payload
            non_empty_bars = [m for m in messages if m["type"] == "bar" and (m.get("segments") or m.get("view"))]
            self.assertGreaterEqual(len(non_empty_bars), 4)

    def test_end_to_end_link_custom_endpoint_via_stdin(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"quota": {"used_pct": 88.0}}).encode())

            def log_message(self, *args):
                pass

        srv = HTTPServer(("127.0.0.1", 0), Handler)
        port = srv.server_port
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                data_dir = os.path.join(tmp, "data")
                home_dir = os.path.join(tmp, "home")
                os.makedirs(data_dir, exist_ok=True)
                os.makedirs(home_dir, exist_ok=True)
                env = dict(os.environ)
                env["HOME"] = home_dir
                env["USERPROFILE"] = home_dir
                env["FENN_DATA_DIR"] = data_dir
                env["FENN_PLUGIN_ID"] = "ai-usage"
                env["PYTHONUNBUFFERED"] = "1"
                env["PYTHONIOENCODING"] = "utf-8"

                proc = subprocess.Popen(
                    [sys.executable, str(ROOT / "main.py")],
                    cwd=str(ROOT),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    env=env,
                )
                assert proc.stdin is not None and proc.stdout is not None
                cmds = [
                    {"type": "hello", "protocol": 1, "fenn_version": "0.6.0", "plugin_id": "ai-usage",
                     "data_dir": data_dir, "taskbar": True, "theme": {"dark": True, "accent": "#A29F9F"}},
                    {"type": "event", "id": "tab", "kind": "change", "value": {"index": 1, "label": "Accounts"}},
                    {"type": "event", "id": "add_kind", "kind": "change", "value": {"index": 6, "label": "Custom"}},
                    {"type": "event", "id": "add_name", "kind": "change", "value": "MyLLM"},
                    {"type": "event", "id": "custom_url", "kind": "change", "value": f"http://127.0.0.1:{port}/q"},
                    {"type": "event", "id": "custom_path", "kind": "change", "value": "quota.used_pct"},
                    {"type": "event", "id": "add_custom", "kind": "click"},
                ]
                for c in cmds:
                    proc.stdin.write(json.dumps(c) + "\n")
                proc.stdin.flush()

                import queue
                q = queue.Queue()

                def _pump():
                    for ln in proc.stdout:
                        q.put(ln)

                threading.Thread(target=_pump, daemon=True).start()

                seen_88_pct = False
                deadline = time.time() + 4.0
                captured = []
                while time.time() < deadline:
                    try:
                        line = q.get(timeout=max(0.05, deadline - time.time()))
                    except queue.Empty:
                        break
                    if not line.strip():
                        continue
                    msg = json.loads(line)
                    captured.append(msg)
                    if msg.get("type") == "bar":
                        if msg.get("view") is not None and "88%" in json.dumps(msg["view"]):
                            seen_88_pct = True
                            break
                        segs = msg.get("segments") or []
                        if segs and segs[0].get("text") == "88%":
                            seen_88_pct = True
                            break
                proc.stdin.write(json.dumps({"type": "shutdown"}) + "\n")
                proc.stdin.flush()
                proc.communicate(timeout=5)

                self.assertTrue(seen_88_pct, f"expected 88% used in bar, got: {captured[-5:]}")
                for m in captured:
                    self.assertFalse(m.get("type") == "log" and m.get("level") == "error", f"plugin logged error: {m}")
                    if m["type"] in ("tile", "page", "preview", "popup") and m.get("view") is not None:
                        validate_view(m["view"])
                saved = json.loads((Path(data_dir) / "accounts.json").read_text(encoding="utf-8"))
                self.assertEqual(len(saved), 1)
                self.assertEqual(saved[0]["name"], "MyLLM")
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
