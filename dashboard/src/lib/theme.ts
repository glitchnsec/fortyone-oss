/**
 * Theme management for light/dark mode toggle.
 *
 * Exports:
 * - initTheme() -- applies saved or OS-preferred theme to <html> on first load
 * - useTheme()  -- React hook returning current theme and toggle function
 *
 * The module self-initializes on import so the correct class is on <html>
 * before React renders, preventing a flash of wrong theme.
 */
import { useSyncExternalStore } from "react";

type Theme = "light" | "dark";

const STORAGE_KEY = "theme";

let currentTheme: Theme = "dark";
const listeners = new Set<() => void>();

function applyTheme(theme: Theme) {
  currentTheme = theme;
  const root = document.documentElement;
  if (theme === "dark") {
    root.classList.add("dark");
    root.classList.remove("light");
  } else {
    root.classList.add("light");
    root.classList.remove("dark");
  }
}

export function initTheme() {
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === "light" || stored === "dark") {
    applyTheme(stored);
    return;
  }
  // No stored preference -- check OS preference
  const prefersLight = window.matchMedia("(prefers-color-scheme: light)").matches;
  applyTheme(prefersLight ? "light" : "dark");
}

function toggleTheme() {
  const next: Theme = currentTheme === "dark" ? "light" : "dark";
  applyTheme(next);
  localStorage.setItem(STORAGE_KEY, next);
  listeners.forEach((cb) => cb());
}

function subscribe(cb: () => void) {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
}

function getSnapshot(): Theme {
  return currentTheme;
}

export function useTheme() {
  const theme = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
  return { theme, toggleTheme };
}

// Self-initialize on import -- ensures correct class before first paint
initTheme();
