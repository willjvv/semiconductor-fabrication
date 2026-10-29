"""Read-only OPC-UA equipment data server for the MiniFab simulator.

The synchronous fab simulator updates these nodes from its simulated equipment
state. The MES gateway reads the nodes over OPC-UA and forwards normalized
telemetry to MQTT. This is a local portfolio simulation, not a control server.
"""

from __future__ import annotations

import asyncio
import os
import threading
from datetime import datetime, timezone
from typing import Any

from asyncua import Server

OPCUA_ENDPOINT = os.getenv("OPCUA_ENDPOINT", "opc.tcp://0.0.0.0:4840/minifab/server/")
OPCUA_NAMESPACE_URI = os.getenv("OPCUA_NAMESPACE_URI", "urn:minifab:equipment")

EQUIPMENT = {
    "LITHO-01": {"process_type": "LITHOGRAPHY", "temperature_c": 58.0, "pressure_mtorr": 1.0, "rf_power_w": 420.0},
    "ETCH-01": {"process_type": "ETCH", "temperature_c": 72.0, "pressure_mtorr": 18.0, "rf_power_w": 850.0},
    "DEP-01": {"process_type": "DEPOSITION", "temperature_c": 68.0, "pressure_mtorr": 12.0, "rf_power_w": 640.0},
    "METRO-01": {"process_type": "INSPECTION", "temperature_c": 24.0, "pressure_mtorr": 0.8, "rf_power_w": 25.0},
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def initial_values(equipment_code: str) -> dict[str, Any]:
    info = EQUIPMENT[equipment_code]
    return {
        "TemperatureC": float(info["temperature_c"]),
        "PressureMTorr": float(info["pressure_mtorr"]),
        "RFPowerW": float(info["rf_power_w"]),
        "MachineState": "IDLE",
        "CurrentWaferCode": "",
        "RemainingSeconds": 0.0,
        "ProcessType": str(info["process_type"]),
        "RuntimeIncrementSeconds": 0.0,
        "Timestamp": now_iso(),
        "AlarmCode": 0,
        "LastEvent": "ServerStarted",
    }


class EquipmentOpcUaServer:
    """Runs asyncua on a dedicated event-loop thread for the sync simulator."""

    def __init__(self) -> None:
        self.endpoint = OPCUA_ENDPOINT
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._startup_error: BaseException | None = None
        self._server: Server | None = None
        self._nodes: dict[str, dict[str, Any]] = {}
        self._values: dict[str, dict[str, Any]] = {}

    def start(self, timeout: float = 20.0) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._startup_error = None
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._thread_main, name="minifab-opcua", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            raise TimeoutError(f"OPC-UA server did not start within {timeout:.0f} seconds")
        if self._startup_error:
            raise RuntimeError("OPC-UA server failed to start") from self._startup_error
        print(f"[opcua] read-only equipment server listening at {self.endpoint}", flush=True)

    def _thread_main(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._start_server())
        except BaseException as exc:
            self._startup_error = exc
            self._ready.set()
            try:
                self._loop.close()
            except Exception:
                pass
            return
        self._loop.run_forever()
        pending = asyncio.all_tasks(self._loop)
        for task in pending:
            task.cancel()
        if pending:
            try:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
        self._loop.close()

    async def _start_server(self) -> None:
        self._server = Server()
        await self._server.init()
        self._server.set_endpoint(self.endpoint)
        self._server.set_server_name("MiniFab Simulated Equipment OPC-UA Server")
        namespace_index = await self._server.register_namespace(OPCUA_NAMESPACE_URI)
        equipment_root = await self._server.nodes.objects.add_object(namespace_index, "Equipment")

        for equipment_code in EQUIPMENT:
            machine_node = await equipment_root.add_object(namespace_index, equipment_code)
            values = initial_values(equipment_code)
            self._values[equipment_code] = values.copy()
            self._nodes[equipment_code] = {}
            for field_name, value in values.items():
                variable = await machine_node.add_variable(namespace_index, field_name, value)
                # Deliberately do not mark these variables writable by remote clients.
                self._nodes[equipment_code][field_name] = variable

        await self._server.start()
        self._ready.set()

    def _submit(self, coroutine, timeout: float = 4.0) -> None:
        if not self._loop or not self._thread or not self._thread.is_alive():
            raise RuntimeError("OPC-UA server is not running")
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        future.result(timeout=timeout)

    async def _set_values(self, equipment_code: str, updates: dict[str, Any]) -> None:
        if equipment_code not in self._nodes:
            return
        current = self._values[equipment_code]
        for field_name, value in updates.items():
            if value is None or field_name not in self._nodes[equipment_code]:
                continue
            # Normalize the OPC-UA value types so the tag schema stays stable.
            if field_name in {"TemperatureC", "PressureMTorr", "RFPowerW", "RemainingSeconds", "RuntimeIncrementSeconds"}:
                value = float(value)
            elif field_name == "AlarmCode":
                value = int(value)
            elif field_name in {"MachineState", "CurrentWaferCode", "ProcessType", "Timestamp", "LastEvent"}:
                value = str(value)
            await self._nodes[equipment_code][field_name].set_value(value)
            current[field_name] = value

    def update_from_telemetry(self, payload: dict[str, Any]) -> None:
        equipment_code = payload.get("equipment_code")
        if equipment_code not in EQUIPMENT:
            return
        current = self._values.get(equipment_code, {})
        is_down = current.get("MachineState") == "DOWN"
        updates = {
            "TemperatureC": payload.get("temperature_c"),
            "PressureMTorr": payload.get("pressure_mtorr"),
            "RFPowerW": payload.get("rf_power_w"),
            "CurrentWaferCode": payload.get("wafer_code") or "",
            "RemainingSeconds": payload.get("remaining_seconds", 0),
            "ProcessType": payload.get("process_type", EQUIPMENT[equipment_code]["process_type"]),
            "RuntimeIncrementSeconds": payload.get("runtime_increment_seconds", 0),
            "Timestamp": payload.get("timestamp") or now_iso(),
        }
        # Telemetry from a paused job reports HOLD. Do not let that overwrite the
        # equipment-level DOWN state or clear its alarm while maintenance is active.
        if not is_down:
            updates.update({
                "MachineState": payload.get("status", "RUNNING"),
                "AlarmCode": 0,
                "LastEvent": "TelemetryUpdated",
            })
        self._submit(self._set_values(equipment_code, updates))

    def update_from_event(self, payload: dict[str, Any]) -> None:
        equipment_code = payload.get("equipment_code")
        event_type = payload.get("event_type", "Unknown")
        if equipment_code not in EQUIPMENT:
            return
        current = self._values.get(equipment_code, {})
        updates: dict[str, Any] = {
            "Timestamp": payload.get("timestamp") or now_iso(),
            "LastEvent": event_type,
            # Event-generated updates must not increment equipment runtime.
            "RuntimeIncrementSeconds": 0.0,
        }
        wafer_code = payload.get("wafer_code")
        if event_type == "ProcessStarted":
            updates.update(MachineState="RUNNING", CurrentWaferCode=wafer_code or "", AlarmCode=0,
                           ProcessType=payload.get("process_type", current.get("ProcessType", "")))
        elif event_type == "ProcessCompleted":
            updates.update(MachineState="IDLE", CurrentWaferCode="", RemainingSeconds=0.0)
        elif event_type == "EquipmentDown":
            updates.update(MachineState="DOWN", AlarmCode=1001)
        elif event_type == "EquipmentRepaired":
            updates.update(MachineState="IDLE", AlarmCode=0)
        elif event_type == "WaferHold":
            updates.update(MachineState="HOLD", CurrentWaferCode=wafer_code or current.get("CurrentWaferCode", ""))
        elif event_type == "WaferReleased":
            updates.update(MachineState="RUNNING", CurrentWaferCode=wafer_code or current.get("CurrentWaferCode", ""), AlarmCode=0)
        self._submit(self._set_values(equipment_code, updates))

    def stop(self, timeout: float = 5.0) -> None:
        if not self._loop or not self._thread or not self._thread.is_alive():
            return
        if self._server:
            try:
                future = asyncio.run_coroutine_threadsafe(self._server.stop(), self._loop)
                future.result(timeout=timeout)
            except Exception as exc:
                print(f"[opcua] graceful stop warning: {exc}", flush=True)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=timeout)


if __name__ == "__main__":
    # Useful for quick local exploration; the normal entry point is simulator/main.py.
    import signal

    server = EquipmentOpcUaServer()
    server.start()
    try:
        while True:
            threading.Event().wait(1)
    except KeyboardInterrupt:
        server.stop()
