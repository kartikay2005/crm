/**
 * The app's signature visual element (see index.css's design-concept
 * comment) — an analog-dial-style gauge, not a generic flat progress bar.
 * Used for domain confidence, quality score, and AI readiness score
 * consistently, so it becomes the visual through-line of the whole app.
 *
 * Reads the color band from the backend (Segment 2's green/yellow/red
 * banding) rather than recomputing thresholds client-side — the server is
 * the source of truth for what counts as "good."
 */
interface ConfidenceGaugeProps {
  score: number; // 0-100
  label: string;
  band?: "green" | "yellow" | "red";
  size?: number;
}

const BAND_COLORS: Record<string, string> = {
  green: "var(--color-signal-500)",
  yellow: "var(--color-amber-500)",
  red: "var(--color-crimson-500)",
};

function bandForScore(score: number): "green" | "yellow" | "red" {
  if (score >= 80) return "green";
  if (score >= 50) return "yellow";
  return "red";
}

export function ConfidenceGauge({ score, label, band, size = 96 }: ConfidenceGaugeProps) {
  const resolvedBand = band ?? bandForScore(score);
  const color = BAND_COLORS[resolvedBand];
  const radius = (size - 12) / 2;
  const circumference = 2 * Math.PI * radius;
  // Dial sweeps 270° (like a real analog instrument), not a full circle —
  // leaves a visible "gap" at the bottom, reinforcing the dial-gauge read.
  const sweepFraction = 0.75;
  const filledLength = circumference * sweepFraction * (score / 100);
  const totalDashLength = circumference * sweepFraction;

  return (
    <div className="flex flex-col items-center gap-1.5" style={{ width: size }}>
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="-rotate-[135deg]">
          <circle
            cx={size / 2} cy={size / 2} r={radius} fill="none"
            stroke="currentColor" className="text-ink-700/10 dark:text-canvas-100/10"
            strokeWidth={7} strokeDasharray={`${totalDashLength} ${circumference}`}
            strokeLinecap="round"
          />
          <circle
            cx={size / 2} cy={size / 2} r={radius} fill="none"
            stroke={color} strokeWidth={7}
            strokeDasharray={`${filledLength} ${circumference}`}
            strokeLinecap="round"
            style={{ transition: "stroke-dasharray 0.6s ease-out" }}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center">
          <span className="font-mono-tabular text-xl font-semibold leading-none">
            {Math.round(score)}
          </span>
          <span className="text-[10px] uppercase tracking-wide text-graphite-500 dark:text-graphite-400 mt-0.5">
            /100
          </span>
        </div>
      </div>
      <span className="text-xs font-medium text-graphite-600 dark:text-graphite-400 text-center leading-tight">
        {label}
      </span>
    </div>
  );
}
