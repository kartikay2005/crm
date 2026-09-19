import { Fragment } from "react";
import {
  BarChart, Bar, PieChart, Pie, Cell, LineChart, Line, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";
import type { Chart } from "../api/types";

const PALETTE = ["#2f6f5e", "#c97a2b", "#a63d40", "#6b7285", "#245a4c", "#8890a0"];

function BarChartView({ chart }: { chart: Chart }) {
  const bars = (chart.data.bars ?? []) as { label: string; value: number }[];
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={bars} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#8890a022" />
        <XAxis dataKey="label" tick={{ fontSize: 11 }} interval={0} angle={-25} textAnchor="end" height={50} />
        <YAxis tick={{ fontSize: 11 }} />
        <Tooltip />
        <Bar dataKey="value" fill="#2f6f5e" radius={[3, 3, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

function HistogramView({ chart }: { chart: Chart }) {
  const buckets = (chart.data.buckets ?? []) as { label: string; value: number }[];
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={buckets} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#8890a022" />
        <XAxis dataKey="label" tick={{ fontSize: 9 }} interval={2} angle={-30} textAnchor="end" height={45} />
        <YAxis tick={{ fontSize: 11 }} />
        <Tooltip />
        <Bar dataKey="value" fill="#245a4c" radius={[2, 2, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}

function PieChartView({ chart }: { chart: Chart }) {
  const slices = (chart.data.slices ?? []) as { label: string; value: number }[];
  return (
    <ResponsiveContainer width="100%" height={220}>
      <PieChart>
        <Pie data={slices} dataKey="value" nameKey="label" cx="50%" cy="50%" outerRadius={80} label>
          {slices.map((_, i) => <Cell key={i} fill={PALETTE[i % PALETTE.length]} />)}
        </Pie>
        <Tooltip />
      </PieChart>
    </ResponsiveContainer>
  );
}

function LineChartView({ chart }: { chart: Chart }) {
  const points = (chart.data.points ?? []) as { x: number; y: number }[];
  return (
    <ResponsiveContainer width="100%" height={220}>
      <LineChart data={points} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#8890a022" />
        <XAxis dataKey="x" tick={{ fontSize: 11 }} type="number" domain={[0, 1]} />
        <YAxis tick={{ fontSize: 11 }} type="number" domain={[0, 1]} />
        <Tooltip />
        <Line type="monotone" dataKey="y" stroke="#2f6f5e" dot={false} strokeWidth={2} />
      </LineChart>
    </ResponsiveContainer>
  );
}

function HeatmapView({ chart }: { chart: Chart }) {
  // Simple grid render — recharts has no native heatmap primitive, and a
  // full dependency for one chart type isn't worth it. Renders as a CSS
  // grid of colored cells, which is sufficient for correlation matrices
  // and confusion matrices (small, square-ish data).
  const columns = (chart.data.columns ?? []) as string[];
  const cells = (chart.data.cells ?? []) as { x: string; y: string; value: number | null }[];
  const cellMap = new Map(cells.map((c) => [`${c.x}|${c.y}`, c.value]));
  const maxAbs = Math.max(1, ...cells.map((c) => Math.abs(c.value ?? 0)));

  return (
    <div className="overflow-x-auto">
      <div
        className="grid gap-0.5 text-[10px]"
        style={{ gridTemplateColumns: `80px repeat(${columns.length}, minmax(36px, 1fr))` }}
      >
        <div />
        {columns.map((c) => (
          <div key={c} className="truncate text-center text-graphite-500 px-1">{c}</div>
        ))}
        {columns.map((rowLabel) => (
          <Fragment key={rowLabel}>
            <div className="truncate text-graphite-500 pr-1 flex items-center">{rowLabel}</div>
            {columns.map((colLabel) => {
              const v = cellMap.get(`${colLabel}|${rowLabel}`) ?? cellMap.get(`${rowLabel}|${colLabel}`) ?? null;
              const intensity = v == null ? 0 : Math.min(1, Math.abs(v) / maxAbs);
              const color = v == null ? "transparent" : v >= 0 ? "#2f6f5e" : "#a63d40";
              return (
                <div
                  key={`${rowLabel}-${colLabel}`}
                  className="aspect-square flex items-center justify-center rounded-sm font-mono-tabular"
                  style={{ backgroundColor: color, opacity: v == null ? 0 : 0.15 + intensity * 0.85 }}
                  title={v != null ? v.toFixed(2) : "—"}
                >
                  {v != null && <span style={{ color: intensity > 0.5 ? "white" : "inherit" }}>{v.toFixed(1)}</span>}
                </div>
              );
            })}
          </Fragment>
        ))}
      </div>
    </div>
  );
}

function BoxPlotView({ chart }: { chart: Chart }) {
  const boxes = (chart.data.boxes ?? []) as { column: string; min: number; q1: number; median: number; q3: number; max: number }[];
  return (
    <div className="space-y-3">
      {boxes.map((b) => {
        const range = b.max - b.min || 1;
        const pct = (v: number) => ((v - b.min) / range) * 100;
        return (
          <div key={b.column} className="text-xs">
            <div className="flex justify-between mb-1">
              <span className="font-medium">{b.column}</span>
              <span className="font-mono-tabular text-graphite-500">
                {b.min.toFixed(1)} – {b.max.toFixed(1)}
              </span>
            </div>
            <div className="relative h-4 rounded bg-graphite-400/10">
              <div
                className="absolute top-0 h-full rounded bg-signal-500/30"
                style={{ left: `${pct(b.q1)}%`, width: `${pct(b.q3) - pct(b.q1)}%` }}
              />
              <div className="absolute top-0 h-full w-0.5 bg-signal-600" style={{ left: `${pct(b.median)}%` }} />
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function ChartView({ chart }: { chart: Chart }) {
  return (
    <div className="rounded-lg border border-graphite-400/20 p-4">
      <h4 className="text-sm font-medium mb-2">{chart.title}</h4>
      {chart.type === "bar" && <BarChartView chart={chart} />}
      {chart.type === "histogram" && <HistogramView chart={chart} />}
      {chart.type === "pie" && <PieChartView chart={chart} />}
      {chart.type === "line" && <LineChartView chart={chart} />}
      {chart.type === "heatmap" && <HeatmapView chart={chart} />}
      {chart.type === "box" && <BoxPlotView chart={chart} />}
    </div>
  );
}

export function ChartsGrid({ charts }: { charts: Chart[] }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
      {charts.map((c) => <ChartView key={c.chart_id} chart={c} />)}
    </div>
  );
}
