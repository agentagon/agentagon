export type Project = {
  id: string;
  name: string;
  path: string;
  branch?: string | null;
  available?: boolean;
  active_jobs?: number;
};

export type Agent = {
  id: string;
  project_id: string;
  name: string;
  responsibility?: string;
  description?: string;
  status: "confirmed" | "suggested";
  code_scopes: string[];
  shared_dependencies: string[];
  trace_selector?: Record<string, unknown>;
  revision?: number;
};

export type Goal = {
  id: string;
  project_id: string;
  agent_id: string;
  name: string;
  category: string;
  objective: string;
  ideal_behavior?: string | null;
  state: string;
  measurement?: { evaluation_id?: string; baseline_id?: string } | null;
  measurement_plan?: { state?: string; revision?: number } | null;
  readiness?: Record<string, { ready: boolean; reason: string }>;
};

export type TaskSummary = {
  id: string;
  project_id: string;
  workflow: string;
  workflow_version: number;
  agent_id?: string | null;
  agent_name?: string | null;
  goal_id?: string | null;
  goal_name?: string | null;
  title: string;
  state: string;
  needs_attention: boolean;
  created_at?: string;
  updated_at?: string;
};

export type TaskDetail = TaskSummary & {
  conversation: Array<{ role?: string; text?: string; content?: string }>;
  events: Array<{ type?: string; text?: string; created_at?: string }>;
  question?: { id: string; prompt?: string; text?: string; options?: string[] } | null;
  progress?: unknown;
  result?: Record<string, unknown> | null;
  next_action?: string | null;
  can_resume: boolean;
  can_cancel: boolean;
  revision?: number;
};

export type SkillDefinition = {
  id: string;
  version: number;
  name: string;
  purpose: string;
  workflow: string;
  requires_goal: boolean;
  inputs: string[];
  outputs: string[];
};

export type WorkflowReadiness = {
  workflow: string;
  workflow_version: number;
  ready: boolean;
  blockers: Array<{ code: string; message: string; action: string }>;
  next_action: string;
};

export type ConnectorType = {
  id: string;
  name: string;
  capabilities: string[];
  default_endpoint: string;
};

export type ProjectConnector = {
  id: string;
  project_id: string;
  provider: string;
  name?: string;
  project?: string;
  project_name?: string;
  endpoint?: string;
  status: string;
  last_checked_at?: string;
  credential_mode?: string;
};

export type Assistant = {
  id: "codex" | "claude";
  name: string;
  available: boolean;
  authenticated?: boolean;
  version?: string;
  message?: string;
};

export type ProjectOverview = {
  project: Project;
  audits: Array<Record<string, unknown>>;
  evaluations: Array<Record<string, unknown>>;
  runs: Array<Record<string, unknown>>;
  baselines: Array<Record<string, unknown>>;
  issues: Array<Record<string, unknown>>;
  jobs: Array<Record<string, unknown>>;
  datasets: Array<Record<string, unknown>>;
  traces: Array<Record<string, unknown>>;
  settings: { settings: Record<string, unknown>; profiles: Record<string, unknown> };
};
