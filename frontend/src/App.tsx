import { OnboardingPage, ProjectProductionPage } from "./lifecycle";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Link, Navigate, Route, Routes, useLocation, useMatch, useNavigate, useParams } from "react-router-dom";

import {
  AddAgentModal,
  AddProjectModal,
  Button,
  Empty,
  LaunchWorkflowModal,
  Sidebar,
  TaskPanel,
  Topbar,
  useSelectedTask,
} from "./components";
import { useAgents, useProjects, useTasks, useWorkflows } from "./hooks";
import { TracePage } from "./traces";
import { NewWorkFlow, NewWorkMenu, type NewWorkAction } from "./newwork";
import {
  AgentInventoryPage,
  AgentPage,
  GoalPage,
  HomePage,
  SettingsPage,
  IssuesPage,
  IssuePage,
  LessonPage,
  ResultPage,
  TasksPage,
} from "./pages";

export function App() {
  return <Routes><Route path="/" element={<Root />} /><Route path="/projects/:projectId/*" element={<Workspace />} /><Route path="*" element={<Navigate to="/" replace />} /></Routes>;
}

function Root() {
  const projects = useProjects();
  const [addProject, setAddProject] = useState<"local" | "clone" | null>(null);
  if (projects.isLoading) return <div className="splash"><img src="/logo-split-crown-96.png" alt="" /><p>Opening Agentagon…</p></div>;
  if (projects.isError) return <div className="splash"><h1>Agentagon could not open</h1><p>{projects.error.message}</p><Button onClick={() => location.reload()}>Retry</Button></div>;
  if (projects.data?.projects.length) {
    const selected = projects.data.projects.find((item) => item.id === projects.data?.selected_project_id) || projects.data.projects[0];
    return <Navigate to={`/projects/${selected.id}/home`} replace />;
  }
  return <main className="welcome" id="main"><div className="welcome-brand"><img src="/logo-split-crown-96.png" alt="" /><span>agentagon</span></div><div className="welcome-content"><p className="eyebrow">Agent improvement workspace</p><h1>Recursive self-improvement<br />for AI agents.</h1><p>Find problems, verify improvements, and learn from production outcomes.</p><div className="welcome-actions"><Button onClick={() => setAddProject("local")}>Open local folder</Button><Button tone="secondary" onClick={() => setAddProject("clone")}>Clone repository</Button></div><small>Work with any local folder. A GitHub remote is not required.</small></div>{addProject && <AddProjectModal mode={addProject} onClose={() => setAddProject(null)} />}</main>;
}

type WorkflowReturn = { to: string; workflow: string; defaultAgentId?: string; defaultGoalId?: string; defaultInput?: { type: string; id?: string; text?: string } };

function Workspace() {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const agentRoute = useMatch("/projects/:projectId/agents/:agentId/*");
  const queryClient = useQueryClient();
  const projects = useProjects();
  const agents = useAgents(projectId);
  const tasks = useTasks(projectId);
  const workflows = useWorkflows();
  const [returnDraft, setReturnDraft] = useState<WorkflowReturn>();
  const [resumedDraft, setResumedDraft] = useState<WorkflowReturn>();
  const [navOpen, setNavOpen] = useState(false);
  const [addProject, setAddProject] = useState(false);
  const [addAgent, setAddAgent] = useState(false);
  const [newWork, setNewWork] = useState<NewWorkAction>();
  const task = useSelectedTask(projectId);
  const project = projects.data?.projects.find((item) => item.id === projectId);
  const contextAgentId = agentRoute?.params.agentId || new URLSearchParams(location.search).get("agent_id");
  const contextAgent = agents.data?.confirmed.find(item => item.id === contextAgentId);
  useEffect(() => { setNewWork(undefined); setReturnDraft(undefined); setResumedDraft(undefined); }, [projectId]);
  useEffect(() => {
    const context = location.state?.workflowReturn as WorkflowReturn | undefined;
    if (context?.to?.startsWith(`/projects/${projectId}/`) && typeof context.workflow === "string") setReturnDraft(context);
  }, [location.key, projectId]);
  const resumedWorkflow = workflows.data?.workflows.find(item => item.workflow === resumedDraft?.workflow);
  const requestedReturn = new URLSearchParams(location.search).get("return");
  const goalReturn = requestedReturn?.startsWith(`/projects/${projectId}/agents/`) && requestedReturn.includes("/goals/") ? requestedReturn : null;

  useEffect(() => {
    if (!projectId) return;
    const source = new EventSource(`/api/projects/${encodeURIComponent(projectId)}/events`);
    const refresh = () => {
      queryClient.invalidateQueries({ queryKey: ["projects", projectId, "tasks"] });
      queryClient.invalidateQueries({ queryKey: ["projects", projectId] });
    };
    source.addEventListener("update", refresh);
    return () => source.close();
  }, [projectId, queryClient]);

  if (projects.isLoading || agents.isLoading) return <div className="splash"><p>Loading workspace…</p></div>;
  if (projects.isError && !projects.data) return <div className="splash"><h1>Workspace unavailable</h1><p>{projects.error.message}</p><Button onClick={() => projects.refetch()}>Retry</Button></div>;
  if (!project) return <Navigate to="/" replace />;
  const taskAttention = tasks.data?.tasks.filter((item) => item.needs_attention).length || 0;
  const attention = taskAttention;
  return <div className={`app-shell ${task.selected ? "has-task-panel" : ""}`}>
    <Sidebar projects={projects.data?.projects || []} project={project} agents={agents.data?.confirmed || []} open={navOpen} onClose={() => setNavOpen(false)} onAddProject={() => setAddProject(true)} onAddAgent={() => setAddAgent(true)} />
    {navOpen && <button className="nav-scrim" aria-label="Close navigation" onClick={() => setNavOpen(false)} />}
    <div className="workspace-shell"><Topbar project={project} taskCount={attention} onMenu={() => setNavOpen(true)} newWork={<NewWorkMenu projectId={projectId} agentId={contextAgent?.id} agentName={contextAgent?.name} onAction={setNewWork} />} /><div className="work-area"><main id="main" tabIndex={-1} className="main-workspace">{goalReturn && <div className="workflow-return"><span>Your goal is saved.</span><Link className="button button-secondary" to={goalReturn}>Back to goal</Link></div>}{returnDraft && <div className="workflow-return"><span>Return to your workflow when setup is complete.</span><Button tone="secondary" onClick={() => { navigate(returnDraft.to); setResumedDraft(returnDraft); setReturnDraft(undefined); }}>Back to draft</Button></div>}<Routes>
      <Route path="home" element={<HomePage project={project} />} />
      <Route path="production" element={<ProjectProductionPage projectId={projectId} />} />
      <Route path="onboarding" element={<OnboardingPage project={project} />} />
      <Route path="agents" element={<AgentInventoryPage projectId={projectId} />} />
      <Route path="agents/:agentId/goals/:goalId" element={<GoalPage projectId={projectId} />} />
      <Route path="agents/:agentId/:tab" element={<AgentPage projectId={projectId} />} />
      <Route path="agents/:agentId" element={<Navigate to="overview" replace />} />
      <Route path="tasks" element={<TasksPage projectId={projectId} />} />
      <Route path="tasks/:taskId" element={<TasksPage projectId={projectId} />} />
      <Route path="results/:workflow/:runId" element={<ResultPage projectId={projectId} />} />
      <Route path="issues" element={<IssuesPage projectId={projectId} />} />
      <Route path="issues/:issueId" element={<IssuePage projectId={projectId} />} />
      <Route path="traces/:snapshotId" element={<TracePage projectId={projectId} />} />
      <Route path="lessons/:groupId/:entryId" element={<LessonPage projectId={projectId} />} />
      <Route path="settings/:section" element={<SettingsPage projectId={projectId} />} />
      <Route path="settings" element={<Navigate to="project" replace />} />
      <Route index element={<Navigate to="home" replace />} />
      <Route path="*" element={<Empty title="Page not found" action={<Button onClick={() => navigate(`/projects/${projectId}/home`)}>Go home</Button>} />} />
    </Routes></main>{task.selected && <TaskPanel key={task.selected} projectId={projectId} taskId={task.selected} onClose={task.close} />}</div></div>
    {addProject && <AddProjectModal onClose={() => setAddProject(false)} />}
    {addAgent && <AddAgentModal projectId={projectId} onClose={() => setAddAgent(false)} />}
    {resumedDraft && resumedWorkflow && <LaunchWorkflowModal key={`${projectId}:${resumedDraft.to}:${resumedDraft.workflow}`} projectId={projectId} workflow={resumedWorkflow} defaultAgentId={resumedDraft.defaultAgentId} defaultGoalId={resumedDraft.defaultGoalId} defaultInput={resumedDraft.defaultInput} onClose={() => setResumedDraft(undefined)} />}
    {newWork && <NewWorkFlow key={`${projectId}:${contextAgent?.id || "project"}:${newWork}`} projectId={projectId} agentId={contextAgent?.id} action={newWork} onClose={() => setNewWork(undefined)} />}
  </div>;
}
