import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Trash2 } from "lucide-react";
import { ThemeToggle } from "../components/ThemeToggle";
import {
  deleteDataset, getAnalysis, getCharts, getInsights, getPredictions,
  getProfile, getRecommendations,
} from "../api/datasets";
import { ConfidenceGauge } from "../components/ConfidenceGauge";
import { DomainBadge } from "../components/DomainBadge";
import { DatasetPreview } from "../components/DatasetPreview";
import { PredictionCard } from "../components/PredictionCard";
import { InsightCard, RecommendationCard } from "../components/InsightCard";
import { ChartsGrid } from "../components/Charts";
import { ReportDownload } from "../components/ReportDownload";
import type { DatasetProfile } from "../api/types";

type Tab = "profile" | "domain" | "prediction" | "insights" | "charts" | "report";
const TABS: { id: Tab; label: string }[] = [
  { id: "profile", label: "Profile" }, { id: "domain", label: "Domain" },
  { id: "prediction", label: "Prediction" }, { id: "insights", label: "Insights" },
  { id: "charts", label: "Charts" }, { id: "report", label: "Report" },
];

export function DatasetDetail() {
  const { datasetId } = useParams<{ datasetId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("domain");

  const analysisQuery = useQuery({
    queryKey: ["analysis", datasetId],
    queryFn: () => getAnalysis(datasetId as string),
    enabled: !!datasetId,
  });

  const deleteMutation = useMutation({
    mutationFn: () => deleteDataset(datasetId as string),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["datasets"] });
      navigate("/datasets");
    },
  });

  if (!datasetId) return null;

  return (
    <div className="min-h-screen">
      <header className="border-b border-graphite-400/20 px-6 py-4 flex items-center justify-between">
        <button onClick={() => navigate("/datasets")} className="flex items-center gap-1.5 text-sm text-graphite-500 hover:text-ink-900 dark:hover:text-canvas-100">
          <ArrowLeft className="w-4 h-4" /> Back to datasets
        </button>
        <button
          onClick={() => { if (confirm("Delete this dataset? This can't be undone from the UI.")) deleteMutation.mutate(); }}
          className="flex items-center gap-1.5 text-sm text-crimson-500 hover:text-crimson-600"
        >
          <Trash2 className="w-4 h-4" /> Delete
        </button>
      </header>

      <div className="max-w-5xl mx-auto px-6 pt-4 flex justify-end">
        <ThemeToggle />
      </div>

      <main className="max-w-5xl mx-auto px-6 py-8">
        {analysisQuery.isLoading ? (
          <p className="text-sm text-graphite-500">Loading…</p>
        ) : analysisQuery.isError ? (
          <p className="text-sm text-crimson-500">Could not load this dataset.</p>
        ) : (
          <>
            <div className="flex items-center gap-4 mb-6">
              <DomainBadge
                domain={analysisQuery.data.top_domain}
                confidence={analysisQuery.data.domain_confidence}
                band={analysisQuery.data.domain_band}
              />
            </div>

            <nav className="flex gap-1 border-b border-graphite-400/20 mb-6">
              {TABS.map((t) => (
                <button
                  key={t.id}
                  onClick={() => setTab(t.id)}
                  className={`px-3 py-2 text-sm font-medium border-b-2 -mb-px transition-colors
                    ${tab === t.id ? "border-signal-500 text-signal-500" : "border-transparent text-graphite-500 hover:text-ink-900 dark:hover:text-canvas-100"}`}
                >
                  {t.label}
                </button>
              ))}
            </nav>

            {tab === "profile" && <ProfileTab datasetId={datasetId} />}
            {tab === "domain" && <DomainTab analysis={analysisQuery.data} />}
            {tab === "prediction" && <PredictionTab datasetId={datasetId} analysis={analysisQuery.data} />}
            {tab === "insights" && <InsightsTab datasetId={datasetId} />}
            {tab === "charts" && <ChartsTab datasetId={datasetId} />}
            {tab === "report" && <ReportDownload datasetId={datasetId} />}
          </>
        )}
      </main>
    </div>
  );
}

function ProfileTab({ datasetId }: { datasetId: string }) {
  const { data, isLoading } = useQuery<DatasetProfile>({
    queryKey: ["profile", datasetId], queryFn: () => getProfile(datasetId),
  });
  if (isLoading) return <p className="text-sm text-graphite-500">Loading profile…</p>;
  if (!data) return null;
  return (
    <div className="space-y-4">
      <div className="flex gap-6 text-sm text-graphite-500">
        <span>{data.n_rows.toLocaleString()} rows</span>
        <span>{data.n_columns} columns</span>
        <span>{data.duplicate_row_pct.toFixed(1)}% duplicate rows</span>
      </div>
      <DatasetPreview profile={data} />
    </div>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function DomainTab({ analysis }: { analysis: any }) {
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
      <ConfidenceGauge score={analysis.domain_confidence} label="Domain confidence" band={analysis.domain_band} />
      <ConfidenceGauge score={analysis.quality.score} label="Data quality" />
      <ConfidenceGauge score={analysis.readiness.score} label="AI readiness" />
      {analysis.model_selection.schema_match_pct != null && (
        <ConfidenceGauge score={analysis.model_selection.schema_match_pct} label="Schema match" />
      )}
    </div>
  );
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function PredictionTab({ datasetId, analysis }: { datasetId: string; analysis: any }) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["predictions", datasetId], queryFn: () => getPredictions(datasetId),
    retry: false,
  });

  if (!analysis.model_selection.selected_model_name) {
    return (
      <div className="rounded-lg border border-amber-500/30 bg-amber-100 dark:bg-amber-500/10 p-4 text-sm">
        {analysis.model_selection.fallback_reason ??
          "No pretrained model was confidently selected for this dataset. Exploratory insights are still available in the Insights tab."}
      </div>
    );
  }
  if (isLoading) return <p className="text-sm text-graphite-500">Loading predictions…</p>;
  if (isError || !data) return <p className="text-sm text-crimson-500">Could not load predictions.</p>;

  return (
    <div>
      <p className="text-sm text-graphite-500 mb-4">
        {data.model_name} v{data.model_version} · predicted {data.n_rows_predicted} rows in{" "}
        {data.inference_time_seconds.toFixed(3)}s
      </p>
      <div className="grid md:grid-cols-2 gap-3">
        {data.local_explanations.slice(0, 20).map((e) => <PredictionCard key={e.row_index} explanation={e} />)}
      </div>
    </div>
  );
}

function InsightsTab({ datasetId }: { datasetId: string }) {
  const insightsQuery = useQuery({ queryKey: ["insights", datasetId], queryFn: () => getInsights(datasetId) });
  const recsQuery = useQuery({ queryKey: ["recommendations", datasetId], queryFn: () => getRecommendations(datasetId) });

  return (
    <div className="space-y-6">
      {insightsQuery.data && (
        <>
          <p className="text-sm">{insightsQuery.data.summary}</p>
          <div className="grid md:grid-cols-2 gap-3">
            {[...insightsQuery.data.key_findings, ...insightsQuery.data.risks,
              ...insightsQuery.data.hidden_trends, ...insightsQuery.data.anomalies].map((insight, i) => (
              <InsightCard key={i} insight={insight} />
            ))}
          </div>
        </>
      )}
      {recsQuery.data && recsQuery.data.length > 0 && (
        <div>
          <h3 className="font-display text-base mb-2">Recommendations</h3>
          <div className="grid md:grid-cols-2 gap-3">
            {recsQuery.data.map((r, i) => <RecommendationCard key={i} recommendation={r} />)}
          </div>
        </div>
      )}
    </div>
  );
}

function ChartsTab({ datasetId }: { datasetId: string }) {
  const { data, isLoading } = useQuery({ queryKey: ["charts", datasetId], queryFn: () => getCharts(datasetId) });
  if (isLoading) return <p className="text-sm text-graphite-500">Loading charts…</p>;
  if (!data?.length) return <p className="text-sm text-graphite-500">No charts available for this dataset.</p>;
  return <ChartsGrid charts={data} />;
}
