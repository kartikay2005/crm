import { useCallback, useRef, useState } from "react";
import { UploadCloud, FileWarning } from "lucide-react";

const ALLOWED_EXTENSIONS = [".csv", ".xlsx", ".xls", ".tsv", ".json"];
const MAX_UPLOAD_MB = 200; // mirrors core/config.py's dataset_max_upload_mb default;
// a real deployment should fetch this from the backend rather than hardcode it
// twice (see IMPLEMENTATION_PLAN.md Segment 9's note on this) — flagged here
// rather than silently drifting from the server-side limit.

interface UploadProps {
  onUpload: (file: File) => void;
  isUploading: boolean;
}

export function Upload({ onUpload, isUploading }: UploadProps) {
  const [isDragging, setIsDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const validateAndUpload = useCallback((file: File) => {
    const ext = "." + file.name.split(".").pop()?.toLowerCase();
    if (!ALLOWED_EXTENSIONS.includes(ext)) {
      setError(`"${ext}" isn't supported. Try: ${ALLOWED_EXTENSIONS.join(", ")}`);
      return;
    }
    if (file.size > MAX_UPLOAD_MB * 1024 * 1024) {
      setError(`File is ${(file.size / 1_048_576).toFixed(1)} MB, over the ${MAX_UPLOAD_MB} MB limit.`);
      return;
    }
    setError(null);
    onUpload(file);
  }, [onUpload]);

  return (
    <div>
      <div
        onDragOver={(e) => { e.preventDefault(); setIsDragging(true); }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setIsDragging(false);
          const file = e.dataTransfer.files[0];
          if (file) validateAndUpload(file);
        }}
        onClick={() => inputRef.current?.click()}
        className={`flex flex-col items-center justify-center gap-3 rounded-lg border-2 border-dashed p-12 cursor-pointer transition-colors
          ${isDragging ? "border-signal-500 bg-signal-100 dark:bg-signal-500/10" : "border-graphite-400/40 hover:border-graphite-400"}`}
      >
        <UploadCloud className="w-8 h-8 text-graphite-500" strokeWidth={1.5} />
        <div className="text-center">
          <p className="font-medium text-sm">
            {isUploading ? "Uploading and analyzing…" : "Drop a dataset here, or click to browse"}
          </p>
          <p className="text-xs text-graphite-500 mt-1">
            CSV, XLSX, XLS, TSV, or JSON — up to {MAX_UPLOAD_MB} MB
          </p>
        </div>
        <input
          ref={inputRef} type="file" className="hidden" accept={ALLOWED_EXTENSIONS.join(",")}
          onChange={(e) => { const f = e.target.files?.[0]; if (f) validateAndUpload(f); }}
          disabled={isUploading}
        />
      </div>
      {error && (
        <div className="mt-2 flex items-center gap-2 text-sm text-crimson-500">
          <FileWarning className="w-4 h-4" /> {error}
        </div>
      )}
    </div>
  );
}
