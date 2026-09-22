import { Panel } from "../Panel";
import type { SystemState } from "../../types";

interface StatusWidgetProps {
  system: SystemState;
}

export function StatusWidget({ system }: StatusWidgetProps) {
  const budgetPercent = system.budget.limit > 0
    ? ((system.budget.spent / system.budget.limit) * 100).toFixed(1)
    : "0";

  return (
    <Panel title="System Status" accent={system.connected}>
      <div className="space-y-3 text-sm font-mono">
        {/* Connection */}
        <div className="flex justify-between items-center">
          <span className="text-eva-text-dim">LINK</span>
          <span className={`flex items-center gap-1.5 ${system.connected ? "text-eva-accent" : "text-eva-danger"}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${system.connected ? "bg-eva-accent" : "bg-eva-danger"}`} />
            {system.connected ? "ONLINE" : "OFFLINE"}
          </span>
        </div>

        {/* Persona */}
        <div className="flex justify-between items-center">
          <span className="text-eva-text-dim">PERSONA</span>
          <span className="text-eva-cyan">{system.persona}</span>
        </div>

        {/* Version */}
        <div className="flex justify-between items-center">
          <span className="text-eva-text-dim">VERSION</span>
          <span className="text-eva-text">v{system.version}</span>
        </div>

        {/* Budget bar */}
        <div className="pt-1">
          <div className="flex justify-between items-center mb-1">
            <span className="text-eva-text-dim">VAULT</span>
            <span className={`${system.budget.exhausted ? "text-eva-danger" : "text-eva-text"}`}>
              {budgetPercent}%
            </span>
          </div>
          <div className="h-1 bg-eva-border rounded-full overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-500 ${
                system.budget.exhausted ? "bg-eva-danger" : Number(budgetPercent) > 80 ? "bg-eva-warning" : "bg-eva-cyan"
              }`}
              style={{ width: `${Math.min(100, Number(budgetPercent))}%` }}
            />
          </div>
          <div className="flex justify-between mt-1 text-[10px] text-eva-text-dim">
            <span>{system.budget.num_calls} calls</span>
            <span>{system.budget.remaining.toFixed(4)} EUR left</span>
          </div>
        </div>
      </div>
    </Panel>
  );
}