import { AlertTriangle, Info, AlertOctagon } from "lucide-react";
import type { Insight, Recommendation } from "../api/types";

const SEVERITY_ICON = { info: Info, warning: AlertTriangle, critical: AlertOctagon };
const SEVERITY_COLOR = {
  info: "text-graphite-500",
  warning: "text-amber-500",
  critical: "text-crimson-500",
};

export function InsightCard({ insight }: { insight: Insight }) {
  const Icon = SEVERITY_ICON[insight.severity];
  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-graphite-400/20 p-3">
      <Icon className={`w-4 h-4 mt-0.5 shrink-0 ${SEVERITY_COLOR[insight.severity]}`} />
      <p className="text-sm">{insight.text}</p>
    </div>
  );
}

const PRIORITY_STYLES = {
  high: "bg-crimson-100 text-crimson-500 dark:bg-crimson-500/15",
  medium: "bg-amber-100 text-amber-500 dark:bg-amber-500/15",
  low: "bg-graphite-400/10 text-graphite-500",
};

export function RecommendationCard({ recommendation }: { recommendation: Recommendation }) {
  return (
    <div className="rounded-lg border border-graphite-400/20 p-3">
      <div className="flex items-center gap-2">
        <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase ${PRIORITY_STYLES[recommendation.priority]}`}>
          {recommendation.priority}
        </span>
        <span className="text-xs text-graphite-500">{recommendation.domain}</span>
      </div>
      <p className="mt-1.5 text-sm font-medium">{recommendation.text}</p>
      <p className="mt-1 text-xs text-graphite-500">{recommendation.rationale}</p>
    </div>
  );
}
