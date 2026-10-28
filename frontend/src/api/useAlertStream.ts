// Dedicated hook for the live alert-stream WebSocket at /ws/alert-stream.
// Connecting alone does not generate traffic: the backend only emits events
// after this client explicitly sends {action: "start_simulation"}, and every
// event it emits is marked is_synthetic. This hook never fabricates events
// itself — it only renders what the server actually sends.
import { useCallback, useEffect, useRef, useState } from "react";
import { API_BASE } from "./client";
import type { StreamStatus } from "../types/domain";

export interface StreamAlertEvent {
  id: string;
  timestamp: string;
  service: string;
  signal: string;
  severity: string;
  is_synthetic: boolean;
}

function wsUrl(): string {
  const base = new URL(API_BASE);
  const protocol = base.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${base.host}/ws/alert-stream`;
}

export function useAlertStream() {
  const [status, setStatus] = useState<StreamStatus>("DISCONNECTED");
  const [events, setEvents] = useState<StreamAlertEvent[]>([]);
  const [simulationRunning, setSimulationRunning] = useState(false);
  const [processingStatusById, setProcessingStatusById] = useState<Record<string, string>>({});
  const socketRef = useRef<WebSocket | null>(null);
  const waitingTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearWaitingTimer = () => {
    if (waitingTimerRef.current) {
      clearTimeout(waitingTimerRef.current);
      waitingTimerRef.current = null;
    }
  };

  const connect = useCallback(() => {
    if (socketRef.current && socketRef.current.readyState <= WebSocket.OPEN) {
      return; // already connecting/connected — avoid duplicate sockets
    }
    setStatus("CONNECTING");
    let socket: WebSocket;
    try {
      socket = new WebSocket(wsUrl());
    } catch {
      setStatus("ERROR");
      return;
    }
    socketRef.current = socket;

    socket.onopen = () => {
      setStatus("CONNECTED");
      waitingTimerRef.current = setTimeout(() => setStatus("CONNECTED_WAITING"), 3000);
    };

    socket.onmessage = (ev) => {
      clearWaitingTimer();
      let payload: unknown;
      try {
        payload = JSON.parse(ev.data);
      } catch {
        return; // malformed message — drop it, never crash the UI
      }
      if (typeof payload !== "object" || payload === null) return;
      const msg = payload as Record<string, unknown>;

      if (msg.type === "alert" && typeof msg.alert === "object" && msg.alert !== null) {
        const a = msg.alert as Record<string, unknown>;
        if (
          typeof a.id === "string" &&
          typeof a.timestamp === "string" &&
          typeof a.service === "string" &&
          typeof a.signal === "string" &&
          typeof a.severity === "string"
        ) {
          setEvents((prev) =>
            [
              {
                id: a.id as string,
                timestamp: a.timestamp as string,
                service: a.service as string,
                signal: a.signal as string,
                severity: a.severity as string,
                is_synthetic: Boolean(msg.is_synthetic),
              },
              ...prev,
            ].slice(0, 50),
          );
          setStatus("CONNECTED");
          clearWaitingTimer();
          waitingTimerRef.current = setTimeout(() => setStatus("CONNECTED_WAITING"), 5000);
        }
        return;
      }
      if (msg.type === "processing_update" && typeof msg.result === "object" && msg.result !== null) {
        const r = msg.result as Record<string, unknown>;
        if (typeof r.alert_id === "string" && typeof r.workflow_status === "string") {
          setProcessingStatusById((prev) => ({ ...prev, [r.alert_id as string]: r.workflow_status as string }));
        }
        return;
      }
      if (msg.type === "simulation_started") setSimulationRunning(true);
      if (msg.type === "simulation_stopped") setSimulationRunning(false);
    };

    socket.onerror = () => {
      setStatus("ERROR");
    };

    socket.onclose = () => {
      setStatus("DISCONNECTED");
      setSimulationRunning(false);
      clearWaitingTimer();
      socketRef.current = null;
    };
  }, []);

  const disconnect = useCallback(() => {
    clearWaitingTimer();
    socketRef.current?.close();
    socketRef.current = null;
    setStatus("DISCONNECTED");
    setSimulationRunning(false);
  }, []);

  const startSimulation = useCallback(() => {
    socketRef.current?.send(JSON.stringify({ action: "start_simulation" }));
  }, []);

  const stopSimulation = useCallback(() => {
    socketRef.current?.send(JSON.stringify({ action: "stop_simulation" }));
  }, []);

  useEffect(() => {
    return () => {
      clearWaitingTimer();
      socketRef.current?.close();
    };
  }, []);

  return {
    status,
    events,
    simulationRunning,
    processingStatusById,
    connect,
    disconnect,
    startSimulation,
    stopSimulation,
  };
}
