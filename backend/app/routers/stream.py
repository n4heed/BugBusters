"""WebSocket alert stream.

Endpoint: ws://localhost:8000/ws/alert-stream

Protocol (demo scope — this backend has no real alert source wired in, so
connecting alone sends nothing):

  Client -> Server JSON messages:
    {"action": "start_simulation"}  - server begins emitting clearly-marked
                                       synthetic alerts on an interval
    {"action": "stop_simulation"}   - server stops emitting

  Server -> Client JSON messages:
    {"type": "connected"}
    {"type": "simulation_started"}
    {"type": "simulation_stopped"}
    {"type": "alert", "is_synthetic": true, "alert": {...Alert fields...}}
    {"type": "processing_update", "is_synthetic": true, "result": {...ProcessingResult fields...}}
    {"type": "error", "detail": "..."}

Every synthetic alert is run through the real Triage -> Investigation ->
Verification pipeline (see orchestrator.py) before either message is sent —
this simulator does not just display events, it exercises the correlation
engine. `is_synthetic: true` on both message types is the only provenance
guarantee this socket makes; there is no mechanism here that emits real
production telemetry.
"""
from __future__ import annotations

import asyncio
import json
import random
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..dataset_registry import registry

router = APIRouter()

_SERVICES = [
    "primary-postgres-db",
    "core-app-svc-01",
    "api-gateway",
    "redis-session-cluster",
    "payments-ledger-db",
    "async-worker-01",
]
_SIGNALS = [
    ("CPU > 90%", "high"),
    ("Disk I/O elevated", "medium"),
    ("HTTP 5xx rate rising", "critical"),
    ("Queue backlog growing", "high"),
    ("Latency p99 > 1.5s", "medium"),
]


def _synthetic_raw_alert() -> dict:
    service = random.choice(_SERVICES)
    signal, severity = random.choice(_SIGNALS)
    return {
        "id": f"SIM-{uuid.uuid4().hex[:8]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": service,
        "signal": signal,
        "severity": severity,
    }


@router.websocket("/ws/alert-stream")
async def alert_stream(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_text(json.dumps({"type": "connected"}))

    simulation_task: asyncio.Task | None = None

    async def run_simulation() -> None:
        try:
            while True:
                await asyncio.sleep(random.uniform(1.0, 2.5))
                active = registry.get_active()
                if active.orchestrator is None or active.store is None:
                    await websocket.send_text(
                        json.dumps({"type": "error", "detail": "Active dataset has not been loaded; simulation paused."})
                    )
                    continue
                raw = _synthetic_raw_alert()
                # Run off the event loop: if AI is enabled, process_alert may
                # make a blocking HTTP call to the model provider, which must
                # not stall other WebSocket/HTTP handling.
                result = await asyncio.to_thread(active.orchestrator.process_alert, raw, True)
                alert = active.store.all_alerts.get(result.alert_id)
                if alert is not None:
                    await websocket.send_text(
                        json.dumps(
                            {
                                "type": "alert",
                                "is_synthetic": True,
                                "alert": json.loads(alert.model_dump_json()),
                            }
                        )
                    )
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "processing_update",
                            "is_synthetic": True,
                            "result": json.loads(result.model_dump_json()),
                        }
                    )
                )
        except asyncio.CancelledError:
            pass

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"type": "error", "detail": "Malformed JSON message"}))
                continue

            action = message.get("action")
            if action == "start_simulation":
                if simulation_task is None or simulation_task.done():
                    simulation_task = asyncio.create_task(run_simulation())
                    await websocket.send_text(json.dumps({"type": "simulation_started"}))
            elif action == "stop_simulation":
                if simulation_task is not None and not simulation_task.done():
                    simulation_task.cancel()
                    simulation_task = None
                await websocket.send_text(json.dumps({"type": "simulation_stopped"}))
            else:
                await websocket.send_text(
                    json.dumps({"type": "error", "detail": f"Unsupported action: {action!r}"})
                )
    except WebSocketDisconnect:
        pass
    finally:
        if simulation_task is not None and not simulation_task.done():
            simulation_task.cancel()
