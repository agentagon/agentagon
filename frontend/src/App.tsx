import { OnboardingPage, ProjectProductionPage } from "./lifecycle";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { Navigate, Route, Routes, useNavigate, useParams } from "react-router-dom";

import {
  AddAgentModal,
  AddProjectModal,
  Button,
  Empty,
  Sidebar,
  TaskPanel,
  Topbar,
  useSelectedTask,
} from "./components";
import { useAgents, useProjects, useTasks } from "./hooks";
import { TracePage } from "./traces";
import {
  AgentInventoryPage,
  AgentPage,
  GoalPage,
  GoalsPage,
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

function Workspace() {
  const { projectId = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const projects = useProjects();
  const agents = useAgents(projectId);
  const tasks = useTasks(projectId);
  const [navOpen, setNavOpen] = useState(false);
  const [addProject, setAddProject] = useState(false);
  const [addAgent, setAddAgent] = useState(false);
  const task = useSelectedTask(projectId);
  const project = projects.data?.projects.find((item) => item.id === projectId);

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
  if (!project) return <Navigate to="/" replace />;
  const taskAttention = tasks.data?.tasks.filter((item) => item.needs_attention).length || 0;
  const suggestionCount = agents.data?.suggestions.length || 0;
  const attention = taskAttention + (suggestionCount ? 1 : 0);
  return <div className={`app-shell ${task.selected ? "has-task-panel" : ""}`}>
    <Sidebar projects={projects.data?.projects || []} project={project} agents={agents.data?.confirmed || []} suggestionCount={suggestionCount} open={navOpen} onClose={() => setNavOpen(false)} onAddProject={() => setAddProject(true)} onAddAgent={() => setAddAgent(true)} />
    {navOpen && <button className="nav-scrim" aria-label="Close navigation" onClick={() => setNavOpen(false)} />}
    <div className="workspace-shell"><Topbar project={project} taskCount={attention} onMenu={() => setNavOpen(true)} /><div className="work-area"><main id="main" tabIndex={-1} className="main-workspace"><Routes>
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
      <Route path="goals" element={<GoalsPage projectId={projectId} />} />
      <Route path="issues" element={<IssuesPage projectId={projectId} />} />
      <Route path="issues/:issueId" element={<IssuePage projectId={projectId} />} />
      <Route path="traces/:snapshotId" element={<TracePage projectId={projectId} />} />
      <Route path="lessons/:groupId/:entryId" element={<LessonPage projectId={projectId} />} />
      <Route path="memory" element={<Navigate to={`/projects/${projectId}/settings/data`} replace />} />
      <Route path="workflows" element={<Navigate to={`/projects/${projectId}/settings/execution`} replace />} />
      <Route path="connectors" element={<Navigate to={`/projects/${projectId}/settings/connections`} replace />} />
      <Route path="settings/:section" element={<SettingsPage projectId={projectId} />} />
      <Route path="settings" element={<Navigate to="project" replace />} />
      <Route index element={<Navigate to="home" replace />} />
      <Route path="*" element={<Empty title="Page not found" action={<Button onClick={() => navigate(`/projects/${projectId}/home`)}>Go home</Button>} />} />
    </Routes></main>{task.selected && <TaskPanel projectId={projectId} taskId={task.selected} onClose={task.close} />}</div></div>
    {addProject && <AddProjectModal onClose={() => setAddProject(false)} />}
    {addAgent && <AddAgentModal projectId={projectId} onClose={() => setAddAgent(false)} />}
  </div>;
}
