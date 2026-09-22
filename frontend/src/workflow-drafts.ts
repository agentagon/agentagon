import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api, post, projectPath } from "./api";
import { loadOperationDraft, operationBinding, type OperationDraft } from "./session";

export type WorkflowDraft = { agentId?: string; goalId?: string; problem?: string; expected?: string; helpDefine?: boolean; traceId?: string; profile?: string };
export type SavedWorkflowDraft = OperationDraft<WorkflowDraft> & { workflow?: string };
export type DraftResponse = { draft: SavedWorkflowDraft | null; revision: number };

export function workflowDraftKey(projectId: string, workflow: string, agentId?: string, goalId?: string, input?: { type: string; id?: string }) {
  return `agentagon.workflow-draft:${projectId}:${workflow}:${agentId || "any"}:${goalId || "any"}:${input?.type || "new"}:${input?.id || "none"}`;
}

function draftRoute(projectId: string, key: string) {
  return projectPath(projectId, `/drafts/${encodeURIComponent(operationBinding(key))}`);
}

export function useWorkflowDraft(projectId: string, key: string) {
  const local = useRef(loadOperationDraft<WorkflowDraft>(key));
  const query = useQuery({ queryKey: ["projects", projectId, "drafts", key], queryFn: () => api<DraftResponse>(draftRoute(projectId, key)), staleTime: 0, refetchOnMount: "always" });
  return { local: local.current, query };
}

/** Serialize draft saves and never overwrite another client's revision. */
export function useSaveWorkflowDraft(projectId: string, key: string, initial: DraftResponse, draft: SavedWorkflowDraft) {
  const route = draftRoute(projectId, key);
  const [revision, setRevision] = useState(initial.revision);
  const [saved, setSaved] = useState(JSON.stringify(initial.draft));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string>();
  const current = JSON.stringify(draft);
  const dirty = current !== saved;
  useEffect(() => {
    if (!dirty || saving || error) return;
    const timer = window.setTimeout(() => {
      setSaving(true);
      post<DraftResponse>(route, { expected_revision: revision, draft: JSON.parse(current) })
        .then(result => { setRevision(result.revision); setSaved(current); })
        .catch((failure: Error) => setError(failure.message))
        .finally(() => setSaving(false));
    }, 300);
    return () => window.clearTimeout(timer);
  }, [current, dirty, error, revision, route, saving]);
  return {
    pending: dirty || saving,
    error,
    retry: () => setError(undefined),
    clear: () => post(route + "/clear", { expected_revision: revision }),
  };
}
