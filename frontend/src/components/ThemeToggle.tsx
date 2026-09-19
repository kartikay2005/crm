import { Moon, Sun } from "lucide-react";
import { useTheme } from "../lib/useTheme";

export function ThemeToggle() {
  const { theme, toggle } = useTheme();
  return (
    <button
      onClick={toggle}
      aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      className="rounded p-1.5 text-graphite-500 hover:text-ink-900 dark:hover:text-canvas-100 hover:bg-graphite-400/10"
    >
      {theme === "dark" ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
    </button>
  );
}
