import { useState } from "react";
import { Download, Loader2 } from "lucide-react";
import { downloadReport } from "../api/datasets";

const FORMATS = [
  { value: "pdf", label: "PDF" },
  { value: "excel", label: "Excel" },
  { value: "csv", label: "CSV" },
  { value: "json", label: "JSON" },
] as const;

export function ReportDownload({ datasetId }: { datasetId: string }) {
  const [format, setFormat] = useState<(typeof FORMATS)[number]["value"]>("pdf");
  const [isDownloading, setIsDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleDownload = async () => {
    setIsDownloading(true);
    setError(null);
    try {
      await downloadReport(datasetId, format);
    } catch {
      setError(
        format === "csv"
          ? "No prediction data is available to export as CSV for this dataset."
          : "Could not generate the report. Please try again.",
      );
    } finally {
      setIsDownloading(false);
    }
  };

  return (
    <div className="flex items-center gap-2">
      <select
        value={format}
        onChange={(e) => setFormat(e.target.value as typeof format)}
        className="rounded border border-graphite-400/30 bg-transparent px-2 py-1.5 text-sm"
      >
        {FORMATS.map((f) => <option key={f.value} value={f.value}>{f.label}</option>)}
      </select>
      <button
        onClick={handleDownload}
        disabled={isDownloading}
        className="flex items-center gap-1.5 rounded bg-signal-500 px-3 py-1.5 text-sm font-medium text-white hover:bg-signal-600 disabled:opacity-50"
      >
        {isDownloading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
        Download
      </button>
      {error && <span className="text-xs text-crimson-500">{error}</span>}
    </div>
  );
}
