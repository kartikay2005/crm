// Shown while the demo auto-sign-in is in flight. On a free-tier backend
// that sleeps when idle, the first request can take up to a minute while
// the server wakes — a blank screen for that long reads as "broken".
export function LoadingScreen() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-ink-950 px-4">
      <div className="text-center">
        <div className="mx-auto mb-4 h-8 w-8 animate-spin rounded-full border-2 border-ink-700 border-t-signal-500" />
        <p className="text-sm text-canvas-100">Loading the live demo…</p>
        <p className="mt-1 text-xs text-graphite-400">The demo server sleeps when idle — the first load can take up to a minute.</p>
      </div>
    </div>
  );
}
