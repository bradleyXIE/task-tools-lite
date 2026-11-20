# task-tools-lite

A small task runner with a web UI. You create a task with a goal, an LLM loop works on it using a set of pluggable tools, and every step is recorded as events, facts, findings and a graph you can browse.

## Features

- Tasks with goals, hints, events, facts and findings, stored in PostgreSQL.
- An LLM runner loop (Anthropic or any OpenAI-compatible endpoint) that calls tools until the goals are met.
- Tools: builtin, HTTP and script tools, plus a Claude-Code-style SDK (Bash, Read, Write, Edit, Glob, Grep, WebFetch, persistent shell sessions, background tasks).
- Next.js UI: task list, task detail, graph view, report view, tool catalogue.
- One-command container deployment and two development modes (host and container).

## Architecture

```
 browser --> web (Next.js, :3010) --/api/*--> api (FastAPI, :8010) --> postgres (:5440)
                                               |
                                               +--> tools (Bash, files, web, ...) --> /data (task workdirs)
```

- `web` proxies `/api/*` to the API (Next.js rewrites, baked at build time in the container image).
- `api` runs the HTTP API, the runner loop and the tools.
- `postgres` keeps all task data in the `pgdata` volume. Task workdirs and background output live in the `task_data` volume (`/data`).

Published ports are bound to `127.0.0.1` only. The app has no authentication.

## Quick start (containers)

Requirements: Docker with Compose v2.24 or newer.

```bash
make run                      # creates .env if missing, builds, starts Postgres + API + Web (all in Docker)
# edit .env and set LLM_API_KEY (see "LLM setup"), then:
make restart
```

Everything the app needs (Python, Node build, Postgres and the agent's CLI tools such as curl, nmap, dig, rg) lives in the
Docker images, so Docker is the only requirement. Other commands: `make stop`, `make logs`, `make ps`, `make shell`, `make help`.

Open:

- UI: http://127.0.0.1:3010
- API health: http://127.0.0.1:8010/api/health
- API docs: http://127.0.0.1:8010/docs

Stop the stack without losing data:

```bash
make stop                     # keeps the pgdata and task_data volumes
```

Never add `-v` to `docker compose down` unless you really want to delete the database. No Makefile target removes volumes.

Different ports (for example when 8010 or 3010 are taken):

```bash
API_PORT=8031 WEB_PORT=3031 PG_PORT=5450 docker compose up -d --build --wait
```

Use a separate `COMPOSE_PROJECT_NAME` as well if you want a second, independent stack with its own volumes.

## Development mode

### A. Host dev mode (hot reload on the host)

Postgres runs in Docker, the backend and the web app run on your machine.

```bash
cp .env.example .env
./dev.sh                      # or: make dev
```

`dev.sh` starts only the `postgres` service, creates `backend/.venv` if needed, installs the backend with dev extras, then runs `uvicorn --reload` on `API_PORT` (default 8010) and `next dev` on `WEB_PORT` (default 3010). It refuses to start if one of those ports is already in use.

Note: in this mode the Bash tool runs commands directly on your host.

### B. Container dev mode (hot reload in containers)

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build     # or: make dev-docker
```

The override builds the `dev` targets, bind-mounts `backend/app`, `backend/tests` and `web/src`, runs `uvicorn --reload` and `next dev`, and keeps `.next` in a named volume. Edit a file under `backend/app` or `web/src` and the matching server reloads (see `docker compose logs -f api web`).

Note: in containerised modes (quick start and container dev mode) the Bash tool is confined to the api container: it can only touch the container filesystem and `/data`, not your host.

## Configuration

Set values in `.env` (copy `.env.example`). Docker Compose uses `.env` for interpolation, and the api container loads it as its environment.

| Variable | Default | Purpose |
|---|---|---|
| `PG_USER`, `PG_PASSWORD`, `PG_DB` | `tasktools` | Postgres credentials and database name |
| `PG_PORT` | `5440` | Host port for Postgres (loopback) |
| `API_PORT` | `8010` | Host port for the API |
| `WEB_PORT` | `3010` | Host port for the UI |
| `DATABASE_URL` | `postgresql://tasktools:tasktools@127.0.0.1:5440/tasktools` | Used in host mode. Containers build their own URL pointing at the `postgres` service |
| `TEST_DATABASE_URL` | `<database>_test` on the same server | Database used by tests. Must end with `_test` |
| `DATA_DIR` | `<repo>/data` (host), `/data` (container) | Task workdirs and background output |
| `LLM_FORMAT` | `anthropic` | `anthropic` or `openai` |
| `LLM_API_KEY` | empty | Provider key. Without it tasks are manual-only |
| `LLM_MODEL` | `claude-sonnet-5-5` | Model name |
| `LLM_BASE_URL` | empty | Gateway or OpenAI-compatible base URL |
| `MAX_STEPS` | `30` | Maximum runner steps per run |
| `SCRIPT_TIMEOUT` | `10` | Seconds for script tools |
| `HTTP_TOOL_TIMEOUT` | `15` | Seconds for HTTP tools |
| `TOOL_OUTPUT_CAP` | `20000` | Maximum characters of tool output kept |
| `TOOL_ENV_PASSTHROUGH` | empty | Comma-separated variable names forwarded to agent-spawned shells (secrets are filtered otherwise) |

## LLM setup

Anthropic (default):

```
LLM_FORMAT=anthropic
LLM_API_KEY=<your key>
LLM_MODEL=claude-sonnet-5-5
# LLM_BASE_URL=<gateway, no trailing /v1>
```

OpenAI-compatible (OpenAI, DeepSeek, Qwen, Ollama, vLLM, OpenRouter):

```
LLM_FORMAT=openai
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4.1
LLM_API_KEY=<your key>
```

The model must support tool calling. Check the configuration from the backend directory (host mode):

```bash
cd backend && .venv/bin/python -m app.runner.llm
```

Inside a container, `127.0.0.1` is the container itself. To reach a service running on your host (for example Ollama) use `host.docker.internal`, e.g. `LLM_BASE_URL=http://host.docker.internal:11434/v1`. The api service maps that name to the host gateway.

## Tools

| Category | Tools |
|---|---|
| Task and goals | `get_task`, `list_tasks`, `spawn_task`, `pause_task`, `resume_task`, `set_goals`, `list_goals`, `goal_met`, `prove_goal`, `add_hint`, `add_task_hint`, `TodoWrite`, `sleep` |
| Files | `Read`, `Write`, `Edit`, `MultiEdit`, `LS`, `Glob`, `Grep` |
| Execution and shell | `Bash` (timeout, background), `shell_open`, `shell_send`, `shell_read`, `shell_list`, `shell_close`, `Monitor`, `TaskList`, `TaskOutput`, `TaskStop` |
| Web | `WebFetch`, `web_search` |
| Findings, facts and assets | `record_fact`, `list_facts`, `report_finding`, `update_finding_report`, `list_findings`, `list_task_findings`, `get_finding_record`, `insert_assets`, `list_assets`, `list_untested_assets`, `add_company_scope`, `add_task_scope`, `list_companies` |
| Graph and traces | `get_task_graph`, `graph_overview`, `node_detail`, `get_task_worker_trace`, `list_task_worker_traces`, `search_task_worker_traces`, `list_llm_profiles` |

You can add custom HTTP and script tools through the API (`/api/tools`). Design notes live in `docs/design/` (`CONTRACT.md`, `CONTRACT2.md`, `CONTRACT3.md`).

## API overview

- `GET /api/health`
- `/api/tasks`, `/api/tasks/{id}/{control,goals,hints,events}`
- `/api/tools`, `POST /api/tools/{name}/call`

Interactive docs are served at `/docs`.

## Testing

Tests never touch your dev data. `backend/tests/conftest.py` takes `TEST_DATABASE_URL` (default: the `DATABASE_URL` database name plus `_test`, e.g. `tasktools_test`), creates that database if it is missing, applies the schema, and aborts the whole session unless the database name ends with `_test`. Tests create their own uniquely named rows and clean up only those; there are no bulk deletes or truncates.

```bash
# host (needs the compose Postgres running)
docker compose up -d --wait postgres
cd backend && .venv/bin/pytest          # or: make test

# container
make test-docker                        # runs pytest in the dev api container
```

Do not point `TEST_DATABASE_URL` at your dev database. The guard will refuse to run.

## Security notes

- There is no authentication. Everything is bound to loopback; keep it that way or put an authenticating proxy in front.
- The Bash tool is powerful. In host dev mode it runs as your user on your machine. In containerised mode the container is the isolation boundary (non-root user, only `/data` is writable state).
- The Bash tool has a small safety floor against irreversibly destructive commands. It is not a sandbox.
- Do not commit `.env`; it is listed in `.gitignore`.

### Secrets

- Never print secrets. `docker compose config`, `docker inspect`, `env` and `cat .env` all show your API key; use `make config`
  for a masked compose config, and share logs only after checking them.
- Processes started by the agent's `Bash`, `Monitor`, background and `shell_*` tools get a filtered environment: names such as
  `LLM_*`, `DATABASE_URL`, `*_API_KEY`, `*_TOKEN`, `*_SECRET`, `*PASSWORD*`, `ANTHROPIC_*`, `OPENAI_*`, `PG*` are removed
  (`backend/app/tools/sdk/safeenv.py`), so a model or a prompt-injected web page cannot read them with `env`.
  Use `TOOL_ENV_PASSTHROUGH=NAME1,NAME2` to forward specific variables on purpose.
- The `.env` file is git-ignored. The key is still present in the API container's environment (the API process needs it), so
  anyone with Docker access on the host can read it.

## Project layout

```
backend/            FastAPI app (app/), tests (tests/), Dockerfile (targets: base, dev, runtime)
web/                Next.js UI (src/), Dockerfile (targets: deps, dev, build, runner)
docs/design/        Design notes (CONTRACT*.md)
docker-compose.yml  postgres + api + web
docker-compose.dev.yml  container dev mode override
dev.sh              host dev mode
Makefile            thin wrappers (up, down, logs, dev, dev-docker, test, test-docker, lint)
.env.example        configuration template
```

## Troubleshooting

- Port already in use: set `API_PORT`, `WEB_PORT` or `PG_PORT` in `.env`, or stop the process holding the port (`lsof -nP -iTCP:<port> -sTCP:LISTEN`).
- UI shows "Reconnecting" or requests fail: check `curl http://127.0.0.1:8010/api/health` and `docker compose ps`. The web container only becomes healthy after the api is healthy.
- Container cannot reach a service on your host: use `host.docker.internal` instead of `127.0.0.1`.
- A "thinking signature" error from the Anthropic API after changing the model or editing history: the runner keeps its system prompt static so cached conversations stay valid; start a new run after switching models.
- Logs: `docker compose logs -f api web postgres` (containers) or the terminal running `./dev.sh` (host).
- Containerised web build needs the API address at build time (`API_URL`, default `http://api:8000`), because Next.js bakes rewrites into the build.

## Verification notes

The container setup was verified with a separate Compose project (`COMPOSE_PROJECT_NAME=ttl-verify`, `PG_PORT=5450`, alternate API and web ports) so a regular dev stack is never touched:

- `docker compose config` succeeds for the base file and for base plus `docker-compose.dev.yml`.
- `docker compose up -d --build --wait` reaches healthy for postgres, api and web; `/api/health` and `/tasks` work through the web port, and `POST /api/tasks` works through the web proxy.
- The `Bash` tool (`curl` to a host stub via `host.docker.internal`) and `WebFetch` work inside the api container; tasks survive `docker compose restart api`.
- Dev override: touching `backend/app/main.py` makes uvicorn log a reload, the web dev container serves `/tasks`, and `make test-docker` passes the whole suite (47 tests).
