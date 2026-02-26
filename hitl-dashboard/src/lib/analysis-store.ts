/**
 * Client-side analysis state store.
 *
 * Shares the latest analysis result between pages (Upload -> Issues)
 * using React 18 useSyncExternalStore. Data is persisted to
 * sessionStorage so it survives client-side navigation.
 */

import { useSyncExternalStore } from "react";
import type { AnalysisResult } from "./api";

type Listener = () => void;

const SESSION_KEY = "wcag_analysis_result";

let _snapshot: AnalysisResult | null = null;
const _listeners = new Set<Listener>();

/** Try to read from sessionStorage (returns null on failure or SSR). */
function readFromSession(): AnalysisResult | null {
  try {
    if (typeof window === "undefined") return null;
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    return JSON.parse(raw) as AnalysisResult;
  } catch {
    return null;
  }
}

/** Try to write to sessionStorage (silently fails in restricted contexts). */
function writeToSession(value: AnalysisResult | null): void {
  try {
    if (typeof window === "undefined") return;
    if (value === null) {
      sessionStorage.removeItem(SESSION_KEY);
    } else {
      sessionStorage.setItem(SESSION_KEY, JSON.stringify(value));
    }
  } catch {
    // sessionStorage may be unavailable (private browsing, storage quota, etc.)
  }
}

function emitChange() {
  _listeners.forEach((listener) => listener());
}

/** Publish a new analysis result (called from upload page). */
export function publishAnalysis(result: AnalysisResult): void {
  _snapshot = result;
  writeToSession(result);
  emitChange();
}

/** Clear stored analysis. */
export function clearAnalysis(): void {
  _snapshot = null;
  writeToSession(null);
  emitChange();
}

function subscribe(listener: Listener): () => void {
  _listeners.add(listener);
  return () => _listeners.delete(listener);
}

function getSnapshot(): AnalysisResult | null {
  // Hydrate from sessionStorage if memory snapshot is empty
  if (_snapshot === null) {
    _snapshot = readFromSession();
  }
  return _snapshot;
}

function getServerSnapshot(): AnalysisResult | null {
  return null;
}

/** React hook to consume the latest analysis result. */
export function useAnalysisResult(): AnalysisResult | null {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
