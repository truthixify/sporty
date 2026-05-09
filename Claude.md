# CLAUDE.md

## Project

A data platform around the Virtustec virtual sports feed served via SportyBet's `/virtual` page. The project will grow into multiple components (collection, storage, analysis, etc.) but for now only the data collection component is in scope.

This is a **data project**. Not betting, not prediction, not trading. Anything that places a wager, simulates a wager, or recommends one is out of scope.

## Current focus

Build the **`scraper/`** component. Its full spec lives at [`docs/scraper/spec.md`](./docs/scraper/spec.md). Read that first before writing any code in `scraper/`. It covers architecture, database schema, season detection, runbooks, and the suggested implementation order.

A reference prototype lives in `vs_scraper/`. Read it for protocol details. Do not import from it. **vs_scraper/ is gitignored** — it stays local for reference only and never goes to GitHub.

## Project structure

```
.
├── CLAUDE.md                    # this file (project-wide instructions)
├── .gitignore
├── docs/
│   └── scraper/
│       └── spec.md              # detailed spec for the scraper component
├── scraper/                     # current focus: data collection component
└── vs_scraper/                  # legacy prototype, local only, gitignored
```

Future components will follow the same convention: each gets its own folder at the root, and its spec lives at `docs/<component>/spec.md`. Don't put cross-component code at the root unless it's genuinely shared infrastructure.

## Repository setup

On first run, set up the repo:

1. If `.git/` doesn't exist, run `git init`.
2. If `.gitignore` doesn't exist, create it with the contents below.
3. If no GitHub remote is configured, create a **private** GitHub repo with `gh repo create --private --source . --remote origin`. Don't push yet — wait until there's something meaningful committed.

### `.gitignore` contents

```
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
.venv/
venv/
env/
.eggs/
*.egg-info/
*.egg

# uv
.uv-cache/

# Tests / type checkers / linters
.pytest_cache/
.coverage
htmlcov/
.tox/
.mypy_cache/
.ruff_cache/

# IDE / OS
.vscode/
.idea/
*.swp
*.swo
.DS_Store
Thumbs.db

# Secrets and runtime data
.env
.env.local
*.db
*.db-journal
*.db-wal
*.db-shm
browser_profile/
data/
logs/
exports/
parsed/

# Legacy prototype (local reference only, not for GitHub)
vs_scraper/
```

If test fixtures need real captured frames from the prototype, copy a small curated subset into `scraper/tests/fixtures/` and track those — don't track the full `vs_scraper/captures/`.

## Git workflow

- **Commit regularly.** After each meaningful unit of work — a passing test suite, a working module, a completed step from the spec's implementation order. Don't bundle a whole feature into one giant commit.
- **Conventional commit messages.** Format: `<type>: <subject>`. Types: `feat`, `fix`, `chore`, `docs`, `test`, `refactor`. One-line subject, lowercase, no period. Example: `feat: add season detector` or `fix: handle None playlistId in parser`.
- **No co-author trailers.** Do not add `Co-Authored-By: Claude`, "Generated with Claude Code", robot emoji, or any other AI attribution to commits. Commits should look like a human wrote them.
- **No commit body** unless a non-trivial decision needs explaining. Most commits are one line.
- **Push after milestones.** Roughly after each numbered step in the spec's implementation order, or when local commits have piled up enough that losing them would hurt. `git push -u origin main` for the first push, `git push` after.

## How to start a task

1. Read this file.
2. Read the spec for the component you're working in (`docs/<component>/spec.md`).
3. Confirm repo is set up (init, gitignore, remote). If not, do that first.
4. If the spec is ambiguous, ask before guessing. Don't invent schemas, endpoints, or behavior.
5. Follow the spec's implementation order. Each step has acceptance criteria — meet them before moving on. Commit after each.

## Project-wide conventions

These apply to every component, not just the scraper.

- **`uv`** for Python environment and package management. Never `pip install`. Always `uv add` or `uv sync`. Run things with `uv run`.
- **Python 3.11+** unless a component spec says otherwise.
- **No em dashes** anywhere — code, comments, docstrings, markdown. Use commas, parentheses, or two sentences.
- **Plain language.** No marketing tone. No hype words ("seamless", "robust", "elegant", "powerful").
- **Type hints** on public functions.
- **Configuration in files**, secrets in `.env`, never in code. No hardcoded URLs, paths, thresholds, or credentials.
- **Tests must pass** before any commit (`uv run pytest` in the relevant component).

## What not to do

- Don't add betting, wagering, or stake-placement code anywhere.
- Don't add features the relevant component spec doesn't call for. Propose spec changes first.
- Don't try to automate SportyBet login. Persistent profile + manual first login is the answer.
- Don't bypass Cloudflare or any bot mitigation. If sessions won't stand up cleanly, that's a research problem, not a code problem.
- Don't import across component folders without an explicit shared-package convention. Components stay independent until shared code is justified.
- Don't commit secrets, browser profiles, databases, runtime data, or `vs_scraper/`. The gitignore should catch these but check before staging.

## When unsure

Re-read the relevant spec section. If it's not there, ask. Don't guess on data layout, schema names, or operational behavior — downstream components will depend on them.