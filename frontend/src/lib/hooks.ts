import { useCallback, useEffect, useRef, useState } from "react";

/** Fetch on mount, expose a manual refresh, and optionally poll. */
export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs = 0,
  deps: unknown[] = [],
): {
  data: T | null;
  error: string | null;
  loading: boolean;
  refresh: () => Promise<void>;
} {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const alive = useRef(true);
  const fetchRef = useRef(fetcher);
  fetchRef.current = fetcher;

  const refresh = useCallback(async () => {
    try {
      const result = await fetchRef.current();
      if (!alive.current) return;
      setData(result);
      setError(null);
    } catch (err) {
      if (!alive.current) return;
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (alive.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void refresh();
    if (intervalMs > 0) {
      const timer = window.setInterval(() => void refresh(), intervalMs);
      return () => {
        alive.current = false;
        window.clearInterval(timer);
      };
    }
    return () => {
      alive.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [intervalMs, refresh, ...deps]);

  return { data, error, loading, refresh };
}

/** Keeps the last N samples of a value, for the live staleness sparkline. */
export function useHistory<T>(value: T | null, limit = 60): { at: number; value: T }[] {
  const [history, setHistory] = useState<{ at: number; value: T }[]>([]);
  useEffect(() => {
    if (value === null || value === undefined) return;
    setHistory((prev) => [...prev, { at: Date.now(), value }].slice(-limit));
  }, [value, limit]);
  return history;
}

export type Theme = "light" | "dark" | "system";

export function useTheme(): [Theme, (theme: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      return (localStorage.getItem("egraph-theme") as Theme) ?? "system";
    } catch {
      return "system";
    }
  });

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("egraph-theme", theme);
    } catch {
      /* private window: the preference just does not persist */
    }
  }, [theme]);

  return [theme, setTheme];
}
