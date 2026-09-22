import type { ReactNode } from "react";

interface PanelProps {
  title: string;
  children: ReactNode;
  className?: string;
  accent?: boolean;
}

export function Panel({ title, children, className = "", accent = false }: PanelProps) {
  return (
    <div
      className={`
        relative rounded-lg overflow-hidden
        bg-eva-panel border border-eva-border
        backdrop-blur-md
        ${accent ? "animate-pulse-glow" : ""}
        ${className}
      `}
    >
      {/* Header bar */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-eva-border">
        <div className="w-1.5 h-1.5 rounded-full bg-eva-cyan" />
        <span className="text-[11px] font-mono uppercase tracking-widest text-eva-cyan-dim">
          {title}
        </span>
      </div>

      {/* Content */}
      <div className="p-4">{children}</div>
    </div>
  );
}