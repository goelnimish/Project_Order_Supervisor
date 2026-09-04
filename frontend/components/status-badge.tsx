import type { WorkflowStatus } from "@/lib/api/types";
import { humanizeIdentifier } from "@/lib/format";
import {
  getWorkflowStatusTone,
  type WorkflowStatusTone,
} from "@/lib/run-status";

const TONE_CLASSES: Record<WorkflowStatusTone, string> = {
  neutral:
    "border-[var(--line)] bg-[var(--surface-strong)] text-[var(--muted)]",
  info:
    "border-[color:color-mix(in_srgb,var(--accent)_48%,transparent)] bg-[color:color-mix(in_srgb,var(--accent)_13%,transparent)] text-[var(--accent)]",
  success:
    "border-[color:color-mix(in_srgb,var(--positive)_45%,transparent)] bg-[color:color-mix(in_srgb,var(--positive)_12%,transparent)] text-[#8ee39b]",
  warning:
    "border-[color:color-mix(in_srgb,var(--warning)_46%,transparent)] bg-[color:color-mix(in_srgb,var(--warning)_12%,transparent)] text-[#f3cc83]",
  danger:
    "border-[color:color-mix(in_srgb,var(--danger)_48%,transparent)] bg-[color:color-mix(in_srgb,var(--danger)_12%,transparent)] text-[#ff9d92]",
};

const DOT_CLASSES: Record<WorkflowStatusTone, string> = {
  neutral: "bg-[var(--quiet)]",
  info: "bg-[var(--accent)]",
  success: "bg-[var(--positive)]",
  warning: "bg-[var(--warning)]",
  danger: "bg-[var(--danger)]",
};

export function StatusBadge({ status }: { status: WorkflowStatus }) {
  const tone = getWorkflowStatusTone(status);

  return (
    <span
      className={`inline-flex min-h-7 items-center gap-2 rounded-full border px-2.5 py-1 text-xs font-semibold leading-none ${TONE_CLASSES[tone]}`}
      data-status={status}
    >
      <span
        aria-hidden="true"
        className={`size-1.5 shrink-0 rounded-full ${DOT_CLASSES[tone]}`}
      />
      {humanizeIdentifier(status)}
    </span>
  );
}
