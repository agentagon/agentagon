import { useQuery } from "@tanstack/react-query";

import { api, projectPath } from "./api";
import type {
  Agent,
  Assistant,
  ConnectorType,
  Goal,
  LessonDetail,
  LessonsResponse,
  Project,
  ProjectConnector,
  ProjectOverview,
  WorkflowDefinition,
  TaskDetail,
  TaskSummary,
} from "./types";

export function useProjects() {
  return useQuery({
    queryKey: ["projects"],
    queryFn: ({ signal }) => api<{ projects: Project[]; selected_project_id?: string }>("/api/projects", { signal }),
  });
}

export function useAgents(projectId?: string) {
  return useQuery({
    queryKey: ["projects", projectId, "agents"],
    queryFn: ({ signal }) =>
      api<{ agents: Agent[]; confirmed: Agent[]; suggestions: Agent[]; excluded?: Agent[] }>(projectPath(projectId!, "/agents"), { signal }),
    enabled: Boolean(projectId),
  });
}

export function useAgent(projectId?: string, agentId?: string) {
  return useQuery({
    queryKey: ["projects", projectId, "agents", agentId],
    queryFn: ({ signal }) => api<Agent>(projectPath(projectId!, `/agents/${encodeURIComponent(agentId!)}`), { signal }),
    enabled: Boolean(projectId && agentId),
  });
}

export function useAgentOverview(projectId?: string, agentId?: string) {
  return useQuery({
    queryKey: ["projects", projectId, "agents", agentId, "overview"],
    queryFn: ({ signal }) => api<ProjectOverview & { readiness: Record<string, unknown> }>(projectPath(projectId!, `/agents/${encodeURIComponent(agentId!)}/overview`), { signal }),
    enabled: Boolean(projectId && agentId),
  });
}

export function useGoals(projectId?: string, agentId?: string) {
  return useQuery({
    queryKey: ["projects", projectId, "agents", agentId, "goals"],
    queryFn: ({ signal }) => api<{ goals: Goal[] }>(projectPath(projectId!, `/agents/${encodeURIComponent(agentId!)}/goals`), { signal }),
    enabled: Boolean(projectId && agentId),
  });
}

export function useGoal(projectId?: string, agentId?: string, goalId?: string) {
  return useQuery({
    queryKey: ["projects", projectId, "agents", agentId, "goals", goalId],
    queryFn: ({ signal }) => api<Goal>(projectPath(projectId!, `/agents/${encodeURIComponent(agentId!)}/goals/${encodeURIComponent(goalId!)}`), { signal }),
    enabled: Boolean(projectId && agentId && goalId),
  });
}

export function useTasks(projectId?: string, filters: Record<string, string> = {}) {
  const query = new URLSearchParams(filters).toString();
  return useQuery({
    queryKey: ["projects", projectId, "tasks", filters],
    queryFn: ({ signal }) => api<{ tasks: TaskSummary[]; next_cursor?: string }>(projectPath(projectId!, `/tasks${query ? `?${query}` : ""}`), { signal }),
    enabled: Boolean(projectId),
  });
}

export function useTask(projectId?: string, taskId?: string | null) {
  return useQuery({
    queryKey: ["projects", projectId, "tasks", taskId],
    queryFn: ({ signal }) => api<TaskDetail>(projectPath(projectId!, `/tasks/${encodeURIComponent(taskId!)}`), { signal }),
    enabled: Boolean(projectId && taskId),
  });
}

export function useLessons(projectId?: string, agentId?: string) {
  const query = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
  return useQuery({
    queryKey: ["projects", projectId, "lessons", agentId],
    queryFn: ({ signal }) => api<LessonsResponse>(projectPath(projectId!, `/lessons${query}`), { signal }),
    enabled: Boolean(projectId),
  });
}

export function useLesson(projectId?: string, groupId?: string, entryId?: string, agentId?: string) {
  const query = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
  return useQuery({
    queryKey: ["projects", projectId, "lessons", groupId, entryId, agentId],
    queryFn: ({ signal }) => api<LessonDetail>(projectPath(projectId!, `/lessons/${encodeURIComponent(groupId!)}/${encodeURIComponent(entryId!)}${query}`), { signal }),
    enabled: Boolean(projectId && groupId && entryId),
  });
}

export function useOverview(projectId?: string) {
  return useQuery({
    queryKey: ["projects", projectId, "overview"],
    queryFn: ({ signal }) => api<ProjectOverview>(projectPath(projectId!, "/overview"), { signal }),
    enabled: Boolean(projectId),
  });
}

export function useWorkflows() {
  return useQuery({
    queryKey: ["workflows"],
    queryFn: ({ signal }) => api<{ workflows: WorkflowDefinition[] }>("/api/workflows", { signal }),
  });
}

export function useConnectors(projectId?: string) {
  return useQuery({
    queryKey: ["projects", projectId, "connectors"],
    queryFn: ({ signal }) => api<{ connections: ProjectConnector[] }>(projectPath(projectId!, "/connectors"), { signal }),
    enabled: Boolean(projectId),
  });
}

export function useConnectorTypes() {
  return useQuery({
    queryKey: ["connector-types"],
    queryFn: ({ signal }) => api<{ connector_types: ConnectorType[] }>("/api/connector-types", { signal }),
  });
}

export function useAssistants() {
  return useQuery({
    queryKey: ["assistants"],
    queryFn: ({ signal }) => api<{ assistants: Assistant[]; defaults: Record<string, unknown> }>("/api/assistants", { signal }),
  });
}
