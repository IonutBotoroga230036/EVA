import { useState, useEffect } from "react";
import { Panel } from "../Panel";

export function ClockWidget() {
  const [now, setNow] = useState(new Date());

  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  const time = now.toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });

  const date = now.toLocaleDateString("en-GB", {
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
  });

  return (
    <Panel title="Chronometer">
      <div className="text-center">
        <div className="text-3xl font-mono text-eva-cyan tracking-widest">{time}</div>
        <div className="text-xs font-mono text-eva-text-dim mt-1 uppercase tracking-wider">
          {date}
        </div>
      </div>
    </Panel>
  );
}