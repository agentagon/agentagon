export type Project = {
  id: string;
  name: string;
  path: string;
  branch?: string | null;
  available?: boolean;
  active_tasks?: number;
  source?: {
    kind: "git" | "folder";
    revision?: string | null;
    dirty?: boolean | null;
    measured_work_ready?: boolean;
  };
};

export type Agent = {
  id: string;
  project_id: string;
  name: string;
  responsibility?: string;
  responsibility_inference?: {
    state: "pending" | "inferred" | "edited" | "unavailable" | string;
    reason?: string;
    task_id?: string;
    at?: string;
    source_discovery_key?: string;
  };
  identity_review?: {
    state: "suggested" | "confirmed" | "excluded" | string;
    reason?: string;
    at?: string;
    source?: "user" | "coding_agent_review" | string;
    discovery_key?: string;
  };
  evidence?: Array<{
    kind: string;
    path?: string;
    symbol?: string;
    line?: number;
    call?: string;
    captured_at?: string;
    context?: { start_line: number; end_line: number; source: string };
    provider?: string;
    trace_id?: string;
    snapshot_id?: string;
    matched_on?: string[];
    [key: string]: unknown;
  }>;
  description?: string;
  status: "confirmed" | "suggested" | "archived";
  code_scopes: string[];
  shared_dependencies: string[];
  trace_selector?: Record<string, unknown>;
  revision?: number;
  confidence?: string;
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
  measurement_plan?: MeasurementPlan | null;
  readiness?: Record<string, { ready: boolean; reason: string }>;
};

export type MeasurementMetric = {
  description?: string;
  direction?: string;
  aggregation?: string;
  missing?: string;
  unit?: string;
  weight?: number;
  scale?: number;
  evidence?: string[];
  prerequisites?: string[];
};

export type MeasurementBehavior = {
  id?: string;
  description?: string;
  required?: boolean;
  check?: string;
  metric?: string;
  op?: string;
  bound?: number;
  rubric?: string;
  evidence?: string[];
  prerequisites?: string[];
};

export type MeasurementPlan = {
  id?: string;
  state?: string;
  revision?: number;
  accepted_at?: string;
  background?: string;
  behaviors?: MeasurementBehavior[];
  metrics?: Record<string, MeasurementMetric>;
  scoring?: {
    mode?: string;
    primary?: string;
    custom_metric?: string;
    target?: number;
  };
  evaluation?: {
    mode?: string;
    evaluation_id?: string;
    candidate_id?: string;
    framework?: string;
    command?: string;
    entrypoint?: string;
    dataset_snapshot_id?: string;
    scorer?: string;
    output_mapping?: Record<string, unknown>;
  };
  evidence?: string[];
  limitations?: string[];
};

export type RunCandidate = {
  id: string;
  parent_id?: string | null;
  source_revision?: string | null;
  hypothesis?: string;
  state: string;
  display_state?: string;
  invalidated?: boolean;
  feasible?: boolean;
  metrics?: Record<string, number | null>;
  task_metrics?: Record<string, number | null>;
  variation?: Record<string, number | null>;
  task_variation?: Record<string, number | null>;
  score?: { score?: number | null; target_reached?: boolean; [key: string]: unknown } | null;
  constraints?: Array<{ metric?: string; op?: string; bound?: number; threshold?: number; actual?: number; passed?: boolean | null; reference?: string }>;
  checks?: Array<{ id: string; passed?: boolean | null; issue_ids?: string[] }>;
  review_verdict?: "pass" | "reject" | null;
  branch?: string | null;
};

export type RunDecision = {
  id?: string;
  revision: number;
  decision: "select_candidate" | "keep_current";
  candidate_id?: string | null;
  decided_at?: string;
  operation_id?: string;
  source_revision?: string;
  evaluation_digest?: string;
  current?: boolean;
};

export type RunDecisionOperation = {
  operation_id: string;
  state: "pending" | "stale_pending" | "stale";
  persisted_state: "pending" | "stale";
  expected_revision: number;
  decision: "select_candidate" | "keep_current";
  candidate_id?: string | null;
  current: boolean;
  next_action: "retry_pending_decision" | "reconcile_stale_decision" | "review_and_choose_again";
  reason?: string | null;
  updated_at?: string;
};

export type ResultKind = "audit" | "eval" | "baseline" | "fix" | "optimize" | "patch";

export type ResultTaskContext = {
  id: string;
  workflow: ResultKind;
  title: string;
  state: string;
  result?: Record<string, unknown> | null;
  limits: Record<string, number>;
  next_action?: string | null;
  updated_at?: string;
};

export type AuditFinding = {
  id?: string;
  title?: string;
  summary?: string;
  severity?: string;
  confidence?: number;
  source_references?: Record<string, { path?: string; line?: number; trace_id?: string }>;
};

export type AuditResult = {
  result_kind: "audit";
  audit_id: string;
  goal?: string;
  state: string;
  mode?: string;
  coverage?: Record<string, unknown>;
  code_scope?: string;
  code_scopes?: string[];
  changes?: Array<{ path?: string; old_path?: string; new_path?: string; change_type?: string }>;
  issues: Array<{
    issue_id?: string;
    title?: string;
    summary?: string;
    severity?: string;
    confidence?: number;
    status?: string;
    findings?: AuditFinding[];
    affected_trace_ids?: string[];
    reviewed_trace_denominator?: number;
  }>;
  ungrouped_findings?: AuditFinding[];
  trace_alignment?: { status?: string; warning?: string; mismatched_traces?: number } | null;
  limits?: string[];
  task?: ResultTaskContext | null;
};

export type EvaluationResult = {
  result_kind: "eval";
  evaluation_id: string;
  goal?: string;
  state: string;
  coverage?: Record<string, unknown> | null;
  metrics?: Record<string, MeasurementMetric>;
  review_branch?: string | null;
  review_verdict?: string | null;
  metric_comparisons?: Array<Record<string, unknown>>;
  metric_comparison_error?: string | null;
  trials?: Array<{
    trial_id?: string;
    case_id?: string;
    state?: string;
    repetition?: number;
    outcome?: string;
    error?: string;
    kind?: string;
  }>;
  budget?: Record<string, number>;
  usage?: Record<string, number>;
  deliveries?: Array<{ delivery_id?: string; state?: string; artifact_urls?: Record<string, string> }>;
  allowed_actions?: string[];
  task?: ResultTaskContext | null;
};

export type ScoreSummary = {
  state?: string;
  score?: number | null;
  value?: number | null;
  eligible?: boolean;
  target_reached?: boolean;
  eligible_count?: number;
  measured_count?: number;
  acquisition_count?: number;
  metrics?: Record<string, number | null>;
};

export type BaselineResult = {
  result_kind: "baseline";
  baseline_id: string;
  evaluation_id?: string;
  evaluator_digest?: string;
  execution_run_id?: string;
  source_revision?: string;
  branch?: string;
  profile_name?: string;
  state: string;
  pending_action?: string | null;
  benchmark_score?: number | ScoreSummary | null;
  recent_traces?: {
    state?: string;
    population?: string;
    provider?: string;
    project?: string;
    window?: Record<string, unknown>;
    completeness?: string;
    alignment?: string;
    count?: number;
    score?: ScoreSummary;
    next_action?: string;
  };
  task?: ResultTaskContext | null;
};

export type PatchResult = {
  result_kind: "patch";
  patch_id: string;
  goal?: string;
  state: string;
  source_revision?: string | null;
  editable_paths?: string[];
  checks?: Array<{ id: string; state: string }>;
  independent_review?: string | null;
  measurement?: "unavailable" | string;
  limitations?: string[];
  deliveries?: Array<{ delivery_id?: string; state?: string; artifact_urls?: Record<string, string> }>;
  allowed_actions?: string[];
  task?: ResultTaskContext | null;
};

export type RunResult = {
  result_kind: "fix" | "optimize";
  run_id: string;
  revision: number;
  goal?: string;
  state: string;
  baseline_id?: string | null;
  candidates: RunCandidate[];
  comparisons: {
    baseline?: string | null;
    baseline_score?: number | null;
    alternatives: RunCandidate[];
    result: "verified_improvement" | "baseline_retained" | "final_verification_pending" | string;
  };
  limits?: Record<string, number>;
  usage?: Record<string, number>;
  limitations?: string[];
  selected_candidate_id?: string | null;
  selection?: {
    recommended_candidate_id?: string | null;
    engine_selected_candidate_id?: string | null;
    decision?: RunDecision | null;
    decision_operation?: RunDecisionOperation | null;
    expected_revision?: number;
    allowed_actions?: Array<"select_candidate" | "keep_current" | "prepare_local_delivery" | "retry_pending_decision" | "reconcile_stale_decision">;
  };
  deliveries?: Array<{
    delivery_id?: string;
    state?: string;
    candidate_id?: string;
    source_revision?: string;
    created_at?: string;
    updated_at?: string;
    user_decision_id?: string;
    user_decision_revision?: number;
    artifact_urls?: Record<string, string>;
    [key: string]: unknown;
  }>;
  task?: ResultTaskContext | null;
};

export type StaticWorkflowResult = AuditResult | EvaluationResult | BaselineResult | PatchResult;

export type CandidateEvidence = {
  run_id: string;
  candidate: Record<string, unknown>;
  diff?: {
    text: string;
    truncated?: boolean;
    sealed?: boolean;
    parent_revision?: string;
    source_revision?: string;
  };
  trials?: Array<Record<string, unknown>>;
  review?: Record<string, unknown> | null;
  lesson_context?: Record<string, unknown>;
};

export type DeliveryResult = {
  delivery_id: string;
  state: string;
  artifact_urls?: Record<string, string>;
  candidate_id?: string;
  source_revision?: string;
  user_decision_id?: string;
  user_decision_revision?: number;
  branch?: string;
  summary?: string;
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
  question?: { id: string; kind?: string; prompt?: string; text?: string; command?: string; options?: string[] } | null;
  progress?: unknown;
  result?: Record<string, unknown> | null;
  workflow_ids?: Partial<Record<"audit_id" | "evaluation_id" | "baseline_id" | "run_id" | "patch_id", string>>;
  next_action?: string | null;
  can_resume: boolean;
  can_cancel: boolean;
  revision?: number;
  memory_recording?: { state: "retrying" | "needs_attention" | string; message: string; automatic_retry: boolean; attempts?: number; last_attempt_at?: string } | null;
};

export type WorkflowDefinition = {
  id: string;
  version: number;
  name: string;
  purpose: string;
  workflow: string;
  requires_goal: boolean;
  inputs: string[];
  outputs: string[];
};

export type WorkflowPrerequisite = {
  code: string;
  state: "satisfied" | "missing" | "stale" | string;
  blocking: boolean;
  field?: string;
  evidence_refs: Array<Record<string, unknown>>;
  resolution?: { action: string; context?: Record<string, unknown> };
  context?: Record<string, unknown>;
};

export type WorkflowLimitation = {
  code: string;
  field?: string;
  evidence_refs: Array<Record<string, unknown>>;
  context?: Record<string, unknown>;
};

export type PreparedWorkflowStart = {
  state: "ready" | "ready_with_limits" | "needs_input" | string;
  normalized_intent: {
    workflow: string;
    agent_id?: string;
    input: { type: string; id?: string; text?: string };
    scope?: string[];
    limits?: Record<string, number>;
    options?: Record<string, unknown>;
    assistant?: string;
    model?: string;
    expected_revisions?: Record<string, string>;
  };
  prerequisites: WorkflowPrerequisite[];
  limitations: WorkflowLimitation[];
  expected_outputs: string[];
  revisions: Record<string, string>;
  source_identity?: Record<string, unknown>;
  code_source?: {
    kind?: string;
    revision?: string | null;
    dirty?: boolean | null;
    complete?: boolean;
    fingerprint?: string;
    changed_paths?: number;
    file_count?: number;
  };
};

export type ServiceHealth = {
  application?: string;
  package_version: string;
  build_id: string;
  frontend_asset_version: string;
  service_started_at: string;
  python_version: string;
  state_contracts: Record<string, unknown>;
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
  tasks: Array<Record<string, unknown>>;
  datasets: Array<Record<string, unknown>>;
  traces: Array<Record<string, unknown>>;
  settings: { settings: Record<string, unknown>; profiles: Record<string, unknown> };
};

export type LessonGroup = {
  id: string;
  name: string;
  purpose: string;
  scope: "agent" | "project";
  agent_ids: string[];
  writable: boolean;
};

export type LessonConsideration = {
  task_id: string;
  title: string;
  workflow?: string;
  state?: string;
  agent_id?: string | null;
  updated_at?: string | null;
  supplied: true;
  decision?: "used" | "rejected" | null;
  reason?: string | null;
};

export type LessonVersion = {
  id: string;
  group_id: string;
  key: string;
  statement: string;
  evidence: string[];
  uncertainty: string;
  status: "active" | "outdated";
  revision_kind: "recorded" | "note" | "outdated" | "revised";
  revision_note: string;
  version: number;
  created_at: string;
  group: LessonGroup;
  source: {
    type: "task" | "observation" | "record";
    id: string;
    available: boolean;
    title?: string;
    workflow?: string;
    state?: string;
    agent_id?: string | null;
    outcome?: unknown;
    learning?: Record<string, unknown>;
    window?: Record<string, unknown> | null;
  };
  considerations: LessonConsideration[];
  supplied_count: number;
  used_count: number;
  rejected_count: number;
  last_considered_at?: string | null;
};

export type LessonsResponse = {
  agent_id?: string | null;
  groups: LessonGroup[];
  lessons: LessonVersion[];
  truncated: boolean;
};

export type LessonDetail = {
  agent_id?: string | null;
  group: LessonGroup;
  latest: LessonVersion;
  versions: LessonVersion[];
};
