import type { Metadata } from "next";

import { RunDetailWorkspace } from "@/components/run-detail-workspace";

export const metadata: Metadata = {
  title: "Run detail",
  description: "Inspect and control one durable order supervision run.",
};

export default async function RunDetailPage({
  params,
}: {
  params: Promise<{ run_id: string }>;
}) {
  const { run_id: runId } = await params;

  return <RunDetailWorkspace key={runId} runId={runId} />;
}
