import { Panel } from "../Panel";

interface ActivityWidgetProps {
  log: string[];
}

export function ActivityWidget({ log }: ActivityWidgetProps) {
  return (
    <Panel title="Activity Log :: PULSE">
      <div className="h-48 overflow-y-auto space-y-1">
        {log.length === 0 ? (
          <p className="text-xs font-mono text-eva-text-dim">Awaiting activity...</p>
        ) : (
          log.map((entry, i) => (
            <p
              key={i}
              className={`text-xs font-mono ${i === 0 ? "text-eva-cyan" : "text-eva-text-dim"}`}
            >
              {entry}
            </p>
          ))
        )}
      </div>
    </Panel>
  );
}