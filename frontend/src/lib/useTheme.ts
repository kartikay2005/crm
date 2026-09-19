import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "adi_theme";

function getInitialTheme(): "light" | "dark" {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === "light" || stored === "dark") return stored;
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/**
 * Applies/removes the `.dark` class on <html> — this is the class the
 * `@custom-variant dark (&:where(.dark, .dark *));` rule in index.css
 * depends on for every `dark:` utility used throughout the components.
 * Without this hook actually running somewhere in the tree, all those
 * `dark:` classes have no way to ever activate — a real gap caught while
 * reviewing Segment 9's "verify dark mode" step, not something assumed
 * to already work.
 */
export function useTheme() {
  const [theme, setTheme] = useState<"light" | "dark">(getInitialTheme);

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    localStorage.setItem(STORAGE_KEY, theme);
  }, [theme]);

  const toggle = useCallback(() => {
    setTheme((t) => (t === "dark" ? "light" : "dark"));
  }, []);

  return { theme, toggle };
}
