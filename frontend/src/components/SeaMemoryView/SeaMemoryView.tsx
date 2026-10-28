import { useEffect, useState } from "react";
import type { DataSource, SeaMemoryEvent } from "../../types/domain";
import { api } from "../../api/client";
import { MemorySummary } from "./MemorySummary";
import { MemoryEventLog } from "./MemoryEventLog";
import { DemoBadge } from "../common/DemoBadge";

export function SeaMemoryView({
  dataSource,
  demoMemoryLog,
}: {
  dataSource: DataSource;
  demoMemoryLog: SeaMemoryEvent[];
}) {
  const [liveEvents, setLiveEvents] = useState<SeaMemoryEvent[] | null>(null);

  useEffect(() => {
    let active = true;
    if (dataSource === "live") {
      api
        .listMemory()
        .then((events) => {
          if (active) setLiveEvents(events);
        })
        .catch(() => {
          if (active) setLiveEvents(null);
        });
    }
    return () => {
      active = false;
    };
  }, [dataSource]);

  const isDemo = liveEvents === null;
  const events = isDemo ? demoMemoryLog : liveEvents;

  return (
    <div>
      <h2 className="section-heading">SEA // Self-Evolving Agent Memory</h2>
      <p className="section-subtitle">Human-verified incident patterns and reviewed alert-tuning decisions.</p>
      {isDemo && (
        <div style={{ marginBottom: 12 }}>
          <DemoBadge />
        </div>
      )}

      <MemorySummary events={events} />

      <section className="panel">
        <MemoryEventLog events={events} />
      </section>
    </div>
  );
}
