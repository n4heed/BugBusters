import type { ServiceDependency } from "../../types/domain";

interface Node {
  id: string;
  depth: number;
  x: number;
  y: number;
  isOrigin: boolean;
  hasModeledEdge: boolean;
}

const ROW_H = 70;
const COL_W = 150;
const PAD = 60;

function layout(services: string[], dependencies: ServiceDependency[], origin: string) {
  const dependsOn = new Map<string, string>();
  dependencies.forEach((d) => dependsOn.set(d.service, d.depends_on));

  // reverse adjacency: target -> services that depend on it
  const dependents = new Map<string, string[]>();
  dependencies.forEach((d) => {
    const list = dependents.get(d.depends_on) ?? [];
    list.push(d.service);
    dependents.set(d.depends_on, list);
  });

  const depth = new Map<string, number>();
  const hasEdge = new Set<string>();
  const queue: string[] = [];

  if (services.includes(origin)) {
    depth.set(origin, 0);
    queue.push(origin);
  }

  while (queue.length > 0) {
    const current = queue.shift() as string;
    const currentDepth = depth.get(current) ?? 0;
    for (const dependent of dependents.get(current) ?? []) {
      hasEdge.add(dependent);
      hasEdge.add(current);
      if (!depth.has(dependent)) {
        depth.set(dependent, currentDepth + 1);
        queue.push(dependent);
      }
    }
  }

  // any service not reached gets depth 1 (unknown relation to origin, still shown)
  services.forEach((s) => {
    if (!depth.has(s)) depth.set(s, s === origin ? 0 : 1);
  });

  const maxDepth = Math.max(...Array.from(depth.values()), 0);
  const byDepth = new Map<number, string[]>();
  services.forEach((s) => {
    const d = depth.get(s) ?? 1;
    const list = byDepth.get(d) ?? [];
    list.push(s);
    byDepth.set(d, list);
  });

  const nodes: Node[] = [];
  byDepth.forEach((list, d) => {
    list.forEach((service, i) => {
      const rowWidth = list.length * COL_W;
      nodes.push({
        id: service,
        depth: d,
        x: PAD + i * COL_W + COL_W / 2 - rowWidth / 2 + (list.length * COL_W) / 2,
        y: PAD + (maxDepth - d) * ROW_H,
        isOrigin: service === origin,
        hasModeledEdge: hasEdge.has(service) || service === origin,
      });
    });
  });

  const width = Math.max(...Array.from(byDepth.values()).map((l) => l.length)) * COL_W + PAD * 2;
  const height = (maxDepth + 1) * ROW_H + PAD;

  return { nodes, dependsOn, width, height };
}

export function DependencyGraph({
  services,
  dependencies,
  origin,
}: {
  services: string[];
  dependencies: ServiceDependency[];
  origin: string;
}) {
  const { nodes, dependsOn, width, height } = layout(services, dependencies, origin);
  const nodeById = new Map(nodes.map((n) => [n.id, n]));

  return (
    <div>
      <div className="table-wrap">
        <svg
          className="dependency-graph"
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={`Service dependency graph. Probable origin: ${origin}.`}
        >
          <defs>
            <marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto">
              <path d="M0,0 L8,4 L0,8 Z" fill="var(--text-muted)" />
            </marker>
          </defs>

          {nodes.map((node) => {
            const target = dependsOn.get(node.id);
            const targetNode = target ? nodeById.get(target) : undefined;
            if (!targetNode) return null;
            return (
              <line
                key={`${node.id}->${target}`}
                x1={node.x}
                y1={node.y + 18}
                x2={targetNode.x}
                y2={targetNode.y - 18}
                stroke="var(--text-muted)"
                strokeWidth={2}
                markerEnd="url(#arrow)"
              />
            );
          })}

          {nodes.map((node) => (
            <g key={node.id} transform={`translate(${node.x}, ${node.y})`}>
              <rect
                x={-70}
                y={-18}
                width={140}
                height={36}
                rx={6}
                fill={node.isOrigin ? "rgba(255,119,119,0.12)" : "var(--surface-raised)"}
                stroke={node.isOrigin ? "var(--critical)" : "var(--border-strong)"}
                strokeWidth={node.isOrigin ? 3 : 2}
                strokeDasharray={node.hasModeledEdge ? undefined : "4 3"}
              />
              <text className="dep-node-label" textAnchor="middle" dy={5}>
                {node.id}
              </text>
            </g>
          ))}
        </svg>
      </div>
      <div className="dep-legend">
        <span>
          <span className="dep-legend-swatch" style={{ background: "var(--critical)" }} /> Suspected origin
        </span>
        <span>
          <span className="dep-legend-swatch" style={{ background: "var(--surface-raised)", border: "1px solid var(--border-strong)" }} /> Downstream / upstream service
        </span>
        <span>Arrows point from a service toward the dependency it relies on.</span>
        <span>Dashed border = no modeled dependency edge to this node.</span>
      </div>
    </div>
  );
}
