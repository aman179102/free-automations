# 🚀 Free Automations Platform

> **100% Free, Autonomous Tech Growth, Hiring Radar & High-Signal Outreach Engine for [Aman Kumar](https://github.com/aman179102)** (`@aman179102`).

Built with zero paid API dependencies, multi-model AI fallbacks, atomic state persistence, and a 1-tap mobile Telegram Co-Pilot for ban-proof, lock-proof mobile execution.

---

## 🏗️ Architecture Overview

```
free-automations/
├── common/                     # Foundational core shared across automations
│   ├── __init__.py             # Public API exports
│   ├── config.py               # Settings loader, personal branding, API keys
│   ├── ai.py                   # Multi-provider waterfall (Gemini -> Groq -> OpenRouter)
│   ├── telegram.py             # 1-Tap Mobile Co-Pilot, HTML dispatch, mailto/HN intents
│   └── state.py                # Atomic JSON persistence & deduplication engine
├── automations/                # Platform automation domains
│   └── hn/                     # Official Hacker News YC Automation Suite
│       ├── api.py              # Firebase REST & Algolia HN client
│       ├── models.py           # HNStory, HNComment, HiringLead models
│       ├── hiring_radar.py     # Remote Golang/Python/FastAPI hiring radar & pitch generator
│       ├── discussion.py       # Ask HN architecture responder
│       ├── showcase.py         # Open-source Show HN project showcase generator
│       └── runner.py           # Orchestrator & Telegram dispatcher
├── tests/                      # Comprehensive unit and integration test suite
├── main.py                     # Unified CLI Router & daemon runner
├── server.py                   # Lightweight HTTP health & webhook server
├── requirements.txt            # Minimal Python dependencies
└── .env.example                # Sample environment configuration
```

---

## ⚡ Key Highlights & Capabilities

### 1. Resilient Multi-Provider AI Engine (`common/ai.py`)
- **Waterfall Fallback**: Queries **Google Gemini**, automatically falls back to **Groq**, then **OpenRouter**.
- **JSON & Text Modes**: Guaranteed valid JSON extraction via `generate_json()` and free-form content generation via `generate_text()`.
- **Automatic Retries**: Exponential backoff on HTTP 429 and 5xx errors.

### 2. 1-Tap Mobile Co-Pilot (`common/telegram.py`)
- **Zero Ban Risk**: Generates drafts and sends them directly to your phone via Telegram.
- **1-Tap Direct Intents**:
  - `mailto:` intent opens your native mobile mail client with pre-drafted pitch, founder address, and subject line.
  - Hacker News item links open the thread directly in browser or app.
  - X post web intent for social sharing.
- **Strict Format Safety**: Adheres strictly to Telegram's 4096-character limit for messages and 1024-character limit for photo captions.

### 3. Atomic State Persistence (`common/state.py`)
- **Safe Disk I/O**: Atomic file replacement via `.tmp` staging to prevent state corruption during sudden shutdowns.
- **Deduplication**: Built-in memory-bounded history and `is_seen` tracking.

### 4. Hacker News Domain Automation (`automations/hn/`)
- Powered 100% by official free APIs:
  - **Firebase REST API**: `https://hacker-news.firebaseio.com/v0/`
  - **Algolia HN Search API**: `https://hn.algolia.com/api/v1/`
- **100% Autonomous Auto-Pilot Mode**: Zero manual buttons or clicks needed. Posts comments and submits Show HN posts automatically when `--live` is enabled or `HN_AUTO_PUBLISH=true`.
- **Popular & Rising Thread Targeting**:
  - Comments on top popular stories (`fetch_topstories`) for broad visibility and karma.
  - Comments on fresh, rising, less popular threads (`fetch_newstories`) early to lead technical discussions.
- **Show HN Showcase Auto-Publisher**: Submits Show HN posts rotating across Aman's open-source projects (Distributed Storage, Real-Time Analytics Pipeline, PodVoice).
- **Stealth & Anti-Ban Cooldown Gate**: Enforces a 30-minute minimum delay between comments and 3 days between story submissions to prevent shadowbans or rate-limiting.
- **Hiring Radar**: Filters "Who is Hiring?" threads for strictly 100% remote Golang, Python, FastAPI, and MVP developer roles; extracts founder contacts and drafts personalized pitches citing Aman's GitHub.

### 5. Qiita Autonomous Tech Publishing Engine (`automations/qiita/`)
- Powered by official Qiita API v2:
  - **API Client**: `https://qiita.com/api/v2` with Bearer auth, exponential backoff, and automatic retries.
  - **Tech Japanese Article Generator**: 80% natural Japanese + 20% technical English ("Tech Japanese") with real working Python/FastAPI/async code blocks, clean ASCII architecture diagrams, 5 curated deep-dive templates, AI fallback, and strict 0.1% rule (zero spam links).
  - **Tech Trend & Tag Radar**: Scans Qiita articles under `#Python`, `#AI`, `#FastAPI`, `#Go`, `#Rust` with state deduplication (`seen_qiita_item_ids`).
  - **Creator Analytics Tracker**: Tracks LGTM (Likes), Stocks (Bookmarks), and Page Views with delta computation against prior state.
  - **Anti-Ban Safety Engine**: Human typing & review jitter delays (5.0s - 12.0s), max 1-2 daily posts cap, and strict content validation gates.
  - **Orchestrator Runner**: 24-hour publication cooldown enforcement, state tracking in `state/qiita_state.json`, and single clean consolidated Telegram digest (zero spam: suppressed during background runs when no new deltas or posts).

---

## 🚀 Quick Start Guide

### 1. Environment Setup

```bash
# Clone and enter directory
cd /home/devaman/Videos/free-automations

# Activate virtual environment
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

Key variables:
- `GEMINI_API_KEY`: Google AI Studio Gemini API Key
- `GROQ_API_KEY`: Groq Cloud API Key
- `OPENROUTER_API_KEY`: OpenRouter API Key
- `TELEGRAM_BOT_TOKEN`: Telegram bot token from @BotFather
- `TELEGRAM_CHAT_ID`: Telegram user/chat ID from @userinfobot

---

## 🖥️ CLI Usage (`main.py`)

Verify bot connectivity:
```bash
python main.py --test-telegram
```

Test AI providers (Gemini, Groq, OpenRouter):
```bash
python main.py --test-ai
```

### 100% Autonomous Auto-Pilot Mode (No Buttons Needed)

Run full cycle in live autonomous mode (publishes comments & Show HN posts):
```bash
python main.py --hn-all --live
```

Target popular top stories for commenting:
```bash
python main.py --hn-discussion --discussion-mode popular --live
```

Target rising / less popular fresh threads:
```bash
python main.py --hn-discussion --discussion-mode rising --live
```

Submit next project in Show HN rotation:
```bash
python main.py --hn-showcase --live
```

### Qiita Autonomous Engine Commands

Generate/publish Tech Japanese article to Qiita:
```bash
python main.py --qiita-article
```

Scan Qiita tech trends & tag radar (#Python, #AI, #FastAPI, #Go, #Rust):
```bash
python main.py --qiita-radar
```

Track Qiita LGTM, Stocks, and Views with delta computation:
```bash
python main.py --qiita-analytics
```

Run full suite of Qiita automations and send consolidated digest:
```bash
python main.py --qiita-all
```

Run 24/7 background autopilot daemon (every 30 mins):
```bash
python main.py --daemon --live --interval 30
```

---

## ☁️ Oracle VPS 24/7 Deployment

Deploying on your Oracle Cloud Always Free VPS takes 2 minutes:

1. **Clone repo on VPS:**
   ```bash
   git clone https://github.com/aman179102/free-automations.git
   cd free-automations
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configure `.env`:**
   ```bash
   cp .env.example .env
   # Add your HN_USERNAME, HN_PASSWORD, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
   ```

3. **Install systemd daemon service:**
   ```bash
   sudo cp free-automations.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now free-automations
   ```

4. **Check status & logs:**
   ```bash
   sudo systemctl status free-automations
   journalctl -u free-automations -f
   ```

---

## 🌐 Webhook & Health Server (`server.py`)

Run the lightweight HTTP server:
```bash
python server.py
# or
python main.py --server --port 10000
```

### Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Service health status, UTC timestamp, and configured services |
| `POST` | `/trigger?type=hiring` | Trigger remote hiring radar scan |
| `POST` | `/trigger?type=discussion` | Trigger Ask HN architecture discussion search |
| `POST` | `/trigger?type=showcase` | Trigger Show HN project showcase draft |
| `POST` | `/trigger?type=all` | Trigger full automation cycle |
| `POST` | `/webhook` | Telegram Bot incoming webhook handler |

---

## 🧪 Testing

Run all unit and integration tests:
```bash
python -m unittest discover tests
```
