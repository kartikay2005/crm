import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { LogOut } from "lucide-react";
import { listDatasets, uploadDataset } from "../api/datasets";
import { Upload } from "../components/Upload";
import { ThemeToggle } from "../components/ThemeToggle";
import { useAuth } from "../auth/AuthContext";

const STATUS_LABEL: Record<string, string> = {
  uploaded: "Uploaded", validated: "Validating", cleaned: "Cleaning",
  profiled: "Profiling", analyzed: "Analyzed", predicted: "Ready", failed: "Failed",
};

export function DatasetList() {
  const navigate = useNavigate();
  const { logout } = useAuth();
  const queryClient = useQueryClient();
  const [uploadError, setUploadError] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["datasets"],
    queryFn: () => listDatasets(),
  });

  const uploadMutation = useMutation({
    mutationFn: uploadDataset,
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["datasets"] });
      navigate(`/datasets/${result.dataset_id}`);
    },
    onError: (err: unknown) => {
      const detail = (err as { response?: { data?: { detail?: { message?: string } } } })?.response?.data?.detail;
      setUploadError(detail?.message ?? "Upload failed. Please try again.");
    },
  });

  return (
    <div className="min-h-screen">
      <header className="border-b border-graphite-400/20 px-6 py-4 flex items-center justify-between">
        <h1 className="font-display text-xl">Dataset Intelligence</h1>
        <div className="flex items-center gap-3">
          <ThemeToggle />
          <button onClick={() => logout()} className="flex items-center gap-1.5 text-sm text-graphite-500 hover:text-ink-900 dark:hover:text-canvas-100">
            <LogOut className="w-4 h-4" /> Sign out
          </button>
        </div>
      </header>

      <main className="max-w-4xl mx-auto px-6 py-8">
        <Upload
          onUpload={(file) => { setUploadError(null); uploadMutation.mutate(file); }}
          isUploading={uploadMutation.isPending}
        />
        {uploadError && <p className="mt-2 text-sm text-crimson-500">{uploadError}</p>}

        <h2 className="font-display text-lg mt-10 mb-3">Your datasets</h2>
        {isLoading ? (
          <p className="text-sm text-graphite-500">Loading…</p>
        ) : !data?.items.length ? (
          <p className="text-sm text-graphite-500">No datasets uploaded yet — drop one above to get started.</p>
        ) : (
          <div className="divide-y divide-graphite-400/10 rounded-lg border border-graphite-400/20">
            {data.items.map((d) => (
              <button
                key={d.dataset_id}
                onClick={() => navigate(`/datasets/${d.dataset_id}`)}
                className="w-full flex items-center justify-between px-4 py-3 text-left hover:bg-graphite-400/5"
              >
                <div>
                  <p className="text-sm font-medium">{d.filename}</p>
                  <p className="text-xs text-graphite-500">
                    {d.top_domain ?? "Analyzing…"} · {new Date(d.uploaded_at).toLocaleString()}
                  </p>
                </div>
                <span className="text-xs rounded-full bg-graphite-400/10 px-2 py-1">
                  {STATUS_LABEL[d.status] ?? d.status}
                </span>
              </button>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
