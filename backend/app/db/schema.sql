-- Idempotent schema; applied at startup.
CREATE TABLE IF NOT EXISTS tasks (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT '',
  goal TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'created'
    CHECK (status IN ('created','running','paused','done','failed','timeout')),
  paused BOOLEAN NOT NULL DEFAULT FALSE,
  pause_reason TEXT NOT NULL DEFAULT '',
  stop_reason TEXT NOT NULL DEFAULT '',
  timeout_seconds INT NOT NULL DEFAULT 0,
  active_run_seconds BIGINT NOT NULL DEFAULT 0,
  run_segment_started_at TIMESTAMPTZ,
  deadline_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  deleted_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_tasks_alive ON tasks (id DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS task_goals (
  id BIGSERIAL PRIMARY KEY,
  task_id BIGINT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  text TEXT NOT NULL,
  met BOOLEAN NOT NULL DEFAULT FALSE,
  evidence TEXT NOT NULL DEFAULT '',
  met_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_goals_task ON task_goals (task_id);

CREATE TABLE IF NOT EXISTS task_hints (
  id BIGSERIAL PRIMARY KEY,
  task_id BIGINT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  text TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_hints_task ON task_hints (task_id);

CREATE TABLE IF NOT EXISTS task_events (
  id BIGSERIAL PRIMARY KEY,
  task_id BIGINT REFERENCES tasks(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,            -- status | tool_call | llm | note | error
  tool_name TEXT NOT NULL DEFAULT '',
  input JSONB,
  output JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_events_task ON task_events (task_id, id);

CREATE TABLE IF NOT EXISTS tools (
  id BIGSERIAL PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL CHECK (kind IN ('builtin','http','script')),
  description TEXT NOT NULL DEFAULT '',
  input_schema JSONB NOT NULL DEFAULT '{"type":"object","properties":{}}'::jsonb,
  config JSONB NOT NULL DEFAULT '{}'::jsonb,
  read_only BOOLEAN NOT NULL DEFAULT FALSE,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END; $$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_tasks_upd ON tasks;
CREATE TRIGGER trg_tasks_upd BEFORE UPDATE ON tasks FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
DROP TRIGGER IF EXISTS trg_tools_upd ON tools;
CREATE TRIGGER trg_tools_upd BEFORE UPDATE ON tools FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- ===== ARTEX-style domain tables (facts / findings / assets / scope) =====
CREATE TABLE IF NOT EXISTS task_facts (
  id BIGSERIAL PRIMARY KEY,
  task_id BIGINT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  summary TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'confirmed',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_facts_task ON task_facts (task_id, id DESC);

CREATE TABLE IF NOT EXISTS task_findings (
  id BIGSERIAL PRIMARY KEY,
  task_id BIGINT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  vulnclass TEXT NOT NULL DEFAULT '',
  name TEXT NOT NULL DEFAULT '',
  severity TEXT NOT NULL DEFAULT 'info' CHECK (severity IN ('info','low','medium','high','critical')),
  summary TEXT NOT NULL DEFAULT '',
  evidence TEXT NOT NULL DEFAULT '',
  report TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'confirmed',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_findings_task ON task_findings (task_id, id DESC);

CREATE TABLE IF NOT EXISTS task_assets (
  id BIGSERIAL PRIMARY KEY,
  task_id BIGINT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  type TEXT NOT NULL,            -- domain | ip | url | service | cidr | other
  value TEXT NOT NULL,
  attrs JSONB NOT NULL DEFAULT '{}'::jsonb,
  tested BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (task_id, type, value)
);

CREATE TABLE IF NOT EXISTS task_scope (
  id BIGSERIAL PRIMARY KEY,
  task_id BIGINT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,            -- company | root_domain | subdomain | ip | cidr | icp | keyword
  value TEXT NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (task_id, kind, value)
);

-- ===== Contract 3: reports, sessions =====
ALTER TABLE task_goals  ADD COLUMN IF NOT EXISTS report TEXT NOT NULL DEFAULT '';
ALTER TABLE tasks       ADD COLUMN IF NOT EXISTS summary_report TEXT NOT NULL DEFAULT '';
ALTER TABLE task_events ADD COLUMN IF NOT EXISTS run_no INT;
ALTER TABLE task_events ADD COLUMN IF NOT EXISTS step INT;
