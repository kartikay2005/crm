const BAND_STYLES: Record<string, string> = {
  green: "bg-signal-100 text-signal-600 dark:bg-signal-500/15 dark:text-signal-500",
  yellow: "bg-amber-100 text-amber-500 dark:bg-amber-500/15",
  red: "bg-crimson-100 text-crimson-500 dark:bg-crimson-500/15",
};

interface DomainBadgeProps {
  domain: string;
  confidence: number;
  band: "green" | "yellow" | "red";
}

export function DomainBadge({ domain, confidence, band }: DomainBadgeProps) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-sm font-medium ${BAND_STYLES[band]}`}>
      {domain}
      <span className="font-mono-tabular text-xs opacity-75">{confidence.toFixed(0)}%</span>
    </span>
  );
}
