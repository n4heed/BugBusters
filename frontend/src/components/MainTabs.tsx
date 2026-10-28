export type TabKey = "incidents" | "tuning" | "memory" | "datasets";

const TABS: { key: TabKey; label: string }[] = [
  { key: "incidents", label: "Active Incidents" },
  { key: "tuning", label: "Folklore Noise & Tuning" },
  { key: "memory", label: "SEA Memory" },
  { key: "datasets", label: "Datasets" },
];

export function MainTabs({ active, onChange }: { active: TabKey; onChange: (key: TabKey) => void }) {
  return (
    <nav className="main-tabs" role="tablist" aria-label="Main sections">
      {TABS.map((tab) => (
        <button
          key={tab.key}
          role="tab"
          aria-selected={active === tab.key}
          className={`main-tab${active === tab.key ? " active" : ""}`}
          onClick={() => onChange(tab.key)}
        >
          {tab.label}
        </button>
      ))}
    </nav>
  );
}
