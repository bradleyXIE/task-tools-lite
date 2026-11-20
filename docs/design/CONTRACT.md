# Shared contract (backend ⇄ runner ⇄ web)

Backend: Python 3.12+, FastAPI, asyncpg (no ORM), pydantic v2. Package `backend/app`. Run: `uvicorn app.main:app --port 8000`.
Env: `DATABASE_URL` (default `postgresql://tasktools:tasktools@127.0.0.1:5440/tasktools`), `LLM_API_KEY`, `LLM_MODEL` (default `claude-sonnet-5-5`), `LLM_BASE_URL` (optional), `MAX_STEPS` (default 30), `SCRIPT_TIMEOUT` (default 10), `HTTP_TOOL_TIMEOUT` (default 15), `TOOL_OUTPUT_CAP` (default 20000 chars).
Web: Next.js 15 app router, TypeScript, Tailwind, `/api/*` rewritten to `http://127.0.0.1:8000`. All JSON ids are STRINGS (like ARTEX) in API responses; accept string or int in paths.

## Modules and owners
- `app/config.py`, `app/db/pool.py` (`get_pool()`, `init_db()` applies `schema.sql` + seeds tools, `close_db()`), `app/main.py` — **backend agent**
- `app/tasks/service.py`, `app/tasks/router.py` — **backend agent**
- `app/tools/base.py`, `app/tools/builtin.py`, `app/tools/custom.py`, `app/tools/registry.py`, `app/tools/router.py` — **tools agent**
- `app/runner/*` — **runner agent** (also owns `backend/pyproject.toml`-independent `llm.py`)
- `web/**` — **web agent**
- `backend/pyproject.toml`, `docker-compose.yml`, `.env.example`, `README.md`, `dev.sh` — **backend agent**

## tasks/service.py public API (async, take asyncpg pool as first arg `pool`)
```
class TaskError(Exception): status_code:int; message:str      # 404 not found, 409 conflict, 400 bad request
create_task(pool, name, description, goal, timeout_seconds=0, start=True) -> dict   # status 'created'; if start: status 'running' + run segment opened
get_task(pool, task_id) -> dict            # includes goals_total, goals_met, run_seconds
list_tasks(pool, status=None, limit=200) -> list[dict]
update_task(pool, task_id, name=None, description=None) -> dict
delete_task(pool, task_id) -> None         # soft delete; 409 if running (must pause first)
pause_task(pool, task_id, reason='paused_by_user') -> dict    # 409 if terminal/already paused/created-not-running
resume_task(pool, task_id) -> dict         # 409 if terminal or not paused
finish_task(pool, task_id, status, stop_reason='') -> bool   # status in done|failed|timeout; guarded: only if not already terminal (first writer wins); folds run segment; sets completed_at; returns True if it won
add_goals(pool, task_id, texts:list[str]) -> list[dict]
list_goals(pool, task_id) -> list[dict]
set_goal_met(pool, task_id, goal_id, evidence) -> dict   # if all goals met -> finish_task(done,'all_goals_met')
add_hint(pool, task_id, text) -> dict
list_hints(pool, task_id) -> list[dict]
add_event(pool, task_id|None, kind, tool_name='', input=None, output=None) -> dict
list_events(pool, task_id, after_id=0, limit=500) -> list[dict]
run_seconds(row) -> int   # active_run_seconds + open segment only while running and not paused
```
Status derivation: DB `status` in created|running|paused|done|failed|timeout; DTO `status` = 'paused' if `paused` flag set, else DB status. Terminal = done|failed|timeout (cannot resume/pause).
create_task(start=True): status='running', run_segment_started_at=now(), deadline_at=now()+timeout if timeout_seconds>0. pause: folds segment into active_run_seconds, sets paused=true,status='paused',pause_reason. resume: status='running', paused=false, new segment, pause_reason=''. Every transition writes a `task_events` row kind='status'.
After create/resume the router calls `app.runner.manager.on_task_running(task_id)`; after pause/finish/delete it calls `on_task_stopped(task_id)`. Both are sync-safe async functions defined by the runner agent (no-ops if no LLM key). Service functions themselves do NOT import the runner (avoid cycles); routers and builtin tools do the calls via a small hook list: `app.tasks.hooks` with `running_hooks: list[async fn(task_id)]` and `stopped_hooks`. Service functions call `await hooks.fire_running(task_id)` / `fire_stopped(task_id)`. **backend agent owns `app/tasks/hooks.py`**; runner registers its hooks in `app/runner/manager.py::register()`, which `main.py` calls at startup.

Task DTO: `{id, name, description, goal, status, paused, pause_reason, stop_reason, timeout_seconds, run_seconds, deadline_at, completed_at, created_at, updated_at, goals_total, goals_met}` (timestamps ISO strings or null).
Goal DTO: `{id, task_id, text, met, evidence, met_at, created_at}`. Hint DTO: `{id, task_id, text, created_at}`. Event DTO: `{id, task_id, kind, tool_name, input, output, created_at}`.

## REST (all under /api)
- `GET /api/health` → `{ok:true, runner:bool}`
- `GET /api/tasks?status=` → `{tasks:[Task]}` ; `POST /api/tasks` body `{name?, description?, goal (required), timeout_seconds?, start?:bool=true, goals?:[str]}` → 201 Task
- `GET/PATCH/DELETE /api/tasks/{id}` (PATCH `{name?,description?}`; DELETE → `{ok:true}`)
- `POST /api/tasks/{id}/control` `{action:'pause'|'resume'}` → Task (409 `{detail}` on conflict)
- `GET/POST /api/tasks/{id}/goals` (POST `{text}` or `{texts:[...]}` → `{goals:[Goal]}`); `PATCH /api/tasks/{id}/goals/{gid}` `{met:true, evidence?}` → Goal
- `GET/POST /api/tasks/{id}/hints` (POST `{text}` → Hint; GET → `{hints:[...]}`)
- `GET /api/tasks/{id}/events?after_id=` → `{events:[Event]}`
- `GET /api/tools` → `{tools:[Tool]}`; `POST /api/tools` (custom only) body `{name, kind:'http'|'script', description, input_schema, config, read_only?, enabled?}` → 201 Tool
- `GET/PATCH/DELETE /api/tools/{id}` — PATCH allows `description,input_schema,enabled` on builtin; any field on custom; DELETE only custom (builtin → 409)
- `POST /api/tools/{name}/call` `{args:{}, task_id?:string}` → `{ok:bool, output:any, error?:str}` ; validates args against input_schema (400/ok:false on invalid); writes `task_events` kind='tool_call' when task_id given
- `GET /api/tools/schema` → `{tools:[{name,description,input_schema}]}` (enabled only, MCP-style)
Errors: HTTP status with `{detail:str}`.

Tool DTO: `{id, name, kind, description, input_schema, config, read_only, enabled, builtin_default_description?}`. Builtin rows are seeded into `tools` at startup (kind='builtin', config {}), names below; code owns handlers, DB owns description/schema/enabled overrides.

## tools package API
```
# base.py
@dataclass Tool: name, description, input_schema:dict, read_only:bool, run: async (ctx:ToolContext, args:dict)->Any
@dataclass ToolContext: pool, task_id:int|None
# builtin.py
BUILTIN_TOOLS: dict[str, Tool]   # list_tasks, get_task, spawn_task, pause_task, resume_task, add_task_hint, set_goals, prove_goal, list_goals, goal_met
# registry.py
async list_tool_defs(pool, enabled_only=False) -> list[dict]   # merged DTOs (builtin defaults overridden by DB)
async call_tool(pool, name, args, task_id=None) -> dict         # {ok, output, error?}; validates with jsonschema; enforces enabled; caps output to TOOL_OUTPUT_CAP; logs event if task_id
async seed_builtin_tools(pool)  # insert missing builtin rows (ON CONFLICT DO NOTHING)
```
Builtin semantics: `prove_goal{goal_id,evidence}`, `goal_met{reason}` (finish_task done), `goal_met` / `set_goals{goals:[str]}` require ctx.task_id; `spawn_task{goal,name?,description?,timeout_seconds?}`; `pause_task/resume_task/get_task{task_id}`; `add_task_hint{task_id?,text}` (task_id defaults to ctx.task_id); `list_tasks{status?}`; `list_goals{task_id?}`.
custom http config: `{method, url, headers:{}, body_template:{}|null, timeout?}`; `{{arg}}` placeholders in url/headers/body_template string values are substituted from args; response truncated to cap; only http/https; returns `{status, body}`.
custom script config: `{code:str}` — Python source run via `python -I -c` wrapper in a subprocess with args as JSON on stdin; must set variable `result` (JSON-serializable) — wrapper prints it as JSON; killed after SCRIPT_TIMEOUT.

## runner
`app/runner/manager.py`: `register()` (adds hooks), `enabled() -> bool` (LLM_API_KEY set), `on_task_running(task_id)`, `on_task_stopped(task_id)`, `shutdown()`; keeps `dict[int, asyncio.Task]`; on startup resumes tasks with status='running' (not paused). `app/runner/loop.py`: `run_task(pool, task_id)`. `app/runner/llm.py`: thin Anthropic Messages API tool-use adapter using `httpx` (no SDK) against `{LLM_BASE_URL or https://api.anthropic.com}/v1/messages`. Loop: system prompt (goal, goals, hints, rules) → call LLM with enabled tools (as Anthropic tools) → execute each tool_use via `registry.call_tool(..., task_id)` → append tool_result → stop when task no longer running (re-read status each step), `goal_met` fired, all goals met, `MAX_STEPS` reached (→ finish failed 'max_steps'), deadline passed (→ finish timeout 'deadline'), or LLM error (→ failed with message). Logs `task_events` kind='llm' for assistant text. Injects new hints between steps.

## web
Pages: `/` redirect to `/tasks`; `/tasks` (list, status filter, new-task form with goal/name/description/timeout/initial goals, pause/resume/delete inline); `/tasks/[id]` (overview, goals add + mark met, hints add, event log polled every 2s, pause/resume); `/tools` (table builtin/custom, enable toggle, edit description, new custom tool form http|script, test-call panel with JSON args and result). Shared `src/lib/api.ts` typed client, `src/lib/types.ts`. Clean minimal Tailwind styling, light/dark via prefers-color-scheme, responsive. Use `npm` with exact pinned deps; must pass `npm run build` and `npx tsc --noEmit`.
