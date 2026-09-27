import json
import os
import random
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
import requests
from sqlalchemy import create_engine, text

API_URL = os.getenv("API_URL", "http://localhost:8000")
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
TELEMETRY_INTERVAL = float(os.getenv("TELEMETRY_INTERVAL", "1"))
FAILURE_AFTER_SECONDS = float(os.getenv("FAILURE_AFTER_SECONDS", "35"))
FAILURE_DURATION_SECONDS = float(os.getenv("FAILURE_DURATION_SECONDS", "15"))
RESET_ON_START = os.getenv("RESET_ON_START", "true").lower() == "true"
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://fab:fab@localhost:5432/fab")

PROCESS_ORDER = ["LITHOGRAPHY", "ETCH", "DEPOSITION", "INSPECTION"]
EQUIPMENT = {
    "LITHOGRAPHY": ("LITHO-01", "LITHO-V1"),
    "ETCH": ("ETCH-01", "ETCH-V2"),
    "DEPOSITION": ("DEP-01", "DEP-V1"),
    "INSPECTION": ("METRO-01", "METRO-V1"),
}
DURATIONS = {"LITHOGRAPHY": 5.0, "ETCH": 6.0, "DEPOSITION": 7.0, "INSPECTION": 3.0}
FAILURE_RATES = {"LITHOGRAPHY": 0.02, "ETCH": 0.03, "DEPOSITION": 0.02, "INSPECTION": 0.06}

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
random.seed(9026)


@dataclass
class Job:
    wafer_code: str
    process_type: str
    equipment_code: str
    recipe_code: str
    remaining: float
    started_at: float
    paused: bool = False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def event(event_type: str, **kwargs) -> dict:
    return {
        "event_id": f"evt-{uuid.uuid4().hex}",
        "event_type": event_type,
        "timestamp": now_iso(),
        **kwargs,
    }


def wait_for_api():
    for _ in range(60):
        try:
            r = requests.get(f"{API_URL}/health", timeout=2)
            if r.ok:
                return
        except requests.RequestException:
            pass
        time.sleep(2)
    raise RuntimeError("API never became healthy")


def reset():
    if RESET_ON_START:
        r = requests.post(f"{API_URL}/simulation/reset", timeout=10)
        r.raise_for_status()
        print(f"[sim] reset: {r.json()}")


def get_ready_wafers() -> list[dict]:
    try:
        r = requests.get(f"{API_URL}/wafers?limit=100&status=READY", timeout=3)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as exc:
        print(f"[sim] wafer fetch failed: {exc}")
        return []


def publish(client: mqtt.Client, topic: str, payload: dict):
    client.publish(topic, json.dumps(payload), qos=1)


def make_telemetry(job: Job, remaining: float) -> dict:
    p = job.process_type
    # Synthetic but process-specific ranges; these are intentionally not physical models.
    base_temp = {"LITHOGRAPHY": 58, "ETCH": 72, "DEPOSITION": 68, "INSPECTION": 24}[p]
    temp = base_temp + random.uniform(-2.0, 2.0)
    pressure = {"LITHOGRAPHY": 1.0, "ETCH": 18.0, "DEPOSITION": 12.0, "INSPECTION": 0.8}[p] + random.uniform(-0.4, 0.4)
    rf_power = {"LITHOGRAPHY": 420, "ETCH": 850, "DEPOSITION": 640, "INSPECTION": 25}[p] + random.uniform(-12, 12)
    # The deliberate ETCH failure causes telemetry to trend upward immediately before alarm.
    if p == "ETCH" and failure_triggered and not failure_repaired:
        temp += min(14, max(0, failure_elapsed / max(1, FAILURE_DURATION_SECONDS) * 14))
        pressure += failure_elapsed / max(1, FAILURE_DURATION_SECONDS) * 5
    return {
        "equipment_code": job.equipment_code,
        "wafer_code": job.wafer_code,
        "timestamp": now_iso(),
        "temperature_c": round(temp, 2),
        "pressure_mtorr": round(pressure, 2),
        "rf_power_w": round(rf_power, 1),
        "status": "RUNNING" if not job.paused else "HOLD",
        "runtime_increment_seconds": TELEMETRY_INTERVAL if not job.paused else 0,
        "remaining_seconds": round(max(0, remaining), 2),
        "process_type": p,
    }


def make_quality(wafer_code: str, fail: bool) -> dict:
    expected = 45.0
    tolerance = 0.5
    measured = expected + (random.uniform(0.9, 1.8) if fail else random.uniform(-0.35, 0.35))
    return event(
        "QualityResult",
        wafer_code=wafer_code,
        equipment_code="METRO-01",
        metric="Critical Dimension (nm)",
        expected=expected,
        measured=round(measured, 3),
        tolerance=tolerance,
        result="FAIL" if fail else "PASS",
    )


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="mini-fab-simulator")
connected = False

def on_connect(c, userdata, flags, reason_code, properties=None):
    global connected
    connected = True
    print(f"[sim] MQTT connected rc={reason_code}")

client.on_connect = on_connect


def run():
    global failure_triggered, failure_repaired, failure_elapsed
    failure_triggered = False
    failure_repaired = False
    failure_elapsed = 0.0

    wait_for_api()
    reset()
    client.connect(MQTT_HOST, MQTT_PORT, 60)
    client.loop_start()

    jobs: dict[str, Job] = {}
    machine_down_until: dict[str, float] = {}
    sim_start = time.monotonic()
    last_tick = sim_start
    last_telemetry = sim_start
    failure_event_sent = False

    print("[sim] MiniFab running. Watch http://localhost:3000")
    while True:
        current = time.monotonic()
        dt = min(1.5, current - last_tick)
        last_tick = current
        elapsed_total = current - sim_start

        # Deliberate equipment failure: ETCH-01 goes down once after the configured time.
        if not failure_triggered and elapsed_total >= FAILURE_AFTER_SECONDS:
            failure_triggered = True
            failure_event_sent = False
            machine_down_until["ETCH-01"] = current + FAILURE_DURATION_SECONDS
            active = next((j for j in jobs.values() if j.equipment_code == "ETCH-01"), None)
            if active:
                active.paused = True
                publish(client, "fab/events", event("WaferHold", wafer_code=active.wafer_code, equipment_code="ETCH-01", reason="ETCH-01 alarm / maintenance"))
            publish(client, "fab/events", event("EquipmentDown", equipment_code="ETCH-01", reason="Pressure control alarm"))
            print("[sim] DELIBERATE FAILURE: ETCH-01 DOWN")

        if failure_triggered and not failure_repaired:
            failure_elapsed = max(0.0, FAILURE_DURATION_SECONDS - (machine_down_until.get("ETCH-01", current) - current))
            if current >= machine_down_until.get("ETCH-01", current + 1):
                failure_repaired = True
                publish(client, "fab/events", event("EquipmentRepaired", equipment_code="ETCH-01", reason="Maintenance completed"))
                active = next((j for j in jobs.values() if j.equipment_code == "ETCH-01"), None)
                if active:
                    active.paused = False
                    publish(client, "fab/events", event("WaferReleased", wafer_code=active.wafer_code, equipment_code="ETCH-01", reason="Equipment restored"))
                print("[sim] ETCH-01 repaired; processing resumes")

        # Find available READY wafers and start work on each free station.
        ready = {w["wafer_code"]: w for w in get_ready_wafers()}
        for process_type, (equipment_code, recipe_code) in EQUIPMENT.items():
            if equipment_code in machine_down_until and current < machine_down_until[equipment_code]:
                continue
            active = next((j for j in jobs.values() if j.equipment_code == equipment_code), None)
            if active:
                continue
            candidates = [w for w in ready.values() if w["current_process"] == process_type]
            if candidates:
                w = sorted(candidates, key=lambda x: x["wafer_code"])[0]
                job = Job(w["wafer_code"], process_type, equipment_code, recipe_code, DURATIONS[process_type], current)
                jobs[job.wafer_code] = job
                publish(client, "fab/events", event(
                    "ProcessStarted",
                    wafer_code=job.wafer_code,
                    equipment_code=equipment_code,
                    process_type=process_type,
                    recipe_code=recipe_code,
                ))
                print(f"[sim] start {job.wafer_code} -> {process_type}")

        # Progress active jobs.
        for wafer_code, job in list(jobs.items()):
            if job.paused:
                continue
            job.remaining -= dt
            if job.remaining <= 0:
                fail = random.random() < FAILURE_RATES[job.process_type]
                idx = PROCESS_ORDER.index(job.process_type)
                next_process = PROCESS_ORDER[idx + 1] if idx + 1 < len(PROCESS_ORDER) else None
                publish(client, "fab/events", event(
                    "ProcessCompleted",
                    wafer_code=job.wafer_code,
                    equipment_code=job.equipment_code,
                    process_type=job.process_type,
                    recipe_code=job.recipe_code,
                    result="FAIL" if (job.process_type == "INSPECTION" and fail) else "PASS",
                    next_process=next_process,
                    metrics={"cycle_seconds": DURATIONS[job.process_type], "synthetic_failure": bool(fail)},
                ))
                if job.process_type == "INSPECTION":
                    publish(client, "fab/events", make_quality(job.wafer_code, fail))
                print(f"[sim] complete {job.wafer_code} -> {job.process_type} {'FAIL' if fail and job.process_type == 'INSPECTION' else 'PASS'}")
                del jobs[wafer_code]

        # Periodic telemetry.
        if current - last_telemetry >= TELEMETRY_INTERVAL:
            last_telemetry = current
            for job in jobs.values():
                publish(client, f"fab/telemetry/{job.equipment_code}", make_telemetry(job, job.remaining))

        time.sleep(0.2)


if __name__ == "__main__":
    run()
