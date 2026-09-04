"use client";

import Link from "next/link";
import { useCallback, useMemo } from "react";

import { usePolling } from "@/hooks/use-polling";
import { getGlobalAnalytics } from "@/lib/api/analytics";
import {
  BUSINESS_ACTION_NAMES,
  type BusinessActionName,
} from "@/lib/api/types";
import {
  formatBusinessActionName,
  formatCount,
  formatDateTime,
  formatDuration,
  formatPercent,
} from "@/lib/format";

const POLL_INTERVAL_MS = 10_000;

export function OverviewWorkspace() {
  const fetchAnalytics = useCallback(
    (signal: AbortSignal) => getGlobalAnalytics({ signal }),
    [],
  );
  const {
    data: analytics,
    error,
    isLoading,
    isRefreshing,
    lastUpdatedAt,
    refresh,
  } = usePolling(fetchAnalytics, {
    intervalMs: POLL_INTERVAL_MS,
    errorMessage: "Analytics could not be refreshed.",
  });
  const metricFallback = isLoading ? "Loading" : "Unavailable";
  const metricFallbackClass = isLoading ? "metric-loading" : "metric-unavailable";

  const actionRows = useMemo(() => {
    const counts = BUSINESS_ACTION_NAMES.map((action) =>
      analytics ? analytics.action_distribution[action] : null,
    );
    const maximum = Math.max(1, ...counts.map((count) => count ?? 0));

    return BUSINESS_ACTION_NAMES.map((action, index) => ({
      action,
      count: counts[index],
      scale: (counts[index] ?? 0) / maximum,
    }));
  }, [analytics]);

  return (
    <div
      aria-busy={isLoading}
      className={`overview-page comp-frame ${analytics ? "overview-has-data" : ""}`}
    >
      <header className="overview-toolbar r-top-bar">
        <h1 className="r-page-title">Operational overview</h1>
        <p className="r-refresh-time">
          {lastUpdatedAt
            ? `Last refreshed: ${formatDateTime(lastUpdatedAt.toISOString())}`
            : isLoading
              ? "Loading live analytics"
              : "Live analytics not available"}
          {isRefreshing ? " · Refreshing" : ""}
        </p>
      </header>

      <p className="overview-purpose r-purpose-line">
        Durable AI supervision for long-running order operations.
      </p>

      <div className="overview-actions">
        <Link className="button button-primary r-configure-button" href="/supervisors">
          Configure supervisor
        </Link>
        <Link className="button button-secondary r-runs-button" href="/runs">
          View runs
        </Link>
      </div>

      {error ? (
        <div className="overview-api-state" role="alert">
          <span>{error}</span>
          <button className="text-button" onClick={() => void refresh()} type="button">
            Retry
          </button>
        </div>
      ) : null}

      <section className="metric-panel total-panel r-total-panel" aria-labelledby="total-label">
        <h2 className="metric-label r-total-label" id="total-label">Total runs</h2>
        <p className={`metric-value r-total-value ${analytics ? "" : metricFallbackClass}`}>
          {analytics ? formatCount(analytics.runs.total) : metricFallback}
        </p>
        <p className="metric-unit r-total-unit">runs</p>
      </section>

      <section className="metric-panel outcomes-panel r-outcomes-panel" aria-labelledby="outcomes-label">
        <h2 className="metric-label r-outcomes-label" id="outcomes-label">Run outcomes</h2>
        <div className="outcome-columns">
          <OutcomeMetric isLoading={isLoading} label="Active" value={analytics?.runs.active} tone="positive" />
          <OutcomeMetric isLoading={isLoading} label="Completed" value={analytics?.runs.completed} tone="positive" />
          <OutcomeMetric isLoading={isLoading} label="Terminated" value={analytics?.runs.terminated} tone="danger" />
        </div>
      </section>

      <section className="metric-panel suppression-panel r-suppression-panel" aria-labelledby="suppression-label">
        <h2 className="metric-label r-suppression-label" id="suppression-label">Wake suppression rate</h2>
        <p className={`metric-value r-suppression-value ${analytics?.wake_suppression_rate == null ? (analytics ? "metric-unavailable" : metricFallbackClass) : ""}`}>
          {analytics
            ? formatPercent(analytics.wake_suppression_rate)
            : metricFallback}
        </p>
        <p className="metric-unit r-suppression-unit">suppressed</p>
      </section>

      <section className="metric-panel actions-panel r-actions-panel" aria-labelledby="actions-label">
        <h2 className="metric-label r-actions-label" id="actions-label">Business actions executed</h2>
        <p className={`metric-value r-actions-value ${analytics ? "" : metricFallbackClass}`}>
          {analytics ? formatCount(analytics.business_actions_executed) : metricFallback}
        </p>
        <p className="metric-unit r-actions-unit">actions</p>
      </section>

      <section className="metric-panel intervention-panel r-intervention-panel" aria-labelledby="intervention-label">
        <h2 className="metric-label r-intervention-label" id="intervention-label">Average time to first intervention</h2>
        <p className={`metric-value r-intervention-value ${analytics?.average_time_to_first_intervention_seconds == null ? (analytics ? "metric-unavailable" : metricFallbackClass) : ""}`}>
          {analytics
            ? formatDuration(analytics.average_time_to_first_intervention_seconds)
            : metricFallback}
        </p>
        <p className="metric-unit r-intervention-unit">elapsed</p>
      </section>

      <section className="distribution-panel" aria-labelledby="distribution-title">
        <div className="distribution-title-row r-distribution-title-row">
          <h2 className="r-distribution-title" id="distribution-title">Action distribution</h2>
        </div>
        <div className="distribution-header-row r-distribution-header-row" aria-hidden="true">
          <span className="r-action-column-label">Action</span>
          <span className="r-count-column-label">Count</span>
        </div>
        <dl className="distribution-body" aria-label="Business action execution counts">
          {actionRows.map((row) => (
            <ActionRow {...row} loading={isLoading} key={row.action} />
          ))}
        </dl>
      </section>
    </div>
  );
}

function OutcomeMetric({
  isLoading,
  label,
  value,
  tone,
}: {
  isLoading: boolean;
  label: string;
  value: number | undefined;
  tone: "positive" | "danger";
}) {
  return (
    <div className="outcome-item">
      <span className={`semantic-text ${tone}`}>{label}</span>
      <strong
        className={`${value === undefined ? "outcome-unavailable" : ""} ${isLoading ? "metric-loading" : ""}`.trim() || undefined}
      >
        {value === undefined ? (isLoading ? "Loading" : "Unavailable") : formatCount(value)}
      </strong>
      <span>runs</span>
    </div>
  );
}

function ActionRow({
  action,
  count,
  scale,
  loading,
}: {
  action: BusinessActionName;
  count: number | null;
  scale: number;
  loading: boolean;
}) {
  return (
    <div className="distribution-row">
      <dt className="distribution-label">{formatBusinessActionName(action)}</dt>
      <dd className="distribution-count">
        {count === null ? (loading ? "Loading" : "Unavailable") : formatCount(count)}
      </dd>
      <span className={`distribution-track ${loading ? "metric-loading" : ""}`} aria-hidden="true">
        <span className="distribution-fill" style={{ transform: `scaleX(${scale})` }} />
      </span>
    </div>
  );
}
