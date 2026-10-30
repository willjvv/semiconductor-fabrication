'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';

// Same-origin reverse proxy configured in next.config.mjs.
const API_URL = '/api';
const PROCESS_ORDER = ['LITHOGRAPHY', 'ETCH', 'DEPOSITION', 'INSPECTION'];
const PROCESS_LABELS: Record<string, string> = {
  LITHOGRAPHY: 'Lithography',
  ETCH: 'Etch',
  DEPOSITION: 'Deposition',
  INSPECTION: 'Inspection',
};

type Equipment = {
  equipment_code: string;
  name: string;
  process_type: string;
  status: string;
  current_wafer_id: string | null;
  utilization_seconds: number;
  total_runtime_seconds: number;
  last_event_at: string | null;
  telemetry_recorded_at: string | null;
  temperature_c: number | null;
  pressure_mtorr: number | null;
  rf_power_w: number | null;
  telemetry_status: string | null;
  telemetry_source: string | null;
  telemetry_alarm_code: string | null;
  telemetry_last_event: string | null;
};

type Wafer = {
  wafer_code: string;
  lot_number: string;
  part_number: string;
  status: string;
  current_process: string;
  process_index: number;
  updated_at: string;
};

type Event = {
  event_id: string;
  event_type: string;
  wafer_code: string | null;
  equipment_code: string | null;
  occurred_at: string;
  payload: Record<string, unknown>;
};

type TelemetryPoint = {
  id: number;
  recorded_at: string;
  temperature_c: number | null;
  pressure_mtorr: number | null;
  rf_power_w: number | null;
  status: string | null;
  source: string | null;
  alarm_code: string | null;
  last_event: string | null;
  payload: Record<string, unknown>;
};

type IntegrationHealth = {
  generated_at: string;
  api_database: { status: string; detail: string };
  telemetry_pipeline: {
    status: string;
    detail: string;
    latest_sample_at: string | null;
    latest_sample_age_seconds: number | null;
    samples_last_60_seconds: number;
    fresh_opcua_equipment: number;
    total_equipment: number;
  };
  equipment: Array<{
    equipment_code: string;
    latest_sample_at: string | null;
    sample_age_seconds: number | null;
    source: string | null;
    last_event: string | null;
    fresh: boolean;
  }>;
  note: string;
};

type Dashboard = {
  stats: { total_wafers: number; in_process: number; completed: number; scrapped: number };
  yield_pct: number;
  events: Event[];
};

type WaferDetail = {
  wafer: Wafer & { product_name: string; created_at: string };
  process_runs: Array<{
    id: number;
    process_type: string;
    equipment_code: string;
    recipe_code: string;
    version: string;
    started_at: string;
    completed_at: string | null;
    status: string;
    result: string | null;
    metrics: Record<string, unknown>;
  }>;
  quality_results: Array<{
    metric: string;
    expected: number;
    measured: number;
    tolerance: number;
    result: string;
    recorded_at: string;
  }>;
};

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { cache: 'no-store' });
  if (!response.ok) throw new Error(`${path} returned HTTP ${response.status}`);
  return response.json() as Promise<T>;
}

function fmtDate(value?: string | null) {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function timeAgo(seconds?: number | null) {
  if (seconds == null || !Number.isFinite(seconds)) return 'No sample yet';
  if (seconds < 1) return 'just now';
  return `${Math.round(seconds)}s ago`;
}

function statusClass(status?: string | null) {
  return `status ${(status || 'unknown').toLowerCase().replace(/[^a-z0-9]+/g, '-')}`;
}

function machineStatus(equipment?: Equipment) {
  if (!equipment) return 'UNKNOWN';
  return equipment.status || equipment.telemetry_status || 'UNKNOWN';
}

function WaferGlyph() {
  return (
    <svg className="wafer-glyph" viewBox="0 0 54 54" role="img" aria-label="Wafer">
      <circle cx="27" cy="27" r="22" />
      <circle cx="27" cy="27" r="16" />
      <path d="M13 20h28M9 27h36M13 34h28M20 13v28M27 9v36M34 13v28" />
    </svg>
  );
}

function Metric({ label, value, note }: { label: string; value: string | number; note?: string }) {
  return (
    <article className="metric">
      <span>{label}</span>
      <strong>{value}</strong>
      {note && <small>{note}</small>}
    </article>
  );
}

function LineChart({
  title,
  points,
  valueKey,
  unit,
  digits = 1,
}: {
  title: string;
  points: TelemetryPoint[];
  valueKey: 'temperature_c' | 'pressure_mtorr' | 'rf_power_w';
  unit: string;
  digits?: number;
}) {
  const valid = points.filter((point) => typeof point[valueKey] === 'number' && Number.isFinite(point[valueKey]));
  const values = valid.map((point) => Number(point[valueKey]));
  const min = values.length ? Math.min(...values) : 0;
  const max = values.length ? Math.max(...values) : 1;
  const padding = max === min ? Math.max(Math.abs(max) * 0.05, 1) : (max - min) * 0.14;
  const low = min - padding;
  const high = max + padding;
  const width = 440;
  const height = 130;
  const left = 44;
  const right = 12;
  const top = 10;
  const bottom = 25;
  const plotWidth = width - left - right;
  const plotHeight = height - top - bottom;
  const coordinates = valid.map((point, index) => {
    const x = valid.length <= 1 ? left + plotWidth / 2 : left + (index / (valid.length - 1)) * plotWidth;
    const y = top + ((high - Number(point[valueKey])) / (high - low || 1)) * plotHeight;
    return { x, y, point, value: Number(point[valueKey]) };
  });
  const path = coordinates.map((coord, index) => `${index === 0 ? 'M' : 'L'} ${coord.x.toFixed(2)} ${coord.y.toFixed(2)}`).join(' ');
  const latest = values.length ? values[values.length - 1] : null;

  return (
    <div className="chart-card">
      <div className="chart-heading">
        <span>{title}</span>
        <strong>{latest == null ? '—' : `${latest.toFixed(digits)} ${unit}`}</strong>
      </div>
      {coordinates.length < 2 ? (
        <div className="chart-empty">Waiting for at least two samples…</div>
      ) : (
        <svg className="trend-chart" viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${title} over recent samples`}>
          {[0, 0.5, 1].map((fraction) => {
            const y = top + fraction * plotHeight;
            const value = high - fraction * (high - low);
            return (
              <g key={fraction}>
                <line x1={left} y1={y} x2={width - right} y2={y} className="chart-gridline" />
                <text x={left - 7} y={y + 4} textAnchor="end" className="chart-axis-label">{value.toFixed(digits)}</text>
              </g>
            );
          })}
          <path d={path} className="chart-line" />
          {coordinates.map((coord) => (
            <circle key={coord.point.id} cx={coord.x} cy={coord.y} r="2.6" className="chart-point">
              <title>{`${fmtDate(coord.point.recorded_at)} · ${coord.value.toFixed(digits)} ${unit}`}</title>
            </circle>
          ))}
          <text x={left} y={height - 5} className="chart-axis-label">Older</text>
          <text x={width - right} y={height - 5} textAnchor="end" className="chart-axis-label">Latest</text>
        </svg>
      )}
    </div>
  );
}

function EquipmentDrawer({
  equipment,
  points,
  events,
  onClose,
}: {
  equipment: Equipment;
  points: TelemetryPoint[];
  events: Event[];
  onClose: () => void;
}) {
  const latest = points[points.length - 1];
  const relatedEvents = events
    .filter((event) => event.equipment_code === equipment.equipment_code)
    .slice(0, 12);
  const alarmEvents = relatedEvents.filter((event) => /alarm|down|hold|repair|maintenance/i.test(event.event_type));

  return (
    <div className="drawer-backdrop" onClick={onClose}>
      <aside className="drawer equipment-drawer" onClick={(event) => event.stopPropagation()}>
        <button className="close" onClick={onClose} aria-label="Close equipment details">×</button>
        <p className="eyebrow">EQUIPMENT DETAIL</p>
        <div className="drawer-title-row">
          <div>
            <h2>{equipment.equipment_code}</h2>
            <p className="drawer-subtitle">{equipment.name}</p>
          </div>
          <span className={statusClass(machineStatus(equipment))}>{machineStatus(equipment)}</span>
        </div>
        <div className="detail-metric-grid">
          <div><span>Current wafer</span><strong>{equipment.current_wafer_id || '—'}</strong></div>
          <div><span>Process</span><strong>{PROCESS_LABELS[equipment.process_type] || equipment.process_type}</strong></div>
          <div><span>Telemetry source</span><strong>{equipment.telemetry_source || '—'}</strong></div>
          <div><span>Last sample</span><strong>{fmtDate(equipment.telemetry_recorded_at)}</strong></div>
        </div>
        <h3>Live measurements</h3>
        <div className="telemetry-readings">
          <div><span>Temperature</span><strong>{equipment.temperature_c == null ? '—' : `${Number(equipment.temperature_c).toFixed(1)} °C`}</strong></div>
          <div><span>Pressure</span><strong>{equipment.pressure_mtorr == null ? '—' : `${Number(equipment.pressure_mtorr).toFixed(2)} mTorr`}</strong></div>
          <div><span>RF power</span><strong>{equipment.rf_power_w == null ? '—' : `${Number(equipment.rf_power_w).toFixed(1)} W`}</strong></div>
          <div><span>Alarm code</span><strong className={Number(equipment.telemetry_alarm_code || 0) > 0 ? 'alarm-value' : ''}>{equipment.telemetry_alarm_code || '0'}</strong></div>
        </div>
        <h3>Recent telemetry trends</h3>
        <p className="section-caption">Latest {points.length} readings stored in PostgreSQL. Values are synthetic simulation data.</p>
        <div className="chart-stack">
          <LineChart title="Chamber temperature" points={points} valueKey="temperature_c" unit="°C" />
          <LineChart title="Chamber pressure" points={points} valueKey="pressure_mtorr" unit="mTorr" digits={2} />
          <LineChart title="RF power" points={points} valueKey="rf_power_w" unit="W" />
        </div>
        <h3>Alarms and equipment events</h3>
        {alarmEvents.length === 0 ? (
          <p className="muted">No equipment alarm/maintenance events in the recent event window.</p>
        ) : (
          <div className="alarm-list">
            {alarmEvents.map((event) => (
              <div className="alarm-row" key={event.event_id}>
                <span className={statusClass(/down|alarm/i.test(event.event_type) ? 'down' : 'hold')}><span className="alarm-dot" />{event.event_type}</span>
                <div><strong>{event.wafer_code || equipment.equipment_code}</strong><time>{fmtDate(event.occurred_at)}</time></div>
                <small>{String(event.payload?.reason || event.payload?.message || '')}</small>
              </div>
            ))}
          </div>
        )}
        {latest && <p className="section-caption last-read">Latest payload: {latest.source || 'unknown source'} · {fmtDate(latest.recorded_at)} · {latest.last_event || 'telemetry update'}</p>}
      </aside>
    </div>
  );
}

export default function Dashboard() {
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [equipment, setEquipment] = useState<Equipment[]>([]);
  const [wafers, setWafers] = useState<Wafer[]>([]);
  const [health, setHealth] = useState<IntegrationHealth | null>(null);
  const [selectedWafer, setSelectedWafer] = useState<WaferDetail | null>(null);
  const [selectedWaferCode, setSelectedWaferCode] = useState<string | null>(null);
  const [selectedEquipmentCode, setSelectedEquipmentCode] = useState<string | null>(null);
  const [telemetryPoints, setTelemetryPoints] = useState<TelemetryPoint[]>([]);
  const [apiEvents, setApiEvents] = useState<Event[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [resetting, setResetting] = useState(false);

  const load = useCallback(async () => {
    try {
      const [d, e, w, h, events] = await Promise.all([
        getJson<Dashboard>('/dashboard'),
        getJson<Equipment[]>('/equipment'),
        getJson<Wafer[]>('/wafers?limit=50'),
        getJson<IntegrationHealth>('/integration/health'),
        getJson<Event[]>('/events?limit=100'),
      ]);
      setDashboard(d);
      setEquipment(e);
      setWafers(w);
      setHealth(h);
      setApiEvents(events);
      setLastUpdated(new Date());
      setError(null);

      if (selectedEquipmentCode) {
        const points = await getJson<TelemetryPoint[]>(`/equipment/${encodeURIComponent(selectedEquipmentCode)}/telemetry?limit=60`);
        setTelemetryPoints(points);
      }
      if (selectedWaferCode) {
        const detail = await getJson<WaferDetail>(`/wafers/${encodeURIComponent(selectedWaferCode)}`);
        setSelectedWafer(detail);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to connect to MES API');
    }
  }, [selectedEquipmentCode, selectedWaferCode]);

  useEffect(() => {
    void load();
    const timer = setInterval(() => void load(), 2000);
    return () => clearInterval(timer);
  }, [load]);

  const wipByProcess = useMemo(() => {
    return PROCESS_ORDER.map((process) => ({
      process,
      count: wafers.filter((wafer) => wafer.current_process === process && ['READY', 'PROCESSING', 'HOLD'].includes(wafer.status)).length,
    }));
  }, [wafers]);

  const selectEquipment = async (equipmentCode: string) => {
    setSelectedWafer(null);
    setSelectedWaferCode(null);
    setSelectedEquipmentCode(equipmentCode);
    try {
      const points = await getJson<TelemetryPoint[]>(`/equipment/${encodeURIComponent(equipmentCode)}/telemetry?limit=60`);
      setTelemetryPoints(points);
    } catch {
      setTelemetryPoints([]);
    }
  };

  const inspectWafer = async (code: string) => {
    setSelectedEquipmentCode(null);
    setTelemetryPoints([]);
    setSelectedWaferCode(code);
    try {
      const detail = await getJson<WaferDetail>(`/wafers/${encodeURIComponent(code)}`);
      setSelectedWafer(detail);
    } catch (err) {
      setError(err instanceof Error ? err.message : `Could not load ${code}`);
    }
  };

  const reset = async () => {
    setResetting(true);
    try {
      const response = await fetch(`${API_URL}/simulation/reset`, { method: 'POST' });
      if (!response.ok) throw new Error(`Reset returned HTTP ${response.status}`);
      window.location.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to reset the simulation');
      setResetting(false);
    }
  };

  const telemetryStatus = health?.telemetry_pipeline.status || 'unknown';
  const freshEquipment = health?.telemetry_pipeline.fresh_opcua_equipment || 0;
  const totalEquipment = health?.telemetry_pipeline.total_equipment || equipment.length || 4;
  const pipelineStatus = health?.telemetry_pipeline.status || 'unknown';
  const integrationSteps = [
    {
      name: 'Equipment / OPC-UA',
      detail: `${freshEquipment}/${totalEquipment} machines with fresh OPC-UA samples`,
      status: freshEquipment > 0 ? (freshEquipment === totalEquipment ? 'healthy' : 'degraded') : 'down',
      icon: '01',
    },
    {
      name: 'Integration gateway',
      detail: 'Tag mapping and telemetry normalization',
      status: pipelineStatus,
      icon: '02',
    },
    {
      name: 'MQTT → PostgreSQL',
      detail: `${health?.telemetry_pipeline.samples_last_60_seconds ?? '—'} samples stored in the last minute`,
      status: pipelineStatus,
      icon: '03',
    },
    {
      name: 'FastAPI / dashboard',
      detail: error ? 'Latest API request failed' : `Dashboard refreshed ${lastUpdated ? fmtDate(lastUpdated.toISOString()) : '—'}`,
      status: error ? 'down' : health?.api_database.status || 'unknown',
      icon: '04',
    },
  ];

  return (
    <main className="page-shell">
      <header className="header">
        <div>
          <p className="eyebrow">MINIFAB • MES MVP</p>
          <h1>Semiconductor Fab Control Center</h1>
          <p className="subtitle">A simulated production line with live equipment telemetry, process tracking, and integration health.</p>
        </div>
        <div className="header-actions">
          <span className={`live-dot ${error ? 'offline' : ''}`} />
          <span>{error ? 'Connection issue' : 'Live polling'} · {lastUpdated ? lastUpdated.toLocaleTimeString() : 'connecting…'}</span>
          <button className="reset-button" onClick={reset} disabled={resetting}>{resetting ? 'Resetting…' : 'Reset simulation'}</button>
        </div>
      </header>

      {error && <div className="error-banner">API connection problem: {error}. Verify the stack with <code>docker compose ps</code> and try <code>/api/health</code>.</div>}

      <section className="metric-grid">
        <Metric label="Total wafers" value={dashboard?.stats.total_wafers ?? '—'} note="In current demo run" />
        <Metric label="In process / hold" value={dashboard?.stats.in_process ?? '—'} note="Work in progress" />
        <Metric label="Completed" value={dashboard?.stats.completed ?? '—'} note="Finished process route" />
        <Metric label="Scrapped" value={dashboard?.stats.scrapped ?? '—'} note="Failed final inspection" />
        <Metric label="Inspection yield" value={dashboard ? `${dashboard.yield_pct}%` : '—'} note="Passed / recorded quality results" />
      </section>

      <section className="grid-two">
        <article className="panel">
          <div className="panel-title"><h2>Equipment</h2><span>4 simulated stations</span></div>
          <div className="equipment-list">
            {equipment.map(eq => (
              <div className="equipment-row" key={eq.equipment_code}>
                <div>
                  <strong>{eq.equipment_code}</strong>
                  <span>{eq.name}</span>
                </div>
                <div className="equipment-mid">
                  <span>{eq.process_type}</span>
                  <span>{eq.current_wafer_id || 'No wafer'}</span>
                </div>
                <div className="equipment-telemetry">
                  <span>Temp: {eq.temperature_c == null ? '—' : `${Number(eq.temperature_c).toFixed(1)} °C`}</span>
                  <span>Pressure: {eq.pressure_mtorr == null ? '—' : `${Number(eq.pressure_mtorr).toFixed(2)} mTorr`}</span>
                  <span>RF: {eq.rf_power_w == null ? '—' : `${Number(eq.rf_power_w).toFixed(1)} W`}</span>
                  <span className="telemetry-source">{eq.telemetry_source ? eq.telemetry_source.toUpperCase() : 'Waiting for OPC-UA'} · {fmtDate(eq.telemetry_recorded_at)}</span>
                </div>
                <span className={statusClass(eq.status)}>{eq.status}</span>
              </div>
            ))}
          </div>
          <div className="mini-note">The simulator deliberately takes ETCH-01 offline after ~35 seconds and repairs it ~15 seconds later.</div>
        </article>

        <article className="panel">
          <div className="panel-title"><h2>WIP by process</h2><span>ready + processing + hold</span></div>
          <div className="process-bars">
            {wipByProcess.map(({ process, count }) => {
              const max = Math.max(1, ...wipByProcess.map((item) => item.count));
              return <div className="bar-row" key={process}><span>{PROCESS_LABELS[process]}</span><div className="bar-track"><div className="bar-fill" style={{ width: `${(count / max) * 100}%` }} /></div><b>{count}</b></div>;
            })}
          </div>
          <div className="mini-note">A wafer's current process is maintained by manufacturing events. Selecting a station opens its telemetry and recent equipment events.</div>
        </article>

        <article className="panel">
          <div className="panel-title"><h2>Recent manufacturing events</h2><span>{dashboard?.events.length ?? 0} latest events</span></div>
          <div className="event-list">
            {(dashboard?.events || []).map((event) => (
              <button className="event-row event-button" key={event.event_id} onClick={() => event.equipment_code && void selectEquipment(event.equipment_code)}>
                <time>{fmtDate(event.occurred_at)}</time>
                <div><strong>{event.event_type}</strong><span>{[event.wafer_code, event.equipment_code].filter(Boolean).join(' · ') || 'Factory event'}</span></div>
                <span className="event-chevron">↗</span>
              </button>
            ))}
            {(dashboard?.events || []).length === 0 && <p className="muted">No events yet. Events appear as wafers move through the process.</p>}
          </div>
        </article>
      </section>

      <section className="panel wafer-panel">
        <div className="panel-title"><h2>Wafer queue</h2><span>{wafers.length} visible</span></div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Wafer</th><th>Lot</th><th>Process</th><th>Status</th><th>Updated</th><th /></tr></thead>
            <tbody>
              {wafers.map((wafer) => <tr key={wafer.wafer_code}>
                <td><button className="link-button" onClick={() => void inspectWafer(wafer.wafer_code)}>{wafer.wafer_code}</button></td>
                <td>{wafer.lot_number}</td>
                <td>{PROCESS_LABELS[wafer.current_process] || wafer.current_process}</td>
                <td><span className={statusClass(wafer.status)}>{wafer.status}</span></td>
                <td>{fmtDate(wafer.updated_at)}</td>
                <td><button className="inspect-button" onClick={() => void inspectWafer(wafer.wafer_code)}>Trace wafer</button></td>
              </tr>)}
            </tbody>
          </table>
        </div>
      </section>

      {selectedEquipmentCode && equipment.find((item) => item.equipment_code === selectedEquipmentCode) && (
        <EquipmentDrawer
          equipment={equipment.find((item) => item.equipment_code === selectedEquipmentCode)!}
          points={telemetryPoints}
          events={apiEvents}
          onClose={() => setSelectedEquipmentCode(null)}
        />
      )}

      {selectedWafer && <div className="drawer-backdrop" onClick={() => { setSelectedWafer(null); setSelectedWaferCode(null); }}>
        <aside className="drawer" onClick={(event) => event.stopPropagation()}>
          <button className="close" onClick={() => { setSelectedWafer(null); setSelectedWaferCode(null); }} aria-label="Close wafer details">×</button>
          <p className="eyebrow">WAFER TRACEABILITY</p>
          <h2>{selectedWafer.wafer.wafer_code}</h2>
          <p className="drawer-subtitle">{selectedWafer.wafer.product_name} · {selectedWafer.wafer.lot_number} · {selectedWafer.wafer.status}</p>
          <h3>Process history</h3>
          <div className="timeline">
            {selectedWafer.process_runs.map((run) => <div className="timeline-item" key={run.id}>
              <div className="timeline-dot" />
              <div><strong>{PROCESS_LABELS[run.process_type] || run.process_type}</strong><span>{run.equipment_code} · {run.recipe_code} v{run.version}</span><span>{fmtDate(run.started_at)} → {fmtDate(run.completed_at)}</span><span>Result: {run.result || run.status}</span></div>
            </div>)}
            {selectedWafer.process_runs.length === 0 && <p className="muted">No process runs recorded yet.</p>}
          </div>
          <h3>Quality results</h3>
          {selectedWafer.quality_results.length === 0 ? <p className="muted">No inspection result recorded yet.</p> : selectedWafer.quality_results.map((quality, index) => <div className="quality-card" key={index}><strong>{quality.metric}</strong><span>Expected {quality.expected} ± {quality.tolerance}</span><span>Measured {quality.measured}</span><span className={statusClass(quality.result)}>{quality.result}</span></div>)}
        </aside>
      </div>}
    </main>
  );
}
