import type { DatasetProfile } from "../api/types";

/**
 * NOTE: the backend currently has no `GET /datasets/{id}/preview` endpoint
 * returning raw row samples — only aggregate profile stats exist (Segment
 * 1-2's DatasetProfile). Per IMPLEMENTATION_PLAN.md Segment 9, that
 * endpoint should be added as a follow-up; until then, this component
 * renders the column-level profile summary (types, missingness, top
 * values) rather than a literal row-by-row data preview, which is the
 * closest honest approximation of "preview" available from the current
 * API surface. Flagging this explicitly rather than silently building
 * against an endpoint that doesn't exist.
 */
interface DatasetPreviewProps {
  profile: DatasetProfile;
}

export function DatasetPreview({ profile }: DatasetPreviewProps) {
  return (
    <div className="overflow-x-auto rounded-lg border border-graphite-400/20">
      <table className="w-full text-sm">
        <thead className="bg-ink-900/[0.03] dark:bg-canvas-100/5 text-left text-xs uppercase tracking-wide text-graphite-500">
          <tr>
            <th className="px-3 py-2 font-medium">Column</th>
            <th className="px-3 py-2 font-medium">Type</th>
            <th className="px-3 py-2 font-medium">Missing</th>
            <th className="px-3 py-2 font-medium">Unique</th>
            <th className="px-3 py-2 font-medium">Sample values / stats</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-graphite-400/10">
          {profile.columns.map((col) => (
            <tr key={col.name}>
              <td className="px-3 py-2 font-medium">{col.name}</td>
              <td className="px-3 py-2 text-graphite-500">{col.inferred_type}</td>
              <td className="px-3 py-2 font-mono-tabular">
                {col.missing_pct > 0 ? `${col.missing_pct.toFixed(1)}%` : "—"}
              </td>
              <td className="px-3 py-2 font-mono-tabular">{col.unique_count}</td>
              <td className="px-3 py-2 text-graphite-500">
                {col.top_values
                  ? Object.keys(col.top_values).slice(0, 3).join(", ")
                  : col.mean != null
                    ? `mean ${col.mean.toFixed(1)}, range ${col.min?.toFixed(1)}–${col.max?.toFixed(1)}`
                    : "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
