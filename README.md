<div align="center">

# AI Usage for Fenn

**Never hit a surprise rate limit mid-flow.**  
Live **Claude**, **ChatGPT**, **Antigravity**, **Gemini**, **OpenRouter**, **GitHub Copilot**, and **Custom JSON** quotas on your Windows taskbar and floating notch.

[![Version](https://img.shields.io/badge/Version-1.0.0-7C5CFF?style=flat-square)](#versioning)
[![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/Dependencies-Zero%20(stdlib%20only)-2EA043?style=flat-square)](#quick-start)
[![Fenn](https://img.shields.io/badge/Fenn-Taskbar%20%2B%20Notch-7C5CFF?style=flat-square)](https://fenn.click)
[![Licence](https://img.shields.io/badge/Licence-MIT-lightgrey?style=flat-square)](LICENSE)

[Features](#why-install-ai-usage) · [Providers](#supported-providers--telemetry) · [Customisation](#granular-customisation) · [Quick start](#quick-start) · [Privacy](#privacy--security-guarantees) · [Architecture](#architecture--tests)

<br/>

![AI Usage overview showing Grab-style expanded notch, multi-counter hover preview, and taskbar pill](assets/showcase.svg)

</div>

> **Acknowledgements:** [Antigravity](https://antigravity.google) was used to assist with the development of this plugin and to generate the demonstration images in this README for public showcase.

---

## Why install AI Usage?

Most developers switch between Claude Code, Codex CLI, Antigravity (`agy`), Gemini CLI, Copilot, and API keys throughout the day, only noticing a 5-hour or weekly reset cap when a prompt fails mid-task. **AI Usage** brings every quota into one native glass pill beside your system clock, with zero background bloat and zero third-party `pip` dependencies.

> [!NOTE]
> **Testing note:** I have personally tested **Claude**, **ChatGPT (GPT)**, and **Antigravity** live on my own setup. **Gemini CLI**, **OpenRouter**, **GitHub Copilot**, and **Custom JSON** are implemented against their official APIs and covered by unit tests, so feedback or PRs for those providers are very welcome!
>
> **Availability:** I am at work all week, so I will reply to issues and PRs as soon as possible in the evenings or at weekends. Thanks for your patience!

<table>
<tr>
<td width="50%">

### Unambiguous Taskbar Bars
Every pill displays `% used` or `% left` right under the progress bar so you always know which direction a meter fills at a glance.

</td>
<td width="50%">

### Full Multi-Counter Hover Preview
Hover over the taskbar pill to see **all** active buckets side by side (`5h`, `7d`, `Gemini 5h`, `Gemini 7d`, `3rd-Party 5h`, `3rd-Party 7d`, and live countdowns).

</td>
</tr>
<tr>
<td width="50%">

### Grab-Style Frosted Dashboard
Both the expanded notch (`640x480`) and clicked taskbar popup (`540px`) share a translucent sidebar, full-width capsule CTA, and 3 colour-tinted live stat tiles.

</td>
<td width="50%">

### Granular Per-Counter Controls
Toggle individual quota buckets on cards and hover preview, plus pick independent **Taskbar** and **Notch** headline pins (`5h`, `Tightest`, or `Follow`).

</td>
</tr>
<tr>
<td width="50%">

### Persistent Cache & Safe Refresh
Calls official CLI refreshes (`claude auth status`, `agy models`, Google OAuth/ADC) without corrupting single-use tokens, and retains cached bars if a session expires.

</td>
<td width="50%">

### Clawd Pet & Custom Branding
Switch Claude between its terracotta logo and **Clawd pet**, pick **Brand**, **Fenn accent**, or **Traffic** colours, or drop custom icons straight onto the panel.

</td>
</tr>
</table>

<div align="center">

![Adaptive notch tile layouts across small arc dial, wide single/multi-account views, and large 2x2+ card](assets/tiles.svg)

</div>

---

## Supported providers & telemetry

| Provider | Live quotas & counters | Detection & linking |
| :--- | :--- | :--- |
| **Claude** | `5h` session, `7d` weekly, `Sonnet`, `Opus`, `Extra usage` (or monthly org USD spend) | **1-click Claude Code** (`~/.claude/.credentials.json`), auto `claude auth status` refresh, OAuth token, Admin key (`sk-ant-admin...`), or API key |
| **ChatGPT / OpenAI** | `5h` & `7d` Plus/Pro windows, `30d` free limits, `Review` quota, credits (or org USD spend) | **1-click Codex CLI** (`~/.codex/auth.json`), ChatGPT bearer JWT, or OpenAI key (`sk-...`) |
| **Antigravity** | `Gemini 5h`, `Gemini 7d`, `3rd-Party 5h`, `3rd-Party 7d`, session spend & context usage | **1-click OS keyring** (`gemini:antigravity`) + opt-in quote-free `agy statusLine` sync (`ai_usage_statusline.cmd`), auto `agy models` refresh, or GCP Project ID |
| **Gemini** | Per-model Cloud Code Assist quota bars & reset countdowns | **1-click Gemini CLI / gcloud ADC** (`~/.gemini/oauth_creds.json` or OS keyring), `GEMINI_API_KEY`, or AI Studio key (`AIza...`) |
| **OpenRouter** | Key spend cap progress, remaining credit balance, today / week / month spend | OpenRouter API key (`sk-or-v1-...`) |
| **GitHub Copilot** | `Premium`, `Chat` & `Completions` quotas + monthly reset timer | **1-click GitHub CLI** (`gh auth token`, `COPILOT_GITHUB_TOKEN`, `hosts.yml`) or GitHub token (`gho_...` / `ghp_...`) |
| **Custom JSON** | Any `% used`, `% left`, or `Used / Limit` metric with custom icon & colour | Any `https://` or `http://localhost` endpoint + dot-separated JSON path |

<details>
<summary><strong>Provider setup & troubleshooting notes</strong></summary>

1. **Claude Code (`5h` / `7d`)**: Sign in interactively inside `claude` (`/login`) so your OAuth token carries `user:profile` scope. Tokens generated by `claude setup-token` lack `user:profile` and return HTTP `403` on `/api/oauth/usage`. Admin keys (`sk-ant-admin...`) fetch monthly organisation spend instead.
2. **Antigravity (`agy`)**: Sign in via `agy` CLI or the Antigravity app, link Antigravity in **Accounts**, and click **Enable statusLine**. AI Usage configures a safe, quote-free `.cmd` wrapper (`~/.gemini/antigravity-cli/ai_usage_statusline.cmd`) that strips personal emails and transcript paths while forwarding official `remaining_fraction` buckets and reset countdowns on every turn. Earlier quoted hooks are repaired automatically on startup.
3. **ChatGPT & Codex CLI**: Run `codex login` once in terminal, then click **Link Codex CLI**. When pasting a raw ChatGPT JWT, `ChatGPT-Account-Id` and plan type are decoded automatically.
4. **Gemini CLI & Google ADC**: Supports `@google/gemini-cli` OAuth credentials, Windows keyring tokens, Google Cloud `application_default_credentials.json` refresh tokens, and `GEMINI_API_KEY` / AI Studio verification. Individual non-GCP Gemini quotas now live in **Antigravity**.
5. **Custom JSON APIs**: Use built-in presets for **DeepSeek USD** or **Local LLM %**, address nested fields with dot paths (e.g. `balance_infos.0.total_balance`), add optional `Bearer ...` or `Header: Value` headers, and click **Choose icon...** (or drag and drop any image) to attach a local PNG icon.

</details>

---

## Granular customisation

<div align="center">

![Granular customisation: visible counter toggles, separate Taskbar and Notch sections, and persistent quota cache](assets/customise.svg)

</div>

Every setting is accessible from **Settings** on both the clicked taskbar popup and the expanded notch dashboard:

- **Visible Counters**: Independent toggle switches for every counter discovered on an account (e.g. show `Gemini 5h` and `Gemini 7d` while hiding `3rd-Party 7d` from hover preview and cards).
- **Shared Display & Refresh**: Switch numbers between `Used %` and `Remaining %`, pick `Brand`, `Fenn accent`, or `Traffic` colour themes, toggle `Logo` vs `Clawd pet` for Claude, set warning thresholds, and pick `2m`, `5m`, or `15m` polling.
- **Taskbar**: Display `All` accounts (up to 5 + `+N` overflow), `Tightest`, or `Hidden`; toggle reset countdown timers beneath bars; pin a preferred window per provider on the taskbar.
- **Notch**: Control collapsed/expanded tile mode (`All` vs `Tightest`), independently follow or override each model's taskbar window (`Follow`, specific bucket, or `Tightest`), toggle card countdowns, warning pill badge, and auto-open on threshold cross.

---

## Quick start

Requires **Windows 10/11**, [**Fenn**](https://fenn.click), and **Python 3.8+** (`python` on `PATH`). Zero `pip install` needed.

### Install into Fenn

```powershell
git clone https://github.com/oscard-06/fenn-ai_usage.git "$env:APPDATA\Fenn\plugins\ai-usage"
```

Restart Fenn (or reload plugins in Fenn Settings), then open **Accounts** and click **Link** beside any auto-detected CLI.

### Live development workflow

Point Fenn directly to your local checkout via a directory junction and enable **Plugin dev mode** in Fenn so edits reload within a second:

```powershell
New-Item -ItemType Junction -Path "$env:APPDATA\Fenn\plugins\ai-usage" -Target "<path-to-this-repo>"
```

---

## Privacy & security guarantees

- **Local-only storage**: Settings, linked tokens, custom icons, and cached readings live exclusively in `%APPDATA%\Fenn\plugin-data\ai-usage\`.
- **Read-only CLI credential access**: Local CLI stores (`~/.claude/.credentials.json`, `~/.codex/auth.json`, `~/.gemini/oauth_creds.json`, Google ADC, and OS keyring `gemini:antigravity`) are never directly overwritten. Token refreshes call vendor CLI subcommands (`claude auth status`, `agy models`).
- **Sanitised telemetry**: `agy_statusline.py` discards emails, workspace paths, and transcript filenames before writing `antigravity_status.json`.
- **Minimal network surface**: HTTPS requests go **only** to the endpoints required for providers you explicitly link:
  - Claude: `api.anthropic.com`
  - ChatGPT / OpenAI: `chatgpt.com` or `api.openai.com`
  - Gemini: `cloudcode-pa.googleapis.com`, `oauth2.googleapis.com`, or `generativelanguage.googleapis.com`
  - GitHub Copilot: `api.github.com`
  - OpenRouter: `openrouter.ai`
  - Antigravity: local `statusLine` JSON by default (zero network calls), or `cloudcode-pa.googleapis.com` if an Enterprise GCP Project ID is provided
  - Custom: only the URL you configure

---

## Versioning

`1.0.0` marks the **initial public GitHub release**. Releases follow `MAJOR.MINOR.PATCH` Semantic Versioning:

| Digit | Release type | When it increments |
| :--- | :--- | :--- |
| **`MAJOR`** (`X.0.0`) | **Breaking release** | Incompatible changes to saved settings schemas, Fenn plugin manifest requirements, or provider configs needing manual re-linking. |
| **`MINOR`** (`1.X.0`) | **Feature release** | Backwards-compatible new AI providers, telemetry sources, UI customisation controls, or non-breaking improvements. |
| **`PATCH`** (`1.0.X`) | **Maintenance / fix release** | Backwards-compatible bug fixes, upstream parser adjustments, visual refinements, and documentation updates. |

---

## Architecture & tests

<div align="center">

[![oscard-06/fenn-ai_usage, explained in a one-minute video](https://gitdiagram.com/api/video/file?username=oscard-06&repo=fenn-ai_usage&format=poster)](https://gitdiagram.com/oscard-06/fenn-ai_usage/video)

</div>

> Interactive module map (click any node on GitHub to open its source file):

```mermaid
%% Generated by https://gitdiagram.com/oscard-06/fenn-ai_usage
flowchart TD

subgraph group_runtime["Plugin runtime"]
  node_controller["Plugin controller<br/>[main.py]"]
  node_fenn_api["Fenn plugin API<br/>[fenn.py]"]
end

subgraph group_accounts["Accounts and state"]
  node_state["Account and quota state<br/>[state.py]"]
end

subgraph group_provider["Quota providers"]
  node_models["Normalised quota models<br/>[models.py]"]
  node_fetchers["Provider fetchers<br/>[fetchers.py]"]
  node_parsers["Quota parsers<br/>[parsers.py]"]
  node_http["HTTP client<br/>[http_client.py]"]
end

subgraph group_surfaces["Display and settings"]
  node_plugin_views["Surface orchestrator<br/>[views.py]"]
  node_ui_canvas["Canvas layouts<br/>[ui_canvas.py]"]
  node_compact_views["Compact displays<br/>[compact_views.py]"]
  node_account_views["Account screens<br/>[account_views.py]"]
  node_settings_views["Settings screens<br/>[settings_views.py]"]
end

subgraph group_integrations["Local integrations"]
  node_cli_auth["CLI credentials and refresh<br/>[cli_auth.py]"]
  node_statusline["Antigravity statusLine<br/>[agy_statusline.py]"]
  node_icons["Provider and custom icons<br/>[icons.py]"]
end

node_developer(("Fenn user"))
node_fenn_host(("Fenn host"))

node_developer -->|"uses plugin"| node_fenn_host
node_fenn_host -->|"dispatches events"| node_fenn_api
node_fenn_api -->|"runs handlers"| node_controller
node_controller -->|"updates state"| node_state
node_controller -->|"requests render"| node_plugin_views
node_plugin_views -->|"builds surfaces"| node_fenn_api
node_plugin_views -->|"renders accounts"| node_account_views
node_plugin_views -->|"renders compact"| node_compact_views
node_plugin_views -->|"renders settings"| node_settings_views
node_plugin_views -->|"builds layouts"| node_ui_canvas
node_state -->|"refreshes accounts"| node_fetchers
node_fetchers -->|"reads credentials"| node_cli_auth
node_fetchers -->|"requests APIs"| node_http
node_fetchers -->|"parses responses"| node_parsers
node_parsers -->|"creates windows"| node_models
node_fetchers -->|"builds results"| node_models
node_state -->|"formats quotas"| node_models
node_account_views -->|"reuses components"| node_compact_views
node_settings_views -->|"reuses components"| node_compact_views
node_account_views -->|"formats account data"| node_models
node_settings_views -->|"reads settings"| node_state
node_compact_views -->|"resolves icons"| node_icons
node_state -->|"manages custom icons"| node_icons
node_state -->|"scans CLIs"| node_cli_auth
node_controller -.->|"enables sync"| node_statusline

click node_controller "https://github.com/oscard-06/fenn-ai_usage/blob/main/main.py"
click node_fenn_api "https://github.com/oscard-06/fenn-ai_usage/blob/main/fenn.py"
click node_plugin_views "https://github.com/oscard-06/fenn-ai_usage/blob/main/ai_usage/views.py"
click node_ui_canvas "https://github.com/oscard-06/fenn-ai_usage/blob/main/ai_usage/ui_canvas.py"
click node_compact_views "https://github.com/oscard-06/fenn-ai_usage/blob/main/ai_usage/compact_views.py"
click node_account_views "https://github.com/oscard-06/fenn-ai_usage/blob/main/ai_usage/account_views.py"
click node_settings_views "https://github.com/oscard-06/fenn-ai_usage/blob/main/ai_usage/settings_views.py"
click node_state "https://github.com/oscard-06/fenn-ai_usage/blob/main/ai_usage/state.py"
click node_models "https://github.com/oscard-06/fenn-ai_usage/blob/main/ai_usage/models.py"
click node_fetchers "https://github.com/oscard-06/fenn-ai_usage/blob/main/ai_usage/fetchers.py"
click node_parsers "https://github.com/oscard-06/fenn-ai_usage/blob/main/ai_usage/parsers.py"
click node_http "https://github.com/oscard-06/fenn-ai_usage/blob/main/ai_usage/http_client.py"
click node_cli_auth "https://github.com/oscard-06/fenn-ai_usage/blob/main/ai_usage/cli_auth.py"
click node_statusline "https://github.com/oscard-06/fenn-ai_usage/blob/main/ai_usage/agy_statusline.py"
click node_icons "https://github.com/oscard-06/fenn-ai_usage/blob/main/ai_usage/icons.py"

classDef toneNeutral fill:#f8fafc,stroke:#334155,stroke-width:1.5px,color:#0f172a
classDef toneBlue fill:#dbeafe,stroke:#2563eb,stroke-width:1.5px,color:#172554
classDef toneAmber fill:#fef3c7,stroke:#d97706,stroke-width:1.5px,color:#78350f
classDef toneMint fill:#dcfce7,stroke:#16a34a,stroke-width:1.5px,color:#14532d
classDef toneRose fill:#ffe4e6,stroke:#e11d48,stroke-width:1.5px,color:#881337
classDef toneIndigo fill:#e0e7ff,stroke:#4f46e5,stroke-width:1.5px,color:#312e81
classDef toneTeal fill:#ccfbf1,stroke:#0f766e,stroke-width:1.5px,color:#134e4a
class node_controller,node_fenn_api,node_developer toneBlue
class node_state toneAmber
class node_models,node_fetchers,node_parsers,node_http toneMint
class node_plugin_views,node_ui_canvas,node_compact_views,node_account_views,node_settings_views toneRose
class node_cli_auth,node_statusline,node_icons,node_fenn_host toneIndigo
```

<details>
<summary><strong>Project file breakdown & test command</strong></summary>

```text
ai-usage/
├── plugin.toml              # Fenn plugin manifest & dimensions (640x480)
├── taskbar.toml             # Taskbar popup (540px) & hover preview (290px) widths
├── fenn.py                  # Official zero-dependency Fenn Python SDK
├── main.py                  # Entry point, window selection & Fenn JSON event loop
├── assets/                  # Bundled provider PNGs & vector UI showcases
├── ai_usage/
│   ├── models.py            # Normalised quota structures, presets & formatting
│   ├── http_client.py       # Stdlib HTTPS JSON client with secret redaction
│   ├── cli_auth.py          # Read-only CLI discovery, safe refresh & statusLine setup
│   ├── agy_statusline.py    # Sanitised Antigravity CLI statusLine collector
│   ├── parsers.py           # Pure stateless quota payload parsers per provider
│   ├── fetchers.py          # Live API / CLI usage fetchers & dispatch
│   ├── icons.py             # Brand PNG resolution & custom icon normalisation
│   ├── ui_canvas.py         # Frosted sidebar, capsule CTA & colour-tinted tile canvases
│   ├── compact_views.py     # Taskbar pill, hover preview, notch tiles & right-click menu
│   ├── account_views.py     # Usage cards, CLI auto-detect rows & connect forms
│   ├── settings_views.py    # Visible counters, display, taskbar & notch controls
│   ├── views.py             # Top-level Grab-style page & popup orchestrator
│   └── state.py             # Settings, persistent cache & background polling
├── tests/
│   └── test_plugin.py       # Unit, parser, persistence & live protocol test suite
└── LICENSE
```

```powershell
python -m unittest discover -s tests -v
```

</details>

<div align="center">

Licensed under the [MIT Licence](LICENSE). Built with pure Python stdlib for [Fenn](https://fenn.click).

</div>
