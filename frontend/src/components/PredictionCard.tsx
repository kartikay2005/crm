import type { LocalExplanation } from "../api/types";

interface PredictionCardProps {
  explanation: LocalExplanation;
}

export function PredictionCard({ explanation }: PredictionCardProps) {
  return (
    <div className="rounded-lg border border-graphite-400/20 p-4">
      <div className="flex items-baseline justify-between">
        <span className="text-xs uppercase tracking-wide text-graphite-500">Row {explanation.row_index}</span>
      </div>
      <p className="mt-1 text-sm">{explanation.natural_language}</p>
      <div className="mt-3 space-y-1.5">
        {explanation.top_contributors.map((c) => (
          <div key={c.feature} className="flex items-center gap-2 text-xs">
            <span
              className={`w-16 shrink-0 font-medium ${c.direction === "increases" ? "text-crimson-500" : "text-signal-500"}`}
            >
              {c.direction === "increases" ? "▲ up" : "▼ down"}
            </span>
            <span className="font-mono-tabular text-graphite-500 w-24 truncate">{c.feature}</span>
            <span className="font-mono-tabular text-graphite-400">{c.value.toFixed(2)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
