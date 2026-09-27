import os
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://fab:fab@localhost:5432/fab")
DEMO_WAFERS = int(os.getenv("DEMO_WAFERS", "50"))
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

app = FastAPI(title="MiniFab MES API", version="0.1.0", description="MES-style API for a simulated semiconductor fab")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ResetResponse(BaseModel):
    wafers: int
    message: str


def wait_for_db() -> None:
    for _ in range(30):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return
        except OperationalError:
            import time
            time.sleep(2)
    raise RuntimeError("Database was not available after retries")


def seed_demo() -> None:
    wait_for_db()
    with engine.begin() as conn:
        product = conn.execute(text("SELECT id FROM products WHERE part_number = 'AI-ACCEL-V1'")).first()
        if not product:
            product_id = conn.execute(
                text("INSERT INTO products (name, part_number) VALUES (:name, :part) RETURNING id"),
                {"name": "VX-900 AI Accelerator", "part": "AI-ACCEL-V1"},
            ).scalar_one()
        else:
            product_id = product.id

        lot_ids = []
        for lot_num in range(1, 6):
            lot = conn.execute(text("SELECT id FROM lots WHERE lot_number=:lot"), {"lot": f"LOT-{lot_num:04d}"}).first()
            if lot:
                lot_ids.append(lot.id)
            else:
                lot_ids.append(conn.execute(
                    text("INSERT INTO lots (lot_number, product_id) VALUES (:lot, :pid) RETURNING id"),
                    {"lot": f"LOT-{lot_num:04d}", "pid": product_id},
                ).scalar_one())

        conn.execute(text("DELETE FROM work_orders"))
        conn.execute(
            text("INSERT INTO work_orders (work_order_number, product_id, quantity, status) VALUES ('WO-2026-0001', :pid, :qty, 'RELEASED') ON CONFLICT (work_order_number) DO NOTHING"),
            {"pid": product_id, "qty": DEMO_WAFERS},
        )

        equipment = [
            ("LITHO-01", "Lithography Cell 01", "LITHOGRAPHY"),
            ("ETCH-01", "Plasma Etch Cell 01", "ETCH"),
            ("DEP-01", "Deposition Cell 01", "DEPOSITION"),
            ("METRO-01", "Metrology / Inspection 01", "INSPECTION"),
        ]
        for code, name, process in equipment:
            conn.execute(
                text("""INSERT INTO equipment (equipment_code, name, process_type, status)
                       VALUES (:code, :name, :process, 'IDLE')
                       ON CONFLICT (equipment_code) DO UPDATE SET name=EXCLUDED.name, process_type=EXCLUDED.process_type"""),
                {"code": code, "name": name, "process": process},
            )

        recipes = [
            ("LITHO-V1", "Standard Lithography", "1.0", "LITHOGRAPHY", 5, {"dose_mj_cm2": 24.0, "focus_um": 0.03}),
            ("ETCH-V2", "Silicon Plasma Etch", "2.0", "ETCH", 6, {"pressure_mtorr": 18.0, "rf_power_w": 850.0}),
            ("DEP-V1", "Thin Film Deposition", "1.0", "DEPOSITION", 7, {"temperature_c": 72.0, "film_nm": 120.0}),
            ("METRO-V1", "Critical Dimension Inspection", "1.0", "INSPECTION", 3, {"target_nm": 45.0, "tolerance_nm": 0.5}),
        ]
        for code, name, version, process, duration, params in recipes:
            conn.execute(
                text("""INSERT INTO recipes (recipe_code, name, version, process_type, nominal_duration_seconds, parameters)
                       VALUES (:code, :name, :version, :process, :duration, CAST(:params AS jsonb))
                       ON CONFLICT (recipe_code) DO UPDATE SET nominal_duration_seconds=EXCLUDED.nominal_duration_seconds,
                         parameters=EXCLUDED.parameters"""),
                {"code": code, "name": name, "version": version, "process": process, "duration": duration, "params": __import__('json').dumps(params)},
            )

        existing = conn.execute(text("SELECT COUNT(*) FROM wafers")).scalar_one()
        if existing == 0:
            for i in range(DEMO_WAFERS):
                lot_id = lot_ids[i % len(lot_ids)]
                conn.execute(
                    text("""INSERT INTO wafers (wafer_code, lot_id, status, current_process, process_index)
                           VALUES (:code, :lot, 'READY', 'LITHOGRAPHY', 0) ON CONFLICT DO NOTHING"""),
                    {"code": f"WAFER-{i+1:05d}", "lot": lot_id},
                )


@app.on_event("startup")
def startup_event() -> None:
    seed_demo()


@app.get("/health")
def health() -> dict[str, str]:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/dashboard")
def dashboard() -> dict[str, Any]:
    with engine.connect() as conn:
        stats = conn.execute(text("""
            SELECT
              COUNT(*) AS total_wafers,
              COUNT(*) FILTER (WHERE status IN ('PROCESSING','HOLD')) AS in_process,
              COUNT(*) FILTER (WHERE status = 'COMPLETED') AS completed,
              COUNT(*) FILTER (WHERE status = 'SCRAPPED') AS scrapped
            FROM wafers
        """)).mappings().one()
        yield_row = conn.execute(text("""
            SELECT COALESCE(ROUND(100.0 * COUNT(*) FILTER (WHERE result='PASS') / NULLIF(COUNT(*),0), 1), 0) AS yield_pct
            FROM quality_results
        """)).mappings().one()
        events = conn.execute(text("""
            SELECT event_id, event_type, wafer_code, equipment_code, occurred_at, payload
            FROM events ORDER BY occurred_at DESC LIMIT 12
        """)).mappings().all()
    return {"stats": dict(stats), "yield_pct": yield_row["yield_pct"], "events": [dict(e) for e in events]}


@app.get("/equipment")
def equipment() -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT equipment_code, name, process_type, status, current_wafer_id,
                   ROUND(utilization_seconds::numeric, 1) AS utilization_seconds,
                   ROUND(total_runtime_seconds::numeric, 1) AS total_runtime_seconds,
                   last_event_at
            FROM equipment ORDER BY equipment_code
        """)).mappings().all()
    return [dict(r) for r in rows]


@app.get("/wafers")
def wafers(limit: int = Query(50, ge=1, le=200), status: str | None = None) -> list[dict[str, Any]]:
    query = """
        SELECT w.wafer_code, l.lot_number, p.part_number, w.status, w.current_process,
               w.process_index, w.created_at, w.updated_at
        FROM wafers w
        JOIN lots l ON l.id=w.lot_id
        JOIN products p ON p.id=l.product_id
    """
    params: dict[str, Any] = {"limit": limit}
    if status:
        query += " WHERE w.status=:status"
        params["status"] = status
    query += " ORDER BY w.updated_at DESC LIMIT :limit"
    with engine.connect() as conn:
        rows = conn.execute(text(query), params).mappings().all()
    return [dict(r) for r in rows]


@app.get("/wafers/{wafer_code}")
def wafer_detail(wafer_code: str) -> dict[str, Any]:
    with engine.connect() as conn:
        wafer = conn.execute(text("""
            SELECT w.wafer_code, l.lot_number, p.part_number, p.name AS product_name,
                   w.status, w.current_process, w.process_index, w.created_at, w.updated_at
            FROM wafers w JOIN lots l ON l.id=w.lot_id JOIN products p ON p.id=l.product_id
            WHERE w.wafer_code=:code
        """), {"code": wafer_code}).mappings().first()
        if not wafer:
            raise HTTPException(status_code=404, detail="Wafer not found")
        runs = conn.execute(text("""
            SELECT pr.id, pr.process_type, e.equipment_code, r.recipe_code, r.version,
                   pr.started_at, pr.completed_at, pr.status, pr.result, pr.metrics
            FROM process_runs pr
            JOIN equipment e ON e.id=pr.equipment_id
            JOIN recipes r ON r.id=pr.recipe_id
            JOIN wafers w ON w.id=pr.wafer_id
            WHERE w.wafer_code=:code ORDER BY pr.started_at
        """), {"code": wafer_code}).mappings().all()
        quality = conn.execute(text("""
            SELECT qr.metric, qr.expected, qr.measured, qr.tolerance, qr.result, qr.recorded_at
            FROM quality_results qr JOIN wafers w ON w.id=qr.wafer_id
            WHERE w.wafer_code=:code ORDER BY qr.recorded_at
        """), {"code": wafer_code}).mappings().all()
    return {"wafer": dict(wafer), "process_runs": [dict(r) for r in runs], "quality_results": [dict(r) for r in quality]}


@app.get("/events")
def events(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT event_id, event_type, wafer_code, equipment_code, occurred_at, payload
            FROM events ORDER BY occurred_at DESC LIMIT :limit
        """), {"limit": limit}).mappings().all()
    return [dict(r) for r in rows]


@app.get("/production/summary")
def production_summary() -> dict[str, Any]:
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT process_type,
                   COUNT(*) AS runs,
                   COUNT(*) FILTER (WHERE result='PASS') AS passed,
                   COUNT(*) FILTER (WHERE result='FAIL') AS failed,
                   ROUND(AVG(EXTRACT(EPOCH FROM (completed_at-started_at)))::numeric,2) AS avg_cycle_seconds
            FROM process_runs WHERE completed_at IS NOT NULL GROUP BY process_type ORDER BY process_type
        """)).mappings().all()
    return [dict(r) for r in rows]


@app.post("/simulation/reset", response_model=ResetResponse)
def reset_simulation() -> ResetResponse:
    wait_for_db()
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE quality_results, process_runs, telemetry, events, wafers RESTART IDENTITY CASCADE"))
        lots = conn.execute(text("SELECT id FROM lots ORDER BY id")).scalars().all()
        for i in range(DEMO_WAFERS):
            lot_id = lots[i % len(lots)]
            conn.execute(
                text("INSERT INTO wafers (wafer_code, lot_id, status, current_process, process_index) VALUES (:code,:lot,'READY','LITHOGRAPHY',0)"),
                {"code": f"WAFER-{i+1:05d}", "lot": lot_id},
            )
        conn.execute(text("UPDATE equipment SET status='IDLE', current_wafer_id=NULL, utilization_seconds=0, total_runtime_seconds=0, last_event_at=NULL"))
    return ResetResponse(wafers=DEMO_WAFERS, message="Simulation data reset. Restart simulator container to begin cleanly.")
