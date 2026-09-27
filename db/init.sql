CREATE TABLE IF NOT EXISTS products (
  id SERIAL PRIMARY KEY,
  name VARCHAR(120) NOT NULL,
  part_number VARCHAR(80) NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lots (
  id SERIAL PRIMARY KEY,
  lot_number VARCHAR(80) NOT NULL UNIQUE,
  product_id INTEGER NOT NULL REFERENCES products(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS work_orders (
  id SERIAL PRIMARY KEY,
  work_order_number VARCHAR(80) NOT NULL UNIQUE,
  product_id INTEGER NOT NULL REFERENCES products(id),
  quantity INTEGER NOT NULL,
  status VARCHAR(40) NOT NULL DEFAULT 'RELEASED',
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS equipment (
  id SERIAL PRIMARY KEY,
  equipment_code VARCHAR(80) NOT NULL UNIQUE,
  name VARCHAR(120) NOT NULL,
  process_type VARCHAR(80) NOT NULL,
  status VARCHAR(40) NOT NULL DEFAULT 'IDLE',
  current_wafer_id VARCHAR(80),
  utilization_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
  total_runtime_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
  last_event_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS recipes (
  id SERIAL PRIMARY KEY,
  recipe_code VARCHAR(80) NOT NULL UNIQUE,
  name VARCHAR(120) NOT NULL,
  version VARCHAR(40) NOT NULL,
  process_type VARCHAR(80) NOT NULL,
  nominal_duration_seconds INTEGER NOT NULL,
  parameters JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS wafers (
  id SERIAL PRIMARY KEY,
  wafer_code VARCHAR(80) NOT NULL UNIQUE,
  lot_id INTEGER NOT NULL REFERENCES lots(id),
  status VARCHAR(40) NOT NULL DEFAULT 'READY',
  current_process VARCHAR(80) NOT NULL DEFAULT 'LITHOGRAPHY',
  process_index INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS process_runs (
  id SERIAL PRIMARY KEY,
  wafer_id INTEGER NOT NULL REFERENCES wafers(id),
  process_type VARCHAR(80) NOT NULL,
  equipment_id INTEGER NOT NULL REFERENCES equipment(id),
  recipe_id INTEGER NOT NULL REFERENCES recipes(id),
  started_at TIMESTAMPTZ NOT NULL,
  completed_at TIMESTAMPTZ,
  status VARCHAR(40) NOT NULL DEFAULT 'RUNNING',
  result VARCHAR(40),
  metrics JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS telemetry (
  id BIGSERIAL PRIMARY KEY,
  equipment_id INTEGER NOT NULL REFERENCES equipment(id),
  wafer_code VARCHAR(80),
  recorded_at TIMESTAMPTZ NOT NULL,
  temperature_c DOUBLE PRECISION,
  pressure_mtorr DOUBLE PRECISION,
  rf_power_w DOUBLE PRECISION,
  status VARCHAR(40),
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS quality_results (
  id BIGSERIAL PRIMARY KEY,
  wafer_id INTEGER NOT NULL REFERENCES wafers(id),
  process_run_id INTEGER REFERENCES process_runs(id),
  metric VARCHAR(120) NOT NULL,
  expected DOUBLE PRECISION NOT NULL,
  measured DOUBLE PRECISION NOT NULL,
  tolerance DOUBLE PRECISION NOT NULL,
  result VARCHAR(40) NOT NULL,
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS events (
  id BIGSERIAL PRIMARY KEY,
  event_id VARCHAR(120) NOT NULL UNIQUE,
  event_type VARCHAR(80) NOT NULL,
  wafer_code VARCHAR(80),
  equipment_code VARCHAR(80),
  occurred_at TIMESTAMPTZ NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_process_runs_wafer ON process_runs(wafer_id);
CREATE INDEX IF NOT EXISTS idx_telemetry_equipment_time ON telemetry(equipment_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_wafers_status ON wafers(status);
