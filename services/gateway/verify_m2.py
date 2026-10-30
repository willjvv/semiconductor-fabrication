"""Smoke-test Milestone 2 from inside the gateway container.

Run after starting the stack:
    docker compose exec gateway python verify_m2.py
"""

import asyncio
import os
import time
from datetime import datetime

import requests
from asyncua import Client

OPCUA_URL = os.getenv("OPCUA_URL", "opc.tcp://localhost:4840/minifab/server/")
OPCUA_NAMESPACE_URI = os.getenv("OPCUA_NAMESPACE_URI", "urn:minifab:equipment")
API_URL = os.getenv("API_URL", "http://api:8000")
EQUIPMENT_CODES = ("LITHO-01", "ETCH-01", "DEP-01", "METRO-01")
FIELDS = ("TemperatureC", "PressureMTorr", "RFPowerW", "MachineState", "Timestamp", "AlarmCode")


async def main() -> None:
    print(f"[verify] connecting to OPC-UA at {OPCUA_URL}")
    initial_timestamps = {}
    async with Client(url=OPCUA_URL, timeout=5) as client:
        ns = await client.get_namespace_index(OPCUA_NAMESPACE_URI)
        root = await client.nodes.objects.get_child([f"{ns}:Equipment"])
        machine_nodes = {}
        for equipment_code in EQUIPMENT_CODES:
            machine = await root.get_child([f"{ns}:{equipment_code}"])
            machine_nodes[equipment_code] = {
                field: await machine.get_child([f"{ns}:{field}"]) for field in FIELDS
            }
            values = await client.read_values(list(machine_nodes[equipment_code].values()))
            readings = dict(zip(FIELDS, values))
            initial_timestamps[equipment_code] = readings["Timestamp"]
            print(
                f"  PASS {equipment_code}: state={readings['MachineState']}, "
                f"temperature={readings['TemperatureC']} °C, pressure={readings['PressureMTorr']} mTorr"
            )
            if readings["Timestamp"] is None:
                raise AssertionError(f"{equipment_code} has no source timestamp")

        # Confirm the simulator is changing its OPC-UA source values, not just exposing static tags.
        print("[verify] checking that source timestamps advance")
        changed = set()
        advanced_timestamps = {}
        source_deadline = time.monotonic() + 35
        while time.monotonic() < source_deadline and not changed:
            for equipment_code, nodes in machine_nodes.items():
                timestamp = await nodes["Timestamp"].read_value()
                if timestamp != initial_timestamps[equipment_code]:
                    changed.add(equipment_code)
                    advanced_timestamps[equipment_code] = timestamp
            if not changed:
                await asyncio.sleep(1)
        if not changed:
            raise AssertionError("No OPC-UA source timestamp changed; confirm simulator is running and producing telemetry")
        print(f"  PASS source timestamps advanced for: {', '.join(sorted(changed))}")

    print("[verify] waiting for OPC-UA-derived samples to reach the MES API")
    deadline = time.monotonic() + 50
    last_equipment = []
    while time.monotonic() < deadline:
        response = requests.get(f"{API_URL}/equipment", timeout=5)
        response.raise_for_status()
        equipment = response.json()
        last_equipment = [
            row for row in equipment
            if row.get("telemetry_source") == "opcua" and row.get("temperature_c") is not None
        ]
        db_by_code = {row["equipment_code"]: row for row in last_equipment}
        has_two_reporting = len(last_equipment) >= 2
        changed_sample_persisted = False
        for code, source_timestamp in advanced_timestamps.items():
            row = db_by_code.get(code)
            if row and row.get("telemetry_recorded_at"):
                db_timestamp = datetime.fromisoformat(row["telemetry_recorded_at"].replace("Z", "+00:00"))
                source_dt = datetime.fromisoformat(str(source_timestamp).replace("Z", "+00:00"))
                if db_timestamp >= source_dt:
                    changed_sample_persisted = True
                    break
        if has_two_reporting and changed_sample_persisted:
            break
        time.sleep(1)
    if len(last_equipment) < 2:
        raise AssertionError(
            "Fewer than two machines have OPC-UA-derived telemetry in the API. "
            "Check gateway logs and ensure the simulator is running."
        )
    if not changed_sample_persisted:
        raise AssertionError("An advanced OPC-UA source timestamp was not observed in the API/PostgreSQL telemetry records")

    for row in last_equipment:
        print(
            f"  PASS API {row['equipment_code']}: source={row['telemetry_source']}, "
            f"status={row.get('telemetry_status')}, temp={row['temperature_c']} °C, "
            f"sample_at={row['telemetry_recorded_at']}"
        )
    print(f"[verify] PASS: {len(last_equipment)} machines are visible in the MES API via OPC-UA -> MQTT -> PostgreSQL")
    print("[verify] For the disconnect/reconnect acceptance test, follow the README restart test and check gateway logs.")


if __name__ == "__main__":
    asyncio.run(main())
