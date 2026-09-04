"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { getApiErrorMessage, isAbortError } from "@/lib/api/client";

export interface PollingOptions<T> {
  enabled?: boolean;
  intervalMs: number;
  initialData?: T | null;
  errorMessage?: string;
}

export interface PollingResult<T> {
  data: T | null;
  error: string | null;
  isLoading: boolean;
  isRefreshing: boolean;
  lastUpdatedAt: Date | null;
  refresh: () => Promise<void>;
}

export function usePolling<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  {
    enabled = true,
    intervalMs,
    initialData = null,
    errorMessage = "Unable to refresh this view.",
  }: PollingOptions<T>,
): PollingResult<T> {
  const fetcherRef = useRef(fetcher);
  const abortControllerRef = useRef<AbortController | null>(null);
  const inFlightRef = useRef<Promise<void> | null>(null);
  const mountedRef = useRef(false);
  const hasDataRef = useRef(initialData !== null);
  const [data, setData] = useState<T | null>(initialData);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(enabled && initialData === null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<Date | null>(null);

  useEffect(() => {
    fetcherRef.current = fetcher;
  }, [fetcher]);

  const refresh = useCallback(async () => {
    if (!mountedRef.current) {
      return;
    }

    if (inFlightRef.current) {
      return inFlightRef.current;
    }

    const controller = new AbortController();
    abortControllerRef.current = controller;

    if (hasDataRef.current) {
      setIsRefreshing(true);
    } else {
      setIsLoading(true);
    }

    const request = (async () => {
      try {
        const nextData = await fetcherRef.current(controller.signal);
        if (!mountedRef.current || controller.signal.aborted) {
          return;
        }

        hasDataRef.current = true;
        setData(nextData);
        setError(null);
        setLastUpdatedAt(new Date());
      } catch (requestError) {
        if (!mountedRef.current || isAbortError(requestError)) {
          return;
        }
        setError(getApiErrorMessage(requestError, errorMessage));
      } finally {
        const isCurrentRequest = abortControllerRef.current === controller;
        if (isCurrentRequest) {
          abortControllerRef.current = null;
        }
        if (mountedRef.current && isCurrentRequest) {
          setIsLoading(false);
          setIsRefreshing(false);
        }
      }
    })();

    inFlightRef.current = request;
    try {
      await request;
    } finally {
      if (inFlightRef.current === request) {
        inFlightRef.current = null;
      }
    }
  }, [errorMessage]);

  useEffect(() => {
    mountedRef.current = true;
    if (!enabled) {
      return () => {
        mountedRef.current = false;
      };
    }

    void refresh();
    const intervalId = window.setInterval(() => {
      if (document.visibilityState === "visible") {
        void refresh();
      }
    }, Math.max(1_000, intervalMs));

    const handleVisibilityChange = () => {
      if (document.visibilityState === "visible") {
        void refresh();
      }
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);

    return () => {
      mountedRef.current = false;
      window.clearInterval(intervalId);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      abortControllerRef.current?.abort();
      abortControllerRef.current = null;
      inFlightRef.current = null;
    };
  }, [enabled, intervalMs, refresh]);

  return {
    data,
    error,
    isLoading: enabled ? isLoading : false,
    isRefreshing: enabled ? isRefreshing : false,
    lastUpdatedAt,
    refresh,
  };
}
