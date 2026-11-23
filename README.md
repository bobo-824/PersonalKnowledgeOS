# Personal Knowledge OS

A personal knowledge-management and long-term-learning system. **Local-first** — all of your data stays on your own machine.

The core idea in one line: **write down your own understanding → let AI test and correct it → consolidate it through spaced review → then connect the knowledge into a network.**

This is not an ordinary note-taking app. Its central asset is not how many articles you have saved, but **the understanding you have formed**, and how those understandings change, get corrected, and link to one another over time.

> Note: the application UI is currently in Chinese. Button and menu names in this guide are English descriptions of what they do.

---

## What works right now (important)

The project is built in stages; **it is currently complete through Stage 5: Backup & Export** (wrapped up 2026-11-04).

| Status | Features |
|---|---|
| ✅ Available | Record knowledge (title, domain, your own understanding, reasoning behind it, open questions, learning sources), view, edit, archive & restore, full-text search, domain autocomplete, duplicate-entry hints, and an **append-only version history** of your understanding |
| ✅ Available (infrastructure) | Local database & migrations, configuration management, environment self-check, one-command install, one-command start, responsive app shell, real homepage health check |
| ✅ Available (Stage 2) | AI Study Room (streaming chat), extract a knowledge draft from the conversation and confirm it into knowledge, knowledge review with per-item disposition, adopting a suggestion creates a new version (**AI never overwrites your original text**), settings page for model config (writes `.env`, takes effect immediately, "Test connection"), clear conversation history |
| ✅ Available (Stage 3) | **Spaced review**: every knowledge item automatically enters a review cycle (individual items can be paused); **the first reviews land on day 1, day 3, and day 7 after you save it, then FSRS stretches the interval based on your mastery**. Due list → self-test (recall, then rate on a 4-level scale) or AI-chat review (AI asks → probes → suggests a rating → **you confirm the rating yourself**); the detail page shows the schedule and review history. **Review works even without AI configured** |
| ✅ Available (Stage 4) | **Knowledge graph**: related candidates are recalled for free → one click lets AI judge each pair (the number of requests is shown before you click) → you confirm / reject / change the type / ignore for now → confirmed relations appear on the graph (React Flow), and pairs you rejected never nag you again. The graph page has a growth-stats card; the detail page adds a "Related knowledge" section. Without AI, recall and the graph still work — there just are no candidates to review |
| ✅ Available (Stage 5) | **Backup & export**: an automatic backup on every launch (failure only warns, never blocks you from entering the app) plus manual backup from the settings page, keeping the 7 most recent; one-click download from the settings page as a **single Markdown file** (grouped by domain, readable) or **full JSON** (table-by-table fidelity, easy to import into other tools later). Restore instructions are written into the settings page |

**Configure before using AI**: open the **Settings** page and fill in the API base URL, key, and model name (any OpenAI-compatible endpoint works — e.g. DeepSeek, a local Ollama), then click **Test connection** to confirm it works.
**The base URL usually needs to end at `/v1`** (e.g. `https://api.deepseek.com/v1`). Entering only the domain hits the provider's website instead of the API — which shows up as "connected, but the AI says nothing".
Not configuring it does not affect manual recording or search at all. The key is stored only in the local `.env` file and never enters Git.

**How the AI Study Room conducts a conversation**: it acts as a **Socratic teacher** — good at prompting your own thinking with questions rather than handing you conclusions. It won't lecture you in one go; it first asks how you currently understand something, finds the gaps in what you have already said, presses on your reasoning, and breaks the question down smaller when you get stuck.
**The last line of every reply carries a mastery score** (`掌握度：N/10` — "Mastery: N/10" — plus a one-sentence reason).
**Once the score passes 8 it stops probing** and hands the choice back to you: wrap up and click "Turn this conversation into knowledge", or keep digging. The conversation ends when you understand it, not when you're worn out.

**Copying the AI's replies**: every reply has a **Copy** button beneath it — one click puts the whole message on the clipboard, then Ctrl+V elsewhere. The button exists because the desktop icon opens a browser app window, and that window's right-click menu has no "Copy". To copy only part of a reply, just select the text and use Ctrl+C as usual.

**The guarantee this version makes**: the "your own understanding" text you write is **never overwritten**. Every change to the understanding body appends a new version; old versions are kept verbatim and you can flip back at any time to see how you originally thought about it. Changing only the title, domain, summary, or sources is housekeeping and does not create a new version.

Every page in the UI is real functionality — there are no placeholder pages.
The full roadmap and per-item acceptance results are in [docs/ROADMAP.md](docs/ROADMAP.md).

---

## Requirements

| Item | Requirement | Notes |
|---|---|---|
| OS | Windows 10/11 | Linux / macOS also supported |
| Python | 3.13 or newer | Developed and verified on 3.14 |
| uv | Any recent version | Python dependency manager; creates the virtual environment |
| Node.js | 22 or newer | Needed for the frontend build |
| Disk space | 1 GB+ | Dependencies + database |

Not sure what's installed? Run the self-check — it tells you, item by item, what's missing and how to fix it:

```powershell
python scripts/doctor.py
```

---

## Installation

Open PowerShell in the project root (`D:\PersonalKnowledgeOS`) and run one command:

```powershell
python scripts\setup.py
```

It runs, in order: environment self-check → install backend dependencies → generate `.env` → create and migrate the database → install frontend dependencies → build the frontend.

**This script is safe to re-run**; steps already completed are skipped automatically. If any step fails, it stops and tells you what to do.

> If it says script execution is disabled, use this command to bypass it (for this run only, without changing system settings):
> ```powershell
> powershell -ExecutionPolicy Bypass -File scripts\setup.py
> ```
> In practice `setup.py` is a Python script and is usually not subject to the execution policy; the one that is, is `start.ps1` below.

---

## Starting the app

### Desktop icon (the easiest)

After installing, run this once:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-shortcut.ps1
```

A "Personal Knowledge OS" icon appears on the desktop. **Double-click to launch**: it starts the service in the background (no console window at all), then opens a **standalone app window** — no address bar, no tabs, it looks like its own piece of software. **Close that window and the background service stops automatically**, leaving no process behind.

| Situation | Result |
|---|---|
| Double-click (service not running) | Migrates the database to the latest version first, then starts the service; the app window appears a few seconds later |
| Double-click while the service is already running | Does not start a second service; just brings up the window |
| Close the app window | The background service stops automatically |
| You started the service yourself with `.\start.ps1` | Double-click only opens the window; closing the window does **not** stop that service — to stop it, go back to the original terminal and press Ctrl+C |
| The service fails to start (port taken, bad `.env`, etc.) | A dialog explains the cause and the fix; details are written to `logs\server.log` |
| Neither Chrome nor Edge is installed | Falls back to a normal tab in your default browser, and warns that this mode cannot auto-stop the service |

Details and troubleshooting:

- The app window uses a **separate browser profile** (`%LOCALAPPDATA%\pkos-desktop-app\browser-profile`), so it never touches your everyday bookmarks, extensions, or sign-in state. Deleting it just resets this window's browser settings (like window size); your knowledge data is not there — it lives in the project's `data\` directory.
- Run records are in `logs\launcher.log` (each click, when the service started, when the window closed) and `logs\server.log` (the service's own output). Both files are auto-trimmed when they grow large, so they never pile up forever.
- If the service ever becomes an "orphan" (e.g. the launcher was force-killed, so the window is gone but the service keeps running), run this one line in PowerShell to stop it:

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like '*uvicorn*backend.main:app*' } | ForEach-Object { Stop-Process -Id $_.ProcessId }
```

- To change the port, edit `PKOS_PORT` in `.env` — the launcher and the service read the same file.
- This section covers Windows only. On macOS / Linux keep using `./start.sh` below.

### PowerShell (recommended)

```powershell
.\start.ps1
```

Your browser opens automatically at <http://127.0.0.1:8000>. **One port serves the whole app** — the pages and the API come from the same process; you don't need a separate frontend server.

If it says "running scripts is disabled", run:

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

Or lift the restriction for the current user once (a one-time action):

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### Git Bash / Linux / macOS

```bash
./start.sh
```

### Development mode (auto-reload on code changes)

```powershell
.\start.ps1 -Dev        # PowerShell
./start.sh --dev        # Git Bash
```

Development mode also starts Vite (port 5173); open <http://127.0.0.1:5173> in the browser. Frontend changes hot-reload; backend changes auto-restart.

### Other flags

| Flag | Effect |
|---|---|
| `-NoBrowser` / `--no-browser` | Start without opening the browser |
| `-SkipDoctor` / `--skip-doctor` | Skip the pre-start environment self-check (faster, but errors are harder to read) |
| `--help` | Show help (`start.sh` only) |

To stop the service: press `Ctrl+C` in the window. Child processes are cleaned up too, so no orphan process is left holding the port.

---

## First use

Your browser opens `http://127.0.0.1:8000` automatically. Then:

1. Click **Knowledge base** on the left, then **Add entry** in the top-right.
2. **Title** and **Your understanding** are required; everything else can be left blank.
   "Your understanding" is the most important field — don't copy the source text, say it in your own words.
   Whatever you can't articulate is exactly what you haven't learned yet.
3. As you type in **Domain**, it suggests spellings you've already used.
   Try to use the same name for the same domain, otherwise they won't group together later.
4. **Learning sources** can hold multiple entries. If you pick the "Other" type, you must state clearly what the source actually is.
5. Click **Save**.

To edit later, open the detail page and click **Edit**. While editing, the top of the form **tells you in advance whether this save will create a new version**:

- Changing "Your understanding / Reasoning / Open questions" → creates a new version; the old text is kept verbatim.
- Changing only title, domain, summary, or sources → no new version (housekeeping).

If you don't want to see an item anymore, use **Archive**, not delete.
After archiving it disappears from the default list and from search, but the data is fully retained — click **Restore** anytime to bring it back.

Search supports Chinese. **Two-character terms are searchable too** — 3+ characters go through the full-text index, 1–2 characters through keyword matching, and the page tells you which one it used.

---

## Project layout

```
PersonalKnowledgeOS/
├── start.ps1              Windows PowerShell start script
├── start.sh               Git Bash / Linux / macOS start script
├── pyproject.toml         Python dependencies and version pinning
├── uv.lock                Exact dependency versions (so another machine installs identically)
├── alembic.ini            Database migration config
├── .env.example           Configuration template (copy to .env, then edit)
│
├── backend/               Backend (FastAPI), five layers; dependencies flow top-down only
│   ├── config.py          The single source of all configuration: ports, data dir, AI key
│   ├── main.py            App entry point; serves both the API and the frontend
│   ├── api/               Interface layer: health.py, knowledge.py, ai.py (chat/extract/review),
│   │                      settings.py (read/write AI config + "Test connection"), deps.py
│   ├── schemas/           Pydantic validation & serialization: knowledge.py, ai.py (the AI-output boundary),
│   │                      ai_api.py (AI request/response models)
│   ├── services/          Application services: knowledge_service.py, search_service.py,
│   │                      ai_service.py (orchestration of the three AI workflows; transaction boundaries live here),
│   │                      settings_service.py (writes AI config into .env),
│   │                      ai/ (Provider protocol, httpx implementation, fake implementation, prompts, factory)
│   ├── domain/            Pure business rules: versioning.py (does not import the database or the web framework)
│   └── db/                Database: ORM models, sessions, column types
│
├── frontend/              Frontend (React + TypeScript + Vite)
│   └── src/
│       ├── app/           Routing and navigation config
│       ├── components/    ui/ (primitives), knowledge/ (business components), feedback/, layout/
│       ├── pages/         9 page components across 10 routes
│       └── lib/           api.ts, schemas.ts, knowledge-queries.ts, format.ts
│
├── migrations/            Database migration scripts (versioned — don't hand-edit a committed migration)
├── scripts/
│   ├── doctor.py          Environment self-check
│   └── setup.py           One-command install
├── tests/                 pytest tests
├── docs/                  Project documentation
└── data/                  Your data (database, backups) — git-ignored, never committed
```

---

## Common commands

Run these from the project root:

| Purpose | Command |
|---|---|
| Environment self-check | `python scripts/doctor.py` |
| Run tests | `uv run pytest` |
| Run tests (with verbose detail) | `uv run pytest -v` |
| Migrate the database to latest | `uv run alembic upgrade head` |
| Show current migration version | `uv run alembic current` |
| Check models against migrations | `uv run alembic check` |
| Generate a new migration | `uv run alembic revision --autogenerate -m "description"` |
| Frontend type check | `cd frontend && npx tsc -b` |
| Frontend build | `cd frontend && npm run build` |
| Frontend dev server | `cd frontend && npm run dev` |

---

## Configuration

All configuration is read from environment variables or the `.env` file, with the `PKOS_` prefix throughout. **There are no hard-coded ports or paths in the code.**

Copy the template to create your own config:

```powershell
Copy-Item .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `PKOS_HOST` | `127.0.0.1` | Listen address. Keep the default for local-only access |
| `PKOS_PORT` | `8000` | Backend port (also the page port in single-port mode) |
| `PKOS_FRONTEND_PORT` | `5173` | Vite port, used only in development mode |
| `PKOS_DATA_DIR` | `data/` inside the project | Where the database and backups live. Leave blank to use the default |
| `PKOS_AI_PROVIDER` | empty | Only needed for Stage 2 |
| `PKOS_AI_BASE_URL` | empty | Only needed for Stage 2 |
| `PKOS_AI_API_KEY` | empty | **The key. Keep it here only — never write it into frontend code** |
| `PKOS_AI_MODEL` | empty | Only needed for Stage 2 |
| `PKOS_AI_TIMEOUT_SECONDS` | `60` | AI request timeout |
| `PKOS_LOG_LEVEL` | `INFO` | Log level |
| `PKOS_ALLOW_MISSING_FRONTEND` | `true` | Whether to allow API-only startup when the frontend isn't built |

`.env` is ignored by `.gitignore` and is never committed.

---

## Data & backups

- Database file: `data/app.db` (single SQLite file)
- Backup directory: `data/backups/`
- The database uses WAL mode, so you'll also see `app.db-wal` and `app.db-shm` in the same directory

**Do not copy `app.db` directly to back it up.** In WAL mode the newest data may not have been written into the main file yet, so a direct copy yields an incomplete database. There are two correct approaches:

- **Let the app back it up**: every launch writes an automatic backup into `data/backups/` (keeping the 7 most recent), and you can click "Back up now" on the Settings page. Backups use `VACUUM INTO`, which always produces a complete snapshot.
- **Manual backup** (when the app won't start): run `VACUUM INTO 'path/to/backup.db'` via the SQLite command line.

**To restore**: quit the app → copy a backup from `data/backups/` over `data/app.db` (and delete the old `-wal`/`-shm` files) → restart. There is deliberately **no** one-click "Restore" button in the UI — restoring overwrites existing data, a dangerous operation worth one manual confirmation.

The entire `data/` directory is git-ignored, so your knowledge data is never committed to version control.

---

## Privacy & security

- **Data stays on your machine by default.** Knowledge, versions, review records, and conversations all live in SQLite on your own computer.
- **Data is sent externally only when you use an AI feature**, following least-privilege — it never uploads your whole knowledge base by default.
- **The API key is read only from backend environment variables** and never appears in frontend code, API responses, logs, or version control. The homepage health-check endpoint returns only "configured or not", never the key's contents.
- The service listens on `127.0.0.1` by default and is not exposed to the LAN or the internet.

---

## Troubleshooting

### 1. Port already in use

The self-check tells you exactly who holds it:

```
[XX]  Backend port 8000   a service is already listening (PID 53328 (python.exe))
       ↳ what to do: kill the process, or set PKOS_PORT in .env to use another port
```

To kill the process holding it:

```powershell
taskkill /PID 53328 /F
```

To change the port: edit `PKOS_PORT` in `.env`; the frontend follows automatically, no code change needed.

### 2. Port is free but binding still fails

On Windows, Hyper-V / WSL **reserve** large ranges of ports; binding a port inside a reserved range fails with "access denied". The self-check detects this:

```
[XX]  Backend port 8000   falls inside the Windows reserved port range 50000-50059; binding will be denied by the OS
```

To see which ports the system reserved:

```powershell
netsh interface ipv4 show excludedportrange protocol=tcp
```

The fix is to pick a port outside the reserved ranges.

### 3. Proxy software intercepting localhost (Clash / Verge, etc.)

**Symptom**: the service is clearly running, but the browser can't open <http://127.0.0.1:8000>, or reports a connection reset.

**Cause**: the proxy is routing the loopback address through the proxy too.

**Fixes** (any one):
- Add `127.0.0.1` and `localhost` to the proxy's bypass list (direct-connect rules)
- Temporarily turn off the system proxy
- Turn off the proxy's "TUN mode / enhanced mode"

A quick command-line check for whether the proxy is the culprit:

```powershell
curl.exe --noproxy "*" http://127.0.0.1:8000/api/health
```

If it works once you add `--noproxy "*"`, the proxy is intercepting.

### 4. Chinese output shows as mojibake

The start scripts already set `PYTHONUTF8=1`, so this shouldn't happen normally. If you run a Python script **directly** and see mojibake, set this first:

```powershell
$env:PYTHONUTF8 = "1"
chcp 65001
```

### 5. PowerShell says scripts are disabled

See the `Set-ExecutionPolicy` note in the "Starting the app" section above.

### 6. `npm ci` is slow or fails

Configure a domestic mirror (helps in mainland China):

```powershell
npm config set registry https://registry.npmmirror.com
```

### 7. Database corruption or migration failure

Back up the current files first, then decide:

```powershell
Copy-Item data\app.db data\app.db.broken-backup
uv run alembic current          # see which version you're on
uv run alembic upgrade head     # try to continue migrating
```

If the database is newly created and holds no data, the simplest fix is to delete the three `data/app.db*` files and re-run `python scripts\setup.py`. **Do not do this when there is real data in it.**

### 8. Blank frontend page

- Confirm the build output exists: `frontend/dist/index.html`
- Confirm you're hitting the backend port (8000 in single-port mode), not the Vite port
- Open the browser DevTools (F12) and check the Console for errors
- Visit <http://127.0.0.1:8000/api/health> to see the backend self-check result

### 9. The AI reply is empty (especially with a relay/proxy endpoint)

**First check whether the "API base URL" on the Settings page ends with `/v1`.** This is the most common cause:

| What you entered | Where the request actually goes | Result |
|---|---|---|
| `https://your-relay.com` | `https://your-relay.com/chat/completions` | Hits the **website**, not the API |
| `https://your-relay.com/v1` | `https://your-relay.com/v1/chat/completions` | Correct |

The website also returns HTTP 200, so it looks "connected", but contains no text — which shows up as "Test connection succeeds, but the chat returns nothing". Both cases now report a clear error:

- When chatting: **"This API address did not return conversation data… please append /v1 to the end"**
- "Test connection" only reports success once it actually receives text; a 200 with no text reports
  **"The model connection is fine but returned no text — try a different model or check the relay's configuration"**

After fixing the address, click "Test connection" to verify — no need to restart the service.
Other possibilities: a wrong model name (reports HTTP 400/404), the relay's daily quota exhausted, or a reasoning model that outputs only its thinking and no answer body (try a regular chat model).

---

## Development conventions

- **Dependency versions are pinned**; upgrading requires an explicit reason recorded in [docs/DECISIONS.md](docs/DECISIONS.md).
- **After each stage**: run tests, type-check, build, update the README and the development log, then start the next stage.
- **Never hand-edit a committed migration file** — if you need a change, create a new migration.
- `start.ps1` must be **UTF-8 with BOM**, and `start.sh` must use **LF line endings** — `.gitattributes` already enforces this; don't bypass it.

More details (these docs are currently in Chinese):

| Document | Contents |
|---|---|
| [docs/PRD.md](docs/PRD.md) | Product requirements and scope boundaries |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Architecture layers, module boundaries, data flow |
| [docs/DATA_MODEL.md](docs/DATA_MODEL.md) | Tables, fields, relations, and migration notes |
| [docs/AI_WORKFLOWS.md](docs/AI_WORKFLOWS.md) | AI prompts, input/output schemas, safety boundaries |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Stages, tasks, acceptance criteria, known issues |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Key technical decisions and their rationale |
