import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, post, projectPath } from "./api";
import { Button } from "./components";

export type ExecutionProfile = {
  runner: { kind: string; host?: string; remote_root?: string; template?: string; api_key_env?: string; python?: string; independent_capacity?: boolean; sandbox_timeout_seconds?: number };
  limits: Record<string, number>;
  env?: Record<string, string>;
  setup?: string[][];
  [key: string]: unknown;
};
export type ExecutionSettings = { profiles: Record<string, ExecutionProfile>; revision: string };

export function useExecutionSettings(projectId: string) {
  return useQuery({ queryKey: ["projects", projectId, "settings"], queryFn: () => api<ExecutionSettings>(projectPath(projectId, "/settings")) });
}

/** Parse argv without invoking a shell or expanding environment variables. */
export function commandArguments(value: string): string[] {
  if (value.trim().startsWith("[")) {
    const parsed: unknown = JSON.parse(value);
    if (!Array.isArray(parsed) || !parsed.length || !parsed.every(part => typeof part === "string" && part.trim())) throw new Error("An argv array must contain nonempty string arguments.");
    return parsed as string[];
  }
  const result: string[] = [];
  let word = "", quote = "", escaped = false, started = false;
  for (const character of value) {
    if (escaped) { word += character; escaped = false; started = true; }
    else if (character === "\\" && quote !== "'") { escaped = true; started = true; }
    else if (quote) { if (character === quote) quote = ""; else word += character; }
    else if (character === '"' || character === "'") { quote = character; started = true; }
    else if (/\s/.test(character)) { if (started) { result.push(word); word = ""; started = false; } }
    else { word += character; started = true; }
  }
  if (quote || escaped) throw new Error("Finish the quoted argument or trailing escape in the command.");
  if (started) result.push(word);
  if (!result.length) throw new Error("Enter a command.");
  return result;
}

export function displayCommand(argv: string[] = []) {
  if (argv.some(value => /[\n\r\t]/.test(value))) return JSON.stringify(argv);
  return argv.map(value => /^[\w./:@=-]+$/.test(value) ? value : JSON.stringify(value)).join(" ");
}

const initialLimits = { max_candidates: 3, max_trials: 30, max_elapsed_seconds: 1800, parallel_candidates: 1, parallel_trials: 1, trial_timeout_seconds: 120 };
const limitLabels: Record<string, string> = { max_candidates: "Candidate limit", max_trials: "Trial limit", max_elapsed_seconds: "Active time allowance (seconds)", parallel_candidates: "Parallel candidates", parallel_trials: "Parallel trials", trial_timeout_seconds: "Trial timeout (seconds)" };

export function ExecutionProfileEditor({ projectId, settings, profileName, onSaved, onCancel }: { projectId: string; settings: ExecutionSettings; profileName?: string; onSaved: (name: string) => void; onCancel: () => void }) {
  const cache = useQueryClient();
  const original = profileName ? settings.profiles[profileName] : undefined;
  const [name, setName] = useState(profileName || "local");
  const [kind, setKind] = useState(original?.runner.kind || "local");
  const [host, setHost] = useState(original?.runner.host || "");
  const [remoteRoot, setRemoteRoot] = useState(original?.runner.remote_root || "");
  const [template, setTemplate] = useState(original?.runner.template || "");
  const [keyReference, setKeyReference] = useState(original?.runner.api_key_env || "E2B_API_KEY");
  const [python, setPython] = useState(original?.runner.python || "");
  const [independent, setIndependent] = useState(original?.runner.independent_capacity || false);
  const [limits, setLimits] = useState<Record<string, number>>({ ...initialLimits, ...original?.limits });
  const [setup, setSetup] = useState((original?.setup || []).map(displayCommand).join("\n"));
  const [environment, setEnvironment] = useState(Object.entries(original?.env || {}).map(([target, source]) => `${target}=${source}`).join("\n"));
  // Bind edits to the revision originally displayed, even if a background query refreshes.
  const [revision] = useState(settings.revision);
  const save = useMutation({
    mutationFn: () => {
      if (!/^[a-z][a-z0-9_-]{0,63}$/.test(name)) throw new Error("Use a lowercase profile name starting with a letter, followed by letters, digits, - or _.");
      if (!profileName && settings.profiles[name]) throw new Error("That name already exists. Edit its profile or choose a different name.");
      const env: Record<string, string> = {};
      for (const row of environment.split("\n").filter(row => row.trim())) {
        const match = row.trim().match(/^([A-Z_][A-Z0-9_]*)\s*=\s*([A-Z_][A-Z0-9_]*)$/);
        if (!match) throw new Error("Environment entries must be NAME=SOURCE_ENV_NAME, never secret values.");
        if (match[1] in env) throw new Error(`Duplicate environment name: ${match[1]}`);
        env[match[1]] = match[2];
      }
      const runner: ExecutionProfile["runner"] = { kind, ...(python.trim() ? { python: python.trim() } : {}), independent_capacity: independent, ...(kind === original?.runner.kind && original.runner.sandbox_timeout_seconds ? { sandbox_timeout_seconds: original.runner.sandbox_timeout_seconds } : {}) };
      if (kind === "ssh") Object.assign(runner, { host: host.trim(), remote_root: remoteRoot.trim() });
      if (kind === "e2b") Object.assign(runner, { template: template.trim(), api_key_env: keyReference.trim(), sandbox_timeout_seconds: Math.max(original?.runner.sandbox_timeout_seconds || 0, limits.trial_timeout_seconds + 60) });
      return post(projectPath(projectId, "/settings"), { scope: "project", expected_revision: revision, profile_name: name, profile: { ...original, runner, limits, env, setup: setup.split("\n").filter(line => line.trim()).map(commandArguments) } });
    },
    onSuccess: async () => { await cache.invalidateQueries({ queryKey: ["projects", projectId] }); onSaved(name); },
  });
  return <section className="settings-card form execution-profile-editor" aria-label={profileName ? "Edit execution profile" : "Configure runner"}>
    <h3>{profileName ? "Edit execution profile" : "Configure runner"}</h3>
    <p className="quiet-copy">Saving validates configuration only. Commands run later when you explicitly start a workflow.</p>
    <div className="form-grid"><label>Profile name<input value={name} disabled={Boolean(profileName)} onChange={event => setName(event.target.value)} autoFocus /></label><label>Runner<select value={kind} onChange={event => setKind(event.target.value)}><option value="local">Local computer</option><option value="ssh">SSH host</option><option value="e2b">E2B sandbox</option></select></label></div>
    {kind === "ssh" && <div className="form-grid"><label>SSH host alias<input value={host} onChange={event => setHost(event.target.value)} placeholder="Configured SSH alias" /></label><label>Remote workspace root<input value={remoteRoot} onChange={event => setRemoteRoot(event.target.value)} placeholder="/home/user/agentagon-runs" /></label></div>}
    {kind === "e2b" && <div className="form-grid"><label>Sandbox template<input value={template} onChange={event => setTemplate(event.target.value)} /></label><label>API key environment variable<input value={keyReference} onChange={event => setKeyReference(event.target.value)} autoComplete="off" /></label></div>}
    <p className="quiet-surface">{kind === "local" ? "Commands execute on this computer in Agentagon-managed work areas." : kind === "ssh" ? "Commands execute on the configured SSH host. Saving does not connect to it." : "An authorized run creates a remote sandbox. Saving does not contact E2B."} Application commands may contact their own external services; review them before running.</p>
    <div className="form-grid">{["max_elapsed_seconds", "trial_timeout_seconds"].map(key => <label key={key}>{limitLabels[key]}<input type="number" min={1} value={limits[key]} onChange={event => setLimits({ ...limits, [key]: Number(event.target.value) })} /></label>)}</div>
    <details><summary>Setup, environment and concurrency</summary><div className="form-grid"><label>Python executable <span className="optional">Optional</span><input value={python} onChange={event => setPython(event.target.value)} placeholder="Runner default" /></label>{["max_candidates", "max_trials", "parallel_candidates", "parallel_trials"].map(key => <label key={key}>{limitLabels[key]}<input type="number" min={1} value={limits[key]} onChange={event => setLimits({ ...limits, [key]: Number(event.target.value) })} /></label>)}</div><label className="checkbox-label"><input type="checkbox" checked={independent} onChange={event => setIndependent(event.target.checked)} /><span>The runner has independent capacity for parallel measurements.</span></label><label>Setup commands <span className="optional">One per line</span><textarea value={setup} onChange={event => setSetup(event.target.value)} placeholder="python -m pip install -e ." rows={3} /><small>Quoted arguments are supported. Shell pipes, redirects and environment expansion are not interpreted.</small></label><label>Environment references <span className="optional">One per line</span><textarea value={environment} onChange={event => setEnvironment(event.target.value)} placeholder="OPENAI_API_KEY=MY_PROJECT_OPENAI_KEY" rows={3} /><small>Names of environment variables only. Do not enter credentials.</small></label></details>
    {save.error && <p className="error-banner" role="alert">{save.error.message}</p>}
    <footer className="form-actions"><Button type="button" tone="secondary" onClick={onCancel} disabled={save.isPending}>Cancel</Button><Button type="button" onClick={() => save.mutate()} disabled={save.isPending}>{save.isPending ? "Saving…" : "Save profile"}</Button></footer>
  </section>;
}

export function ExecutionSettingsPanel({ projectId }: { projectId: string }) {
  const settings = useExecutionSettings(projectId);
  const [editing, setEditing] = useState<string | null>();
  const [saved, setSaved] = useState("");
  if (settings.isLoading) return <p role="status">Loading execution profiles…</p>;
  if (!settings.data) return <div className="error-banner" role="alert">{settings.error?.message || "Execution settings are unavailable."}<Button onClick={() => settings.refetch()}>Retry</Button></div>;
  return <section className="settings-section"><div className="section-heading"><div><h2>Execution profiles</h2><p className="quiet-copy">Choose where bounded evaluation commands run. Existing runs keep their frozen settings.</p></div>{editing === undefined && <Button onClick={() => { setEditing(null); setSaved(""); }}>Configure runner</Button>}</div>{editing !== undefined ? <ExecutionProfileEditor key={editing ?? "new"} projectId={projectId} settings={settings.data} profileName={editing ?? undefined} onSaved={name => { setSaved(`${name} saved. No commands were executed.`); setEditing(undefined); }} onCancel={() => setEditing(undefined)} /> : Object.keys(settings.data.profiles).length ? <div className="list-surface">{Object.entries(settings.data.profiles).map(([name, profile]) => <div className="list-row" key={name}><div><strong>{name}</strong><span>{profile.runner.kind} · {profile.limits.max_elapsed_seconds / 60} minute allowance</span></div><Button tone="secondary" onClick={() => { setEditing(name); setSaved(""); }}>Edit profile</Button></div>)}</div> : <p className="quiet-surface">No runner configured. Add a profile to prepare measured work.</p>}{saved && <p role="status">{saved}</p>}</section>;
}
