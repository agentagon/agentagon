import { OnboardingPage } from "./lifecycle";
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
import {
  AgentInventoryPage,
  AgentPage,
  ConnectorsPage,
  GoalPage,
  GoalsPage,
  HomePage,
  SettingsPage,
  WorkflowsPage,
  IssuesPage,
  MemoryPage,
  TasksPage,
} from "./pages";

export function App() {
  return <Routes><Route path="/" element={<Root />} /><Route path="/projects/:projectId/*" element={<Workspace />} /><Route path="*" element={<Navigate to="/" replace />} /></Routes>;
}

function Root() {
  const projects = useProjects();
  const [addProject, setAddProject] = useState(false);
  if (projects.isLoading) return <div className="splash"><img src="/logo-split-crown-96.png" alt="" /><p>Opening Agentagon…</p></div>;
  if (projects.isError) return <div className="splash"><h1>Agentagon could not open</h1><p>{projects.error.message}</p><Button onClick={() => location.reload()}>Retry</Button></div>;
  if (projects.data?.projects.length) {
    const selected = projects.data.projects.find((item) => item.id === projects.data?.selected_project_id) || projects.data.projects[0];
    return <Navigate to={`/projects/${selected.id}/home`} replace />;
  }
  return <main className="welcome" id="main"><div className="welcome-brand"><img src="/logo-split-crown-96.png" alt="" /><span>agentagon</span></div><div className="welcome-content"><p className="eyebrow">Agent improvement workspace</p><h1>Recursive self-improvement<br />for AI agents.</h1><p>Add a local agent project to begin.</p><Button onClick={() => setAddProject(true)}>Add project</Button></div>{addProject && <AddProjectModal onClose={() => setAddProject(false)} />}</main>;
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
      <Route path="onboarding" element={<OnboardingPage project={project} />} />
      <Route path="agents" element={<AgentInventoryPage projectId={projectId} />} />
      <Route path="agents/:agentId/goals/:goalId" element={<GoalPage projectId={projectId} />} />
      <Route path="agents/:agentId/:tab" element={<AgentPage projectId={projectId} />} />
      <Route path="agents/:agentId" element={<Navigate to="overview" replace />} />
      <Route path="tasks" element={<TasksPage projectId={projectId} />} />
      <Route path="tasks/:taskId" element={<TasksPage projectId={projectId} />} />
      <Route path="goals" element={<GoalsPage projectId={projectId} />} />
      <Route path="issues" element={<IssuesPage projectId={projectId} />} />
      <Route path="memory" element={<MemoryPage projectId={projectId} />} />
      <Route path="workflows" element={<WorkflowsPage projectId={projectId} />} />
      <Route path="connectors" element={<ConnectorsPage projectId={projectId} />} />
      <Route path="settings/:section" element={<SettingsPage projectId={projectId} />} />
      <Route path="settings" element={<Navigate to="assistants" replace />} />
      <Route index element={<Navigate to="home" replace />} />
      <Route path="*" element={<Empty title="Page not found" action={<Button onClick={() => navigate(`/projects/${projectId}/home`)}>Go home</Button>} />} />
    </Routes></main>{task.selected && <TaskPanel projectId={projectId} taskId={task.selected} onClose={task.close} />}</div></div>
    {addProject && <AddProjectModal onClose={() => setAddProject(false)} />}
    {addAgent && <AddAgentModal projectId={projectId} onClose={() => setAddAgent(false)} />}
  </div>;
}
