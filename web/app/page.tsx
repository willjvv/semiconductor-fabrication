'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';

// Browser requests go through the Next.js same-origin proxy. This avoids CORS/localhost issues
// when the dashboard is served from Docker or another hostname.
const API_URL = '/api';

type Equipment = {
  equipment_code: string;
  name: string;
  process_type: string;
  status: string;
  current_wafer_id: string | null;
  utilization_seconds: number;
  total_runtime_seconds: number;
  last_event_at: string | null;
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

function fmtDate(value?: string | null) {
  if (!value) return '—';
  return new Date(value).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function statusClass(status: string) {
  return `status ${status.toLowerCase()}`;
}

export default function Dashboard() {
  const [dashboard, setDashboard] = useState<Dashboard | null>(null);
  const [equipment, setEquipment] = useState<Equipment[]>([]);
  const [wafers, setWafers] = useState<Wafer[]>([]);
  const [selected, setSelected] = useState<WaferDetail | null>(null);
  const [selectedCode, setSelectedCode] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const load = useCallback(async () => {
    try {
      const [d, e, w] = await Promise.all([
        fetch(`${API_URL}/dashboard`, { cache: 'no-store' }).then(r => r.json()),
        fetch(`${API_URL}/equipment`, { cache: 'no-store' }).then(r => r.json()),
        fetch(`${API_URL}/wafers?limit=50`, { cache: 'no-store' }).then(r => r.json())
      ]);
      setDashboard(d);
      setEquipment(e);
      setWafers(w);
      setLastUpdated(new Date());
      setError(null);
      if (selectedCode) {
        const detail = await fetch(`${API_URL}/wafers/${selectedCode}`, { cache: 'no-store' }).then(r => r.ok ? r.json() : null);
        setSelected(detail);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to connect to MES API');
    }
  }, [selectedCode]);

  useEffect(() => {
    load();
    const timer = setInterval(load, 2000);
    return () => clearInterval(timer);
  }, [load]);

  const productionOrder = useMemo(() => {
    const order = ['LITHOGRAPHY', 'ETCH', 'DEPOSITION', 'INSPECTION'];
    return order.map(process => wafers.filter(w => w.current_process === process && ['READY', 'PROCESSING', 'HOLD'].includes(w.status)).length);
  }, [wafers]);

  const fabFlow = useMemo(() => {
    const order = ['LITHOGRAPHY', 'ETCH', 'DEPOSITION', 'INSPECTION'];
    return order.map(process => {
      const station = equipment.find(eq => eq.process_type === process);
      return {
        process,
        equipmentCode: station?.equipment_code ?? '—',
        name: station?.name ?? process,
        status: station?.status ?? 'IDLE',
        wafer: station?.current_wafer_id ?? '—',
      };
    });
  }, [equipment]);

  const inspect = async (code: string) => {
    setSelectedCode(code);
    try {
      const detail = await fetch(`${API_URL}/wafers/${code}`, { cache: 'no-store' }).then(r => r.json());
      setSelected(detail);
    } catch {
      setSelected(null);
    }
  };

  const reset = async () => {
    await fetch(`${API_URL}/simulation/reset`, { method: 'POST' });
    window.location.reload();
  };

  return (
    <main className="page-shell">
      <header className="header">
        <div>
          <p className="eyebrow">MINIFAB • MES MVP</p>
          <h1>Semiconductor Fab Control Center</h1>
          <p className="subtitle">Synthetic production environment: lithography → etch → deposition → inspection.</p>
        </div>
        <div className="header-actions">
          <span className="live-dot" /> Live polling · {lastUpdated ? lastUpdated.toLocaleTimeString() : 'connecting…'}
          <button className="reset-button" onClick={reset}>Reset simulation</button>
        </div>
      </header>

      {error && <div className="error-banner">API connection problem: {error}. Verify the stack with <code>docker compose ps</code> and try <code>/api/health</code>.</div>}

      <section className="metric-grid">
        <Metric label="Total wafers" value={dashboard?.stats.total_wafers ?? '—'} />
        <Metric label="In process / hold" value={dashboard?.stats.in_process ?? '—'} />
        <Metric label="Completed" value={dashboard?.stats.completed ?? '—'} />
        <Metric label="Scrapped" value={dashboard?.stats.scrapped ?? '—'} />
        <Metric label="Inspection yield" value={dashboard ? `${dashboard.yield_pct}%` : '—'} />
      </section>

      <section className="panel fab-visual-panel">
        <div className="panel-title"><h2>Fab floor view</h2><span>live process flow</span></div>
        <div className="fab-flow">
          {fabFlow.map((station, index) => (
            <div className="fab-station-wrap" key={station.process}>
              <div className={`fab-station ${station.status.toLowerCase()}`}>
                <div className="station-header">
                  <span className="station-label">{station.process}</span>
                  <span className={`station-status ${station.status.toLowerCase()}`}>{station.status}</span>
                </div>
                <strong>{station.equipmentCode}</strong>
                <span>{station.name}</span>
                <div className="station-wafer">{station.wafer === '—' ? 'Awaiting wafer' : station.wafer}</div>
              </div>
              {index < fabFlow.length - 1 && <div className="fab-arrow">→</div>}
            </div>
          ))}
        </div>
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
                <span className={statusClass(eq.status)}>{eq.status}</span>
              </div>
            ))}
          </div>
          <div className="mini-note">The simulator deliberately takes ETCH-01 offline after ~35 seconds and repairs it ~15 seconds later.</div>
        </article>

        <article className="panel">
          <div className="panel-title"><h2>WIP by process</h2><span>ready + processing + hold</span></div>
          <div className="process-bars">
            {['Lithography', 'Etch', 'Deposition', 'Inspection'].map((p, i) => {
              const count = productionOrder[i];
              const max = Math.max(1, ...productionOrder);
              return <div className="bar-row" key={p}><span>{p}</span><div className="bar-track"><div className="bar-fill" style={{ width: `${(count / max) * 100}%` }} /></div><b>{count}</b></div>;
            })}
          </div>
        </article>
      </section>

      <section className="grid-two">
        <article className="panel">
          <div className="panel-title"><h2>Recent events</h2><span>event-driven history</span></div>
          <div className="event-list">
            {(dashboard?.events || []).map(evt => (
              <div className="event-row" key={evt.event_id}>
                <time>{fmtDate(evt.occurred_at)}</time>
                <div><strong>{evt.event_type}</strong><span>{[evt.wafer_code, evt.equipment_code].filter(Boolean).join(' · ') || 'Factory event'}</span></div>
              </div>
            ))}
          </div>
        </article>

        <article className="panel">
          <div className="panel-title"><h2>Production flow</h2><span>current wafer state</span></div>
          <div className="flow">
            {['LITHOGRAPHY', 'ETCH', 'DEPOSITION', 'INSPECTION'].map((p, i) => <div className="flow-step" key={p}><div className="flow-index">{i + 1}</div><span>{p}</span>{i < 3 && <span className="flow-arrow">→</span>}</div>)}
          </div>
          <div className="mini-note">Click any wafer below to inspect its process genealogy and quality records.</div>
        </article>
      </section>

      <section className="panel wafer-panel">
        <div className="panel-title"><h2>Wafer queue</h2><span>{wafers.length} visible</span></div>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Wafer</th><th>Lot</th><th>Process</th><th>Status</th><th>Updated</th><th /></tr></thead>
            <tbody>
              {wafers.map(w => <tr key={w.wafer_code}>
                <td><button className="link-button" onClick={() => inspect(w.wafer_code)}>{w.wafer_code}</button></td>
                <td>{w.lot_number}</td>
                <td>{w.current_process}</td>
                <td><span className={statusClass(w.status)}>{w.status}</span></td>
                <td>{fmtDate(w.updated_at)}</td>
                <td><button className="inspect-button" onClick={() => inspect(w.wafer_code)}>Inspect</button></td>
              </tr>)}
            </tbody>
          </table>
        </div>
      </section>

      {selected && <div className="drawer-backdrop" onClick={() => { setSelected(null); setSelectedCode(null); }}>
        <aside className="drawer" onClick={e => e.stopPropagation()}>
          <button className="close" onClick={() => { setSelected(null); setSelectedCode(null); }}>×</button>
          <p className="eyebrow">WAFER TRACEABILITY</p>
          <h2>{selected.wafer.wafer_code}</h2>
          <p className="drawer-subtitle">{selected.wafer.product_name} · {selected.wafer.lot_number} · {selected.wafer.status}</p>
          <h3>Process history</h3>
          <div className="timeline">
            {selected.process_runs.map(run => <div className="timeline-item" key={run.id}>
              <div className="timeline-dot" />
              <div><strong>{run.process_type}</strong><span>{run.equipment_code} · {run.recipe_code} v{run.version}</span><span>{fmtDate(run.started_at)} → {fmtDate(run.completed_at)}</span><span>Result: {run.result || run.status}</span></div>
            </div>)}
          </div>
          <h3>Quality results</h3>
          {selected.quality_results.length === 0 ? <p className="muted">No inspection result recorded yet.</p> : selected.quality_results.map((q, i) => <div className="quality-card" key={i}><strong>{q.metric}</strong><span>Expected {q.expected} ± {q.tolerance}</span><span>Measured {q.measured}</span><span className={statusClass(q.result)}>{q.result}</span></div>)}
        </aside>
      </div>}
    </main>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return <article className="metric"><span>{label}</span><strong>{value}</strong></article>;
}
