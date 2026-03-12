# Survival Skills AI Chatbot (Modular)

Offline-first survival knowledge assistant with local storage, full-article retrieval, and autonomous background updates.

## New Additions (Current State)

- **Modular architecture:**
  - `offline-survival-ai.py` — bootstrap + lifecycle wiring
  - `config.py` — paths, categories, app constants, configurable timeouts
  - `database.py` — Peewee ORM, FTS search, dedup/versioning, schema migration guards
  - `plugins.py` — built-in plugins + custom sources support (generic URLs, local files/folders/ZIP, OpenLibrary) + drop-in external plugin loader
  - `updater.py` — seeding, plugin ingestion, import/export, cache writes
  - `scraper.py` — autonomous background scrape loop with internet checks, watchdog timeout (with improved logging), and rate-limiting on late-completion messages
  - `cli.py` — interactive menu, search, chat with synthesis, full-article pagination, favorites, custom source management, import/export
  - `utils.py` — sanitization, tokenization, JSON helpers, pagination helpers

- **Search & Content:**
  - Tokenization + synonym expansion + SQLite FTS5 (BM25) with SQL `LIKE` fallback.
  - Content deduplication/versioning via `content_hash`, `revision`, `KnowledgeVersion` history.
  - Auto-migration of existing databases for new columns (`source`, `content_hash`, `revision`, `last_updated`).

- **Chat Mode (Synthesis & Article Reading):**
  - Generates direct extractive answers from top-matched local documents.
  - Supports configurable per-session answer formats: `field-manual` (step-by-step + field notes) or `compact` (summary).
  - Still returns full paginated source articles for context and deep reading.
  - Type `style` in chat to toggle answer format mid-session.

- **Custom Sources:**
  - Ingest from URLs (generic `http`/`https`), local file paths, folders (recursive), and ZIP archives.
  - Supported document types: `.txt`, `.md`, `.html`, `.htm`, `.pdf`, `.doc`, `.docx`.
  - Each extracted document becomes its own searchable and readable topic (appears in pagination).
  - OpenLibrary integration via `provider: openlibrary` with subject-based fetching and automatic category inference.
  - Manage all custom sources from CLI menu option `12` (add/list/remove/toggle/edit).

- **Background Scraper Improvements:**
  - User-triggered manual updates pause background scraping to prevent watchdog timeouts.
  - Watchdog timeout logs now include configured timeout seconds and background-completion status.
  - Late-completion log messages are rate-limited (configurable `LATE_COMPLETION_LOG_COOLDOWN_SECONDS`).

## Linux CLI Install (Step-by-Step with `venv`)

### 1) Prerequisites

On Debian/Ubuntu:

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
```

Verify:

```bash
python3 --version
```

### 2) Open project folder

```bash
cd /path/to/offline-survival-AI
```

### 3) Create a virtual environment

```bash
python3 -m venv .venv
```

### 4) Activate the virtual environment

```bash
source .venv/bin/activate
```

After activation, your prompt should show `(.venv)`.

### 5) Upgrade packaging tools (recommended)

```bash
python -m pip install --upgrade pip setuptools wheel
```

### 6) Install project dependencies

```bash
pip install -r requirements.txt
```

### 7) Run the chatbot

```bash
python offline-survival-ai.py
```

First run behavior:

- Creates app data under `~/.survival_chatbot/`
- Creates `knowledge.db` and cache/media folders
- Seeds built-in content
- Runs initial plugin-driven update
- Starts background scraper loop

### 8) Exit and deactivate `venv`

When done:

```bash
deactivate
```

### 9) Next time you use the project

```bash
cd /path/to/offline-survival-AI
source .venv/bin/activate
python offline-survival-ai.py
```

## CLI Menu & Usage

**Main Menu Options:**

- `[1]` Search knowledge — ranked full-text search, then paginate through results
- `[2]` Browse categories — select category, view all entries
- `[3]` Chat — ask a question, receive synthesized answer + source articles (type `style` to toggle format)
- `[4]` View summary — count of entries per category
- `[5]` Update knowledge — run manual update from configured sources
- `[6]` Deep dive — extended scrape from plugins
- `[7]` Delete cache — wipe database and cache (restart rebuilds)
- `[8]` Recent searches — view search history
- `[9]` Favorites — view saved articles (toggle with `f#` in results)
- `[10]` Export knowledge — save all entries to JSON
- `[11]` Import knowledge — load entries from JSON file
- `[12]` Manage custom sources — add/list/remove/toggle/edit sources (saved to `~/.survival_chatbot/custom_sources.json`)

**Navigation Keys (in paginated views):**

- `n` — next page
- `p` — previous page
- `f#` — toggle favorite (e.g., `f2` for item 2)
- `#` — open item (by number)
- `b` — back to results
- `menu` — return to main menu
- `exit` — quit app

**Chat-Specific Commands:**

- Type `style` to toggle between `field-manual` and `compact` answer formats (per session).
- Type `menu` to return to main menu.
- Type `exit` to quit.

## Plugins

Built-in plugins:

- Project Gutenberg (`project_gutenberg`)
- Wikipedia API (`wikipedia_api`)
- Offline media indexer (`offline_media_index`)
- Custom sources (`custom_sources`)

Custom sources format:

- File path: `~/.survival_chatbot/custom_sources.json`
- Supported JSON shape: list of entries (or `{ "sources": [...] }`)
- Entry fields:
  - `name` (string)
  - `url` (string, required; supports `https://...`, local file path, local folder path, or `.zip` archive path)
  - `provider` (string, optional; `generic` or `openlibrary`)
  - `categories` (list of category keys, optional; blank means all)
  - `queries` (list of substring filters, optional; blank means all)
  - `subjects` (list, optional; used by `openlibrary` provider)
  - `enabled` (bool, optional, default true)

Local-file ingestion notes:

- Supported source files: `.txt`, `.md`, `.html`, `.htm`, `.pdf`, `.doc`, `.docx`
- Supported archive source: `.zip` (scans supported files inside archive)
- Folder sources are scanned recursively for the same supported file types.
- Each extracted file is saved as its own knowledge topic, so it opens in the existing paginated article reader.

Example:

```json
[
  {
    "name": "Field Manual",
    "url": "https://example.org/survival-notes",
    "categories": ["survival_techniques"],
    "queries": ["shelter", "water"],
    "enabled": true
  }
]
```

OpenLibrary example:

```json
[
  {
    "name": "OpenLibrary Survival",
    "provider": "openlibrary",
    "url": "https://openlibrary.org",
    "subjects": ["fishing", "bushcraft", "wilderness survival"],
    "categories": ["fishing", "survival_techniques"],
    "queries": ["improvised", "setup"],
    "enabled": true
  }
]
```

**OpenLibrary Provider Behavior:**

- Searches OpenLibrary API by configured `subjects` (e.g., "fishing", "bushcraft").
- Stores each matching work (book) as an individual topic with metadata (author, year, subjects, summary, OpenLibrary link).
- Infers best-fit category from work subjects or configured categories (with keyword mapping for survival domains).
- Full topic content is searchable and readable in the paginated article viewer.
- Works are fetched on demand during updates or deep-dive scrapes.

**Chat Synthesis Behavior:**

- Searches downloaded local knowledge on user query.
- Extracts relevant sentences from top-ranked documents and synthesizes a direct answer.
- Formats response based on configured style:
  - **`field-manual`** (default): Numbered steps + field notes + source cues.
  - **`compact`**: Direct summary (first 2-3 ranked sentences).
- Afterward shows matching source articles for full-context reading.
- Type `style` in chat mode to toggle format mid-session.

External plugins:

- Place `.py` files in `~/.survival_chatbot/plugins/`
- Expose class `Plugin` with:
  - `name: str`
  - `fetch(query: str, category: str) -> list[dict[str, str]]`

Expected returned keys per record:

- `category`
- `title`
- `content`
- `source`

## Import / Export

- Export from CLI option `10` to a JSON file.
- Import from CLI option `11` from a JSON file.
- Invalid categories in imported JSON are skipped for safety.

## Run Tests

```bash
pytest -q
```

## Troubleshooting

- `sqlite3.OperationalError` mentioning `fts5`:
  Your Python SQLite build may not include FTS5.

  Check SQLite version:

  ```bash
  python -c "import sqlite3; print(sqlite3.sqlite_version)"
  ```

- `source .venv/bin/activate` fails:
  Ensure you created the environment in the project root with `python3 -m venv .venv`.

- External plugins are not loading:
  Confirm plugin files are in `~/.survival_chatbot/plugins/` and define `Plugin` with `name` and `fetch(...)`.

- Background scraping appears idle:
  Scraper pauses while user input is active and respects the configured background interval. Manual updates (menu option 5) will pause background scraping to avoid watchdog timeouts.

- "Background update watchdog timeout reached" message:
  A background update took longer than `WATCHDOG_TIMEOUT_SECONDS` (default 90s). The update continues in the background; you'll see a follow-up message when it finishes (rate-limited). To adjust timeout, edit `config.py` or use environment variable overrides.

## Dependency Notes

- `peewee` >= 3.17.0 — ORM and schema management
- `prompt_toolkit` >= 3.0.0 — optional enhanced input; app falls back to standard `input()` if unavailable
- `pypdf` >= 5.0.0 — PDF text extraction for custom sources
- `pytest` >= 8.0.0, `pytest-mock` >= 3.0.0 — test dependencies
