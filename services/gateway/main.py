import asyncio
import json
import os
import threading
import time
from datetime import datetime, timezone

from asyncua import Client
import paho.mqtt.client as mqtt
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://fab:fab@localhost:5432/fab")
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
OPCUA_URL = os.getenv("OPCUA_URL", "opc.tcp://localhost:4840/minifab/server/")
OPCUA_NAMESPACE_URI = os.getenv("OPCUA_NAMESPACE_URI", "urn:minifab:equipment")
OPCUA_SAMPLE_INTERVAL = float(os.getenv("OPCUA_SAMPLE_INTERVAL", "0.5"))
EQUIPMENT_CODES = ("LITHO-01", "ETCH-01", "DEP-01", "METRO-01")
OPCUA_FIELDS = (
    "TemperatureC", "PressureMTorr", "RFPowerW", "MachineState",
    "CurrentWaferCode", "RemainingSeconds", "ProcessType", "RuntimeIncrementSeconds",
    "Timestamp", "AlarmCode", "LastEvent",
)
engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def iso_to_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def insert_event(conn, payload: dict) -> bool:
    event_id = payload.get("event_id")
    if not event_id:
        raise ValueError("event_id is required")
    try:
        conn.execute(text("""INSERT INTO events (event_id,event_type,wafer_code,equipment_code,occurred_at,payload)
                            VALUES (:eid,:etype,:wafer,:equipment,:ts,CAST(:payload AS jsonb))"""), {
            "eid": event_id,
            "etype": payload.get("event_type", "Unknown"),
            "wafer": payload.get("wafer_code"),
            "equipment": payload.get("equipment_code"),
            "ts": iso_to_dt(payload.get("timestamp")),
            "payload": json.dumps(payload),
        })
        return True
    except Exception as exc:
        if "duplicate key" in str(exc).lower():
            conn.rollback()
            return False
        raise


def process_event(payload: dict) -> None:
    event_type = payload.get("event_type")
    wafer_code = payload.get("wafer_code")
    equipment_code = payload.get("equipment_code")
    with engine.begin() as conn:
        # Store events idempotently. Duplicate event IDs are ignored.
        try:
            conn.execute(text("""INSERT INTO events (event_id,event_type,wafer_code,equipment_code,occurred_at,payload)
                                VALUES (:eid,:etype,:wafer,:equipment,:ts,CAST(:payload AS jsonb))
                                ON CONFLICT (event_id) DO NOTHING"""), {
                "eid": payload["event_id"], "etype": event_type, "wafer": wafer_code,
                "equipment": equipment_code, "ts": iso_to_dt(payload.get("timestamp")),
                "payload": json.dumps(payload),
            })
        except Exception:
            return

        if equipment_code:
            if event_type == "EquipmentDown":
                conn.execute(text("UPDATE equipment SET status='DOWN', last_event_at=:ts WHERE equipment_code=:code"), {"code": equipment_code, "ts": iso_to_dt(payload.get("timestamp"))})
            elif event_type == "EquipmentRepaired":
                conn.execute(text("UPDATE equipment SET status='IDLE', last_event_at=:ts WHERE equipment_code=:code"), {"code": equipment_code, "ts": iso_to_dt(payload.get("timestamp"))})
            elif event_type == "ProcessStarted":
                conn.execute(text("UPDATE equipment SET status='RUNNING', current_wafer_id=:wafer, last_event_at=:ts WHERE equipment_code=:code"), {"code": equipment_code, "wafer": wafer_code, "ts": iso_to_dt(payload.get("timestamp"))})
            elif event_type == "ProcessCompleted":
                conn.execute(text("UPDATE equipment SET status='IDLE', current_wafer_id=NULL, last_event_at=:ts WHERE equipment_code=:code"), {"code": equipment_code, "ts": iso_to_dt(payload.get("timestamp"))})

        if event_type == "ProcessStarted" and wafer_code:
            wafer = conn.execute(text("SELECT id FROM wafers WHERE wafer_code=:code"), {"code": wafer_code}).scalar()
            eq = conn.execute(text("SELECT id FROM equipment WHERE equipment_code=:code"), {"code": equipment_code}).scalar()
            recipe = conn.execute(text("SELECT id FROM recipes WHERE recipe_code=:code"), {"code": payload.get("recipe_code")}).scalar()
            if wafer and eq and recipe:
                conn.execute(text("UPDATE wafers SET status='PROCESSING', current_process=:proc, updated_at=:ts WHERE id=:id"), {"id": wafer, "proc": payload["process_type"], "ts": iso_to_dt(payload.get("timestamp"))})
                conn.execute(text("""INSERT INTO process_runs (wafer_id,process_type,equipment_id,recipe_id,started_at,status)
                                    VALUES (:wafer,:proc,:eq,:recipe,:ts,'RUNNING')"""), {
                    "wafer": wafer, "proc": payload["process_type"], "eq": eq, "recipe": recipe, "ts": iso_to_dt(payload.get("timestamp")),
                })

        elif event_type == "ProcessCompleted" and wafer_code:
            run_id = conn.execute(text("""SELECT pr.id FROM process_runs pr JOIN wafers w ON w.id=pr.wafer_id
                                           WHERE w.wafer_code=:code AND pr.status='RUNNING' ORDER BY pr.started_at DESC LIMIT 1"""), {"code": wafer_code}).scalar()
            if run_id:
                result = payload.get("result", "PASS")
                conn.execute(text("UPDATE process_runs SET completed_at=:ts,status='COMPLETED',result=:result,metrics=CAST(:metrics AS jsonb) WHERE id=:id"), {
                    "id": run_id, "ts": iso_to_dt(payload.get("timestamp")), "result": result, "metrics": json.dumps(payload.get("metrics", {})),
                })
            if payload.get("process_type") == "INSPECTION" and payload.get("result") == "FAIL":
                conn.execute(text("UPDATE wafers SET status='SCRAPPED', updated_at=:ts WHERE wafer_code=:code"), {"code": wafer_code, "ts": iso_to_dt(payload.get("timestamp"))})
            elif payload.get("next_process"):
                conn.execute(text("UPDATE wafers SET status='READY', current_process=:proc, process_index=process_index+1, updated_at=:ts WHERE wafer_code=:code"), {"code": wafer_code, "proc": payload["next_process"], "ts": iso_to_dt(payload.get("timestamp"))})
            else:
                conn.execute(text("UPDATE wafers SET status='COMPLETED', updated_at=:ts WHERE wafer_code=:code"), {"code": wafer_code, "ts": iso_to_dt(payload.get("timestamp"))})

        elif event_type == "WaferHold" and wafer_code:
            conn.execute(text("UPDATE wafers SET status='HOLD', updated_at=:ts WHERE wafer_code=:code"), {"code": wafer_code, "ts": iso_to_dt(payload.get("timestamp"))})
        elif event_type == "WaferReleased" and wafer_code:
            conn.execute(text("UPDATE wafers SET status='PROCESSING', updated_at=:ts WHERE wafer_code=:code"), {"code": wafer_code, "ts": iso_to_dt(payload.get("timestamp"))})

        if event_type == "QualityResult" and wafer_code:
            wafer = conn.execute(text("SELECT id FROM wafers WHERE wafer_code=:code"), {"code": wafer_code}).scalar()
            run_id = conn.execute(text("""SELECT pr.id FROM process_runs pr JOIN wafers w ON w.id=pr.wafer_id
                                           WHERE w.wafer_code=:code ORDER BY pr.started_at DESC LIMIT 1"""), {"code": wafer_code}).scalar()
            if wafer:
                conn.execute(text("""INSERT INTO quality_results (wafer_id,process_run_id,metric,expected,measured,tolerance,result,recorded_at)
                                    VALUES (:wafer,:run,:metric,:expected,:measured,:tolerance,:result,:ts)"""), {
                    "wafer": wafer, "run": run_id, "metric": payload["metric"], "expected": payload["expected"],
                    "measured": payload["measured"], "tolerance": payload["tolerance"], "result": payload["result"],
                    "ts": iso_to_dt(payload.get("timestamp")),
                })


def on_connect(client, userdata, flags, reason_code, properties=None):
    print(f"[gateway] connected rc={reason_code}")
    client.subscribe("fab/telemetry/#", qos=1)
    client.subscribe("fab/events", qos=1)


def on_message(client, userdata, msg):
    try:
        payload = json.loads(msg.payload.decode("utf-8"))
        if msg.topic.startswith("fab/telemetry/"):
            equipment_code = payload["equipment_code"]
            with engine.begin() as conn:
                eq_id = conn.execute(text("SELECT id FROM equipment WHERE equipment_code=:code"), {"code": equipment_code}).scalar()
                if eq_id:
                    conn.execute(text("""INSERT INTO telemetry (equipment_id,wafer_code,recorded_at,temperature_c,pressure_mtorr,rf_power_w,status,payload)
                                        VALUES (:eq,:wafer,:ts,:temp,:pressure,:power,:status,CAST(:payload AS jsonb))"""), {
                        "eq": eq_id, "wafer": payload.get("wafer_code"), "ts": iso_to_dt(payload.get("timestamp")),
                        "temp": payload.get("temperature_c"), "pressure": payload.get("pressure_mtorr"), "power": payload.get("rf_power_w"),
                        "status": payload.get("status"), "payload": json.dumps(payload),
                    })
                    conn.execute(text("UPDATE equipment SET total_runtime_seconds=total_runtime_seconds+:inc, utilization_seconds=utilization_seconds+:inc, last_event_at=:ts WHERE id=:id"), {
                        "id": eq_id, "inc": payload.get("runtime_increment_seconds", 0), "ts": iso_to_dt(payload.get("timestamp"))
                    })
        elif msg.topic == "fab/events":
            process_event(payload)
    except Exception as exc:
        print(f"[gateway] message error: {exc}")


async def read_opcua(client: mqtt.Client) -> None:
    while True:
        try:
            async with Client(url=OPCUA_URL, timeout=5) as opcua:
                namespace = await opcua.get_namespace_index(OPCUA_NAMESPACE_URI)
                root = await opcua.nodes.objects.get_child([f"{namespace}:Equipment"])
                nodes = {}
                for equipment_code in EQUIPMENT_CODES:
                    machine = await root.get_child([f"{namespace}:{equipment_code}"])
                    nodes[equipment_code] = {
                        field: await machine.get_child([f"{namespace}:{field}"])
                        for field in OPCUA_FIELDS
                    }
                print(f"[gateway] OPC-UA connected; monitoring {len(nodes)} equipment assets", flush=True)
                previous = {}
                last_published = {}
                while True:
                    for equipment_code, equipment_nodes in nodes.items():
                        values = await opcua.read_values(list(equipment_nodes.values()))
                        snapshot = dict(zip(OPCUA_FIELDS, values))
                        if snapshot == previous.get(equipment_code):
                            continue
                        previous[equipment_code] = snapshot.copy()
                        source_timestamp = snapshot["Timestamp"]
                        collected_at = time.monotonic()
                        elapsed = max(0.0, collected_at - last_published.get(equipment_code, collected_at))
                        last_published[equipment_code] = collected_at
                        payload = {
                            "equipment_code": equipment_code,
                            "wafer_code": snapshot["CurrentWaferCode"] or None,
                            "timestamp": source_timestamp,
                            "temperature_c": snapshot["TemperatureC"],
                            "pressure_mtorr": snapshot["PressureMTorr"],
                            "rf_power_w": snapshot["RFPowerW"],
                            "status": snapshot["MachineState"],
                            "process_type": snapshot["ProcessType"],
                            "remaining_seconds": snapshot["RemainingSeconds"],
                            "runtime_increment_seconds": elapsed if snapshot["MachineState"] == "RUNNING" else 0.0,
                            "alarm_code": snapshot["AlarmCode"],
                            "last_event": snapshot["LastEvent"],
                            "source": "opcua",
                        }
                        client.publish(
                            f"fab/telemetry/normalized/{equipment_code}",
                            json.dumps(payload),
                            qos=1,
                        )
                    await asyncio.sleep(OPCUA_SAMPLE_INTERVAL)
        except Exception as exc:
            print(f"[gateway] OPC-UA connection/read failed: {exc}; retrying in 3 seconds", flush=True)
            await asyncio.sleep(3)


def start_opcua_reader(client: mqtt.Client) -> None:
    asyncio.run(read_opcua(client))


def main():
    while True:
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="mini-fab-gateway")
            client.on_connect = on_connect
            client.on_message = on_message
            client.connect(MQTT_HOST, MQTT_PORT, 60)
            client.loop_start()
            threading.Thread(target=start_opcua_reader, args=(client,), name="opcua-reader", daemon=True).start()
            while True:
                time.sleep(3600)
        except Exception as exc:
            print(f"[gateway] reconnecting after error: {exc}")
            time.sleep(3)


if __name__ == "__main__":
    main()
