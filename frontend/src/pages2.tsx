import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { t } from "./i18n";
import type { Ctx, Device } from "./pages";

type PP = { c: Ctx };
export type Project = { id: string; device_id: string; name: string; root: string; stack: string; build_cmd: string[] | null; test_cmd: string[] | null; deploy_cmd: string[] | null };
export type Step = { id: string; type: string; title: string; risk: string; status: string; auto: boolean; production?: boolean; result?: { ok: boolean; verified?: boolean; detail: string } };
export type Task = { id: string; device_id: string; project_id?: string; kind: string; status: string; mode: string | null; mode_reason: string; instruction: string; steps: Step[]; events: { ts: number; level: string; msg: string }[]; event_count?: number; changes: { path: string; status: string; diff: string }[]; created_at: number; report?: string; target: { name: string; os: string } };

function useAsync() {
  const [err, setErr] = useState(""); const [busy, setBusy] = useState(false);
  const go = useCallback(async <T,>(f: () => Promise<T>): Promise<T | undefined> => { setBusy(true); setErr(""); try { return await f(); } catch (e: any) { setErr(e.message); } finally { setBusy(false); } }, []);
  return { err, busy, go };
}
const Err = ({ m }: { m: string }) => (m ? <div className="err" role="alert">{m}</div> : null);
const NoDev = () => <div className="card">{t("none")}</div>;
const ST: Record<string, string> = { tested: "ok", untested: "bad", mock: "info", planned: "info", blocked: "bad" };
export const Badge = ({ s }: { s: string }) => <span className={"pill " + (ST[s] ?? "")}>{s}</span>;

// ------------------------------------------------------------------ Quick Connect wizard
export function QuickConnect({ c }: PP) {
  const { err, busy, go } = useAsync();
  const [methods, setMethods] = useState<any[]>([]); const [caps, setCaps] = useState<any>({}); const [pick, setPick] = useState("usb_agent");
  const [addr, setAddr] = useState(""); const [probe, setProbe] = useState<any>(null);
  const [f, setF] = useState({ name: "", owner_name: "", owner_contact: "", authorization_note: "" }); const [code, setCode] = useState(""); const [fp, setFp] = useState("");
  const [created, setCreated] = useState<Device | null>(null); const [test, setTest] = useState<any>(null);
  const [gitUrl, setGitUrl] = useState(""); const [gitRes, setGitRes] = useState<any>(null); const [ts, setTs] = useState<any>(null); const [rd, setRd] = useState(""); const [rdRes, setRdRes] = useState<any>(null);
  useEffect(() => { api("/connections/methods").then(setMethods).catch(() => {}); api("/connections/local").then(setCaps).catch(() => {}); }, []);
  const m = methods.find(x => x.id === pick);
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const agentish = ["usb_agent", "ethernet", "lan", "tailscale"].includes(pick);
  return <>
    <Err m={err} />
    <div className="grid">{methods.map(x => <button key={x.id} className={"card " + (pick === x.id ? "sel" : "")} style={{ textAlign: "start", cursor: "pointer" }} onClick={() => setPick(x.id)}><h3>{x.title}</h3><div className="tag">{x.kind}</div></button>)}</div>
    {m && <div className="card mt"><h3>{m.title} — setup</h3><ol>{m.steps.map((s: string, i: number) => <li key={i}>{s}</li>)}</ol>
      <div className="tag">This computer: {Object.entries(caps).map(([k, v]) => `${k}:${v ? "✔" : "✖"}`).join("  ")}</div></div>}
    {agentish && <div className="card mt"><h3>1 · Find the client</h3><div className="row"><input style={{ flex: 1 }} dir="ltr" placeholder="169.254.10.2 or 192.168.1.20 or 100.x.y.z (optional :port)" value={addr} onChange={e => setAddr(e.target.value)} />
      <button className="btn" disabled={busy || !addr} onClick={() => go(async () => setProbe(await api("/connections/probe", "POST", { address: addr })))}>Detect methods</button></div>
      {probe && <div className="mt">{probe.open.length ? <ul>{probe.open.map((o: any) => <li key={o.port}>{o.label} — port {o.port}</li>)}</ul> : "Nothing answered."}{probe.advice.map((a: string, i: number) => <div className="tag" key={i}>{a}</div>)}</div>}</div>}
    {agentish && <div className="card mt"><h3>2 · Register the client and pair</h3>
      <div className="grid"><div><label>Name</label><input value={f.name} onChange={set("name")} /></div><div><label>Owner</label><input value={f.owner_name} onChange={set("owner_name")} /></div>
        <div><label>Owner contact</label><input value={f.owner_contact} onChange={set("owner_contact")} /></div></div>
      <label>Authorization record</label><textarea rows={2} value={f.authorization_note} onChange={set("authorization_note")} />
      {!created ? <button className="btn mt" disabled={busy || !addr || !f.name} onClick={() => go(async () => setCreated(await api("/devices", "POST", { ...f, address: addr.includes(":") ? addr : addr + ":8766", transport: "agent" })))}>Register</button> :
        <div className="mt"><div className="tag">The pairing code and fingerprint appear on the CLIENT's screen. Type them here — they are never shown by this app.</div>
          <div className="grid"><div><label>Pairing code (8 digits)</label><input value={code} maxLength={8} onChange={e => setCode(e.target.value)} /></div><div><label>Fingerprint (64 hex)</label><input dir="ltr" value={fp} onChange={e => setFp(e.target.value)} /></div></div>
          <button className="btn mt" disabled={busy} onClick={() => go(async () => { const d = await api<Device>(`/devices/${created.id}/pair`, "POST", { code, fingerprint: fp }); c.refresh(); c.select(d.id); setTest(await api(`/devices/${d.id}/connection-test`)); })}>Pair &amp; test</button></div>}</div>}
    {test && <div className="card mt"><h3>Connection test</h3><table><tbody>{test.checks.map((k: any) => <tr key={k.stage}><td>{k.ok ? "✔" : "✖"} {k.stage}</td><td>{k.detail}{k.hint && <div className="tag">→ {k.hint}</div>}</td></tr>)}</tbody></table></div>}
    {pick === "tailscale" && <div className="card mt"><h3>Tailscale peers</h3><button className="btn ghost" onClick={() => go(async () => setTs(await api("/connections/tailscale")))}>Refresh</button>
      {ts && (ts.available ? <table><tbody>{ts.peers.map((p: any) => <tr key={p.ip}><td>{p.name}</td><td>{p.ip}</td><td>{String(p.online)}</td></tr>)}</tbody></table> : <div className="tag">{ts.reason}</div>)}</div>}
    {pick === "git" && <div className="card mt"><h3>Check repository access</h3><div className="row"><input style={{ flex: 1 }} dir="ltr" value={gitUrl} onChange={e => setGitUrl(e.target.value)} placeholder="https://github.com/org/repo.git" /><button className="btn" onClick={() => go(async () => setGitRes(await api("/connections/git", "POST", { url: gitUrl })))}>Test</button></div>
      {gitRes && <div className="mt">{gitRes.ok ? "✔ " : "✖ "}{gitRes.detail}{gitRes.hint && <div className="tag">{gitRes.hint}</div>}</div>}</div>}
    {pick === "rustdesk" && <div className="card mt"><h3>Open RustDesk</h3><div className="row"><input dir="ltr" value={rd} onChange={e => setRd(e.target.value)} placeholder="client's RustDesk ID" /><button className="btn" onClick={() => go(async () => setRdRes(await api("/connections/rustdesk", "POST", { remote_id: rd })))}>Connect</button></div>
      {rdRes && <div className="mt">{rdRes.launched ? rdRes.note : `${rdRes.reason} — ${rdRes.install}`}</div>}</div>}
    {["ssh", "winrm", "vps", "cloud_api"].includes(pick) && <div className="card mt"><div className="tag">{pick === "cloud_api" ? "Planned — not implemented." : "Register this device on the Devices page (transport OpenSSH / WinRM; servers: kind = server). Credentials are stored with Windows DPAPI."}</div></div>}
  </>;
}

// ------------------------------------------------------------------ AI Workspace (task engine)
function useTask(id: string | null) {
  const [task, setTask] = useState<Task | null>(null);
  useEffect(() => {
    if (!id) return setTask(null); let live = true;
    const tick = async () => { try { const t = await api<Task>(`/tasks/${id}`); if (live) setTask(t); } catch {} };
    tick(); const i = setInterval(tick, 1500); return () => { live = false; clearInterval(i); };
  }, [id]);
  return { task, setTask };
}

export function TaskView({ id, onChange }: { id: string; onChange?: () => void }) {
  const { task, setTask } = useTask(id); const { err, busy, go } = useAsync();
  const [conf, setConf] = useState<Record<string, boolean>>({}); const [txt, setTxt] = useState<Record<string, string>>({});
  const log = useRef<HTMLPreElement>(null);
  useEffect(() => { if (log.current) log.current.scrollTop = log.current.scrollHeight; }, [task?.events.length]);
  if (!task) return null;
  const upd = (p: Promise<Task>) => go(async () => { setTask(await p); onChange?.(); });
  const running = task.status === "running";
  return <div className="card mt"><div className="row"><b>{task.kind}</b><span className="pill">{task.status}</span>{task.mode && <span className="pill">{task.mode === "central" ? t("modeCentral") : t("modeRemote")}</span>}<span className="tag">{task.target.name}</span></div>
    <div className="tag">{task.mode_reason}</div><Err m={err} />
    <table className="mt"><thead><tr><th>Step</th><th>Risk</th><th>Status</th><th></th></tr></thead><tbody>{task.steps.map(s => <tr key={s.id}><td>{s.title}{s.production && <span className="pill bad"> production</span>}</td>
      <td className={"risk-" + s.risk}>{s.risk}</td><td>{s.status}{s.result && <div className="tag">{s.result.verified ? "✔ verified: " : s.result.ok ? "" : "✖ "}{s.result.detail}</div>}</td>
      <td>{s.status === "proposed" && <div className="row">
        {s.risk === "high" && <label style={{ margin: 0 }}><input type="checkbox" style={{ width: "auto" }} checked={!!conf[s.id]} onChange={e => setConf({ ...conf, [s.id]: e.target.checked })} /> {t("highConfirm")}</label>}
        {s.production && <input style={{ width: 160 }} placeholder="type the exact name" value={txt[s.id] ?? ""} onChange={e => setTxt({ ...txt, [s.id]: e.target.value })} />}
        <button className="btn" disabled={busy || (s.risk === "high" && !conf[s.id])} onClick={() => upd(api(`/tasks/${id}/steps/${s.id}/approve`, "POST", { approver: "Technician", confirm_high_risk: !!conf[s.id], confirm_text: txt[s.id] }))}>{t("approve")}</button>
        <button className="btn ghost" onClick={() => upd(api(`/tasks/${id}/steps/${s.id}/reject`, "POST"))}>{t("reject")}</button></div>}</td></tr>)}</tbody></table>
    <div className="row mt">
      <button className="btn ghost" disabled={busy} onClick={() => upd(api(`/tasks/${id}/approve-low-risk`, "POST", { approver: "Technician" }))}>{t("approveLow")}</button>
      <button className="btn" disabled={busy || running || ["blocked", "completed", "cancelled"].includes(task.status)} onClick={() => upd(api(`/tasks/${id}/execute`, "POST"))}>{t("execute")}</button>
      <button className="btn danger" disabled={!running} onClick={() => upd(api(`/tasks/${id}/cancel`, "POST"))}>Cancel</button>
      {task.changes.length > 0 && <button className="btn ghost" onClick={() => upd(api(`/tasks/${id}/rollback`, "POST").then(() => api<Task>(`/tasks/${id}`)))}>Rollback pushed changes</button>}</div>
    {task.changes.length > 0 && <details open className="mt"><summary>{task.changes.length} changed file(s) — review before pushing</summary>{task.changes.map(ch => <div key={ch.path}><b>{ch.status}</b> {ch.path}<pre dir="ltr">{ch.diff || "(no text diff)"}</pre></div>)}</details>}
    <h3 className="mt">Live log</h3><pre ref={log} dir="ltr">{task.events.map(e => `${new Date(e.ts * 1000).toLocaleTimeString()} ${e.level === "error" ? "ERR " : e.level === "warn" ? "WARN " : ""}${e.msg}`).join("\n")}</pre>
    {task.report && <details className="mt"><summary>Report</summary><pre>{task.report}</pre></details>}</div>;
}

export function Workspace({ c }: PP) {
  const { err, busy, go } = useAsync(); const [projects, setProjects] = useState<Project[]>([]);
  const [kind, setKind] = useState("dev"); const [mode, setMode] = useState("auto"); const [pid, setPid] = useState(""); const [ins, setIns] = useState("");
  const [tools, setTools] = useState<string[]>([]); const [acts, setActs] = useState<string[]>(["srv_uptime", "srv_disk"]); const [bash, setBash] = useState(false); const [tid, setTid] = useState<string | null>(null);
  const [kinds, setKinds] = useState<any>(null); const did = c.dev?.id;
  useEffect(() => { api("/task-kinds").then(setKinds).catch(() => {}); }, []);
  useEffect(() => { if (did) api<Project[]>(`/projects?device_id=${did}`).then(p => { setProjects(p); setPid(p[0]?.id ?? ""); }).catch(() => {}); }, [did]);
  if (!c.dev) return <NoDev />;
  const body: any = { device_id: did, kind, instruction: ins, project_id: ["dev", "deploy"].includes(kind) ? pid : null, mode, tools, allow_bash: bash, actions: kind === "server" ? acts.map(a => ({ tool_id: a, params: {} })) : [] };
  return <><Err m={err} /><div className="card"><h3>{t("newTask")} — {c.dev.name}</h3>
    <div className="tag">Flow: identify target → verify authorization → detect tools → choose Central/Remote Claude → plan → approvals → live execution → verification → report. Claude Code on this computer: {kinds?.claude_central_available ? "✔ found" : "✖ not found"}.</div>
    <div className="grid"><div><label>{t("kind")}</label><select value={kind} onChange={e => setKind(e.target.value)}><option value="dev">Development (Claude Code edits a project)</option><option value="diagnose">Diagnose Windows (rule-based)</option><option value="install_tools">Install dev tools</option><option value="server">Server actions</option><option value="deploy">Deploy a project</option></select></div>
      {kind === "dev" && <div><label>{t("mode")}</label><select value={mode} onChange={e => setMode(e.target.value)}><option value="auto">Automatic</option><option value="central">{t("modeCentral")}</option><option value="remote">{t("modeRemote")}</option></select></div>}
      {["dev", "deploy"].includes(kind) && <div><label>Project</label><select value={pid} onChange={e => setPid(e.target.value)}>{projects.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></div>}</div>
    {kind === "dev" && <><label>{t("instruction")}</label><textarea rows={3} value={ins} onChange={e => setIns(e.target.value)} placeholder="Fix the failing tests and explain what you changed" />
      <label style={{ display: "flex", gap: 6, alignItems: "center" }}><input type="checkbox" style={{ width: "auto" }} checked={bash} onChange={e => setBash(e.target.checked)} /> Let Claude run shell commands (Bash) — off by default</label></>}
    {kind === "install_tools" && <div className="row mt">{["git", "node", "python", "docker", "claude"].map(x => <label key={x} style={{ margin: 0 }}><input type="checkbox" style={{ width: "auto" }} checked={tools.includes(x)} onChange={e => setTools(e.target.checked ? [...tools, x] : tools.filter(y => y !== x))} /> {x}</label>)}</div>}
    {kind === "server" && <div className="row mt">{["srv_uptime", "srv_disk", "srv_memory", "srv_ports", "docker_ps"].map(x => <label key={x} style={{ margin: 0 }}><input type="checkbox" style={{ width: "auto" }} checked={acts.includes(x)} onChange={e => setActs(e.target.checked ? [...acts, x] : acts.filter(y => y !== x))} /> {x}</label>)}<span className="tag">More actions (restart, backup…) on Server Manager.</span></div>}
    <button className="btn mt" disabled={busy || (["dev", "deploy"].includes(kind) && !pid)} onClick={() => go(async () => { const tk = await api<Task>("/tasks", "POST", body); setTid(tk.id); })}>Prepare plan</button></div>
    {tid && <TaskView id={tid} />}</>;
}

// ------------------------------------------------------------------ Development projects
export function Projects({ c }: PP) {
  const { err, busy, go } = useAsync(); const did = c.dev?.id;
  const [projects, setProjects] = useState<Project[]>([]); const [sel, setSel] = useState<Project | null>(null); const [stacks, setStacks] = useState<any>({});
  const [f, setF] = useState({ name: "", root: "", stack: "python", deploy: "" });
  const [tree, setTree] = useState<any[]>([]); const [rel, setRel] = useState(""); const [file, setFile] = useState<{ path: string; content: string; binary?: boolean } | null>(null);
  const [out, setOut] = useState(""); const [cmd, setCmd] = useState("git status"); const [env, setEnv] = useState<any>(null); const [backups, setBackups] = useState<any[]>([]); const [tid, setTid] = useState<string | null>(null); const [ins, setIns] = useState("");
  const load = useCallback(() => { if (did) api<Project[]>(`/projects?device_id=${did}`).then(p => { setProjects(p); setSel(s => p.find(x => x.id === s?.id) ?? p[0] ?? null); }).catch(() => {}); }, [did]);
  useEffect(() => { load(); api("/stacks").then(setStacks).catch(() => {}); }, [load]);
  const sid = sel?.id;
  useEffect(() => { setRel(""); setFile(null); setOut(""); setEnv(null); if (sid) { api(`/projects/${sid}/tree`).then(setTree).catch(() => {}); api(`/projects/${sid}/backups`).then(setBackups).catch(() => {}); api(`/projects/${sid}/env`).then(setEnv).catch(() => {}); } }, [sid]);
  if (!c.dev) return <NoDev />;
  const open = (e: any) => { const p = (rel ? rel + "/" : "") + e.name; if (e.dir) { setRel(p); api(`/projects/${sid}/tree?path=${encodeURIComponent(p)}`).then(setTree); } else api(`/projects/${sid}/file?path=${encodeURIComponent(p)}`).then(r => setFile({ path: p, content: r.content, binary: r.binary })); };
  const up = () => { const p = rel.split("/").slice(0, -1).join("/"); setRel(p); api(`/projects/${sid}/tree?path=${encodeURIComponent(p)}`).then(setTree); };
  const show = (r: any) => setOut(`exit ${r.exit}${r.timed_out ? " (timed out)" : ""}\n${r.output}`);
  return <><Err m={err} />
    <div className="card"><div className="row"><select style={{ width: 240 }} value={sid ?? ""} onChange={e => setSel(projects.find(p => p.id === e.target.value) ?? null)}>{projects.length === 0 && <option>No projects yet</option>}{projects.map(p => <option key={p.id} value={p.id}>{p.name} ({p.stack})</option>)}</select>
      {sel && <span className="tag" dir="ltr">{sel.root}</span>}</div></div>
    <details className="card mt" open={!projects.length}><summary>Add project on {c.dev.name}</summary><div className="grid"><div><label>Name</label><input value={f.name} onChange={e => setF({ ...f, name: e.target.value })} /></div>
      <div><label>Folder on the target</label><input dir="ltr" value={f.root} onChange={e => setF({ ...f, root: e.target.value })} /></div>
      <div><label>Stack</label><select value={f.stack} onChange={e => setF({ ...f, stack: e.target.value })}>{Object.keys(stacks).map(k => <option key={k}>{k}</option>)}</select></div>
      <div><label>Deploy command (optional, space-separated)</label><input dir="ltr" value={f.deploy} onChange={e => setF({ ...f, deploy: e.target.value })} /></div></div>
      <div className="tag">Default build/test commands for the stack: {JSON.stringify(stacks[f.stack])}</div>
      <button className="btn mt" disabled={busy || !f.name || !f.root} onClick={() => go(async () => { await api("/projects", "POST", { device_id: did, name: f.name, root: f.root, stack: f.stack, deploy_cmd: f.deploy ? f.deploy.split(/\s+/) : null }); load(); })}>Add</button></details>
    {sel && <><div className="grid mt">
      <div className="card"><h3>Explorer {rel && <button className="btn ghost" onClick={up}>↑</button>} <span dir="ltr">/{rel}</span></h3><table><tbody>{tree.map(e => <tr key={e.name} style={{ cursor: "pointer" }} onClick={() => open(e)}><td dir="ltr">{e.dir ? "📁" : "📄"} {e.name}</td></tr>)}</tbody></table></div>
      <div className="card" style={{ gridColumn: "span 2" }}><h3>Editor {file && <span dir="ltr">{file.path}</span>}</h3>{file ? (file.binary ? "binary file" : <><textarea dir="ltr" rows={14} style={{ fontFamily: "monospace" }} value={file.content} onChange={e => setFile({ ...file, content: e.target.value })} />
        <button className="btn mt" disabled={busy} onClick={() => go(async () => { await api(`/projects/${sid}/file`, "PUT", { path: file.path, content: file.content }); setOut(`saved ${file.path} (previous version kept)`); })}>Save</button></>) : "Select a file"}</div></div>
      <div className="grid mt"><div className="card"><h3>Environment</h3>{env && <><div>{env.os} · {env.host}</div><div className="tag">allowed: {(env.grants || []).join(", ") || "read-only"}</div>
        <div className="tag">{Object.entries(env.tools || {}).map(([k, v]: any) => `${k}:${v.found ? "✔" : "✖"}`).join(" ")}</div></>}</div>
        <div className="card"><h3>Git / build / test</h3><div className="row"><button className="btn ghost" onClick={() => go(async () => show(await api(`/projects/${sid}/git/status`)))}>git status</button><button className="btn ghost" onClick={() => go(async () => show(await api(`/projects/${sid}/git/diff`)))}>git diff</button>
          <button className="btn ghost" disabled={!sel.build_cmd} onClick={() => go(async () => show(await api(`/projects/${sid}/run`, "POST", { which: "build" })))}>Build</button><button className="btn ghost" disabled={!sel.test_cmd} onClick={() => go(async () => show(await api(`/projects/${sid}/run`, "POST", { which: "test" })))}>Test</button></div>
          <div className="row mt"><input dir="ltr" style={{ flex: 1 }} value={cmd} onChange={e => setCmd(e.target.value)} placeholder="terminal (no shell: program + arguments)" /><button className="btn" disabled={busy} onClick={() => go(async () => show(await api(`/projects/${sid}/terminal`, "POST", { argv: cmd.trim().split(/\s+/) })))}>Run</button></div></div>
        <div className="card"><h3>Backup &amp; rollback</h3><button className="btn ghost" onClick={() => go(async () => { await api(`/projects/${sid}/backup`, "POST"); setBackups(await api(`/projects/${sid}/backups`)); })}>Backup now</button>
          {backups.map(b => <div key={b.id} className="row"><span className="tag">{b.id} · {b.files} files</span><button className="btn danger" onClick={() => go(async () => { const r = await api(`/projects/${sid}/rollback`, "POST", { backup_id: b.id }); setOut(`restored ${r.restored} file(s)`); })}>Restore</button></div>)}</div></div>
      <div className="card mt"><h3>Output</h3><pre dir="ltr">{out}</pre></div>
      <div className="card mt"><h3>Ask Claude about this project</h3><textarea rows={2} value={ins} onChange={e => setIns(e.target.value)} placeholder="Fix the failing tests…" />
        <button className="btn mt" disabled={busy || !ins} onClick={() => go(async () => { const tk = await api<Task>("/tasks", "POST", { device_id: did, kind: "dev", instruction: ins, project_id: sid, mode: "auto" }); setTid(tk.id); })}>Prepare Claude task</button></div>
      {tid && <TaskView id={tid} />}</>}</>;
}

// ------------------------------------------------------------------ Remote Claude sessions
export function RemoteClaude({ c }: PP) {
  const [tasks, setTasks] = useState<Task[]>([]); const [open, setOpen] = useState<string | null>(null);
  const load = useCallback(() => api<Task[]>(`/tasks?kind=dev${c.dev ? `&device_id=${c.dev.id}` : ""}`).then(r => setTasks(r.filter(x => x.mode === "remote"))).catch(() => {}), [c.dev]);
  useEffect(() => { load(); const i = setInterval(load, 4000); return () => clearInterval(i); }, [load]);
  return <><div className="card"><div className="tag">Remote Claude runs on the CLIENT's computer with the CLIENT's own Claude login (agent started with --allow-claude). Your personal credentials are never sent. Reconnect = reopen a session here; running jobs stay on the agent until the session ends.</div></div>
    <div className="card mt"><table><thead><tr><th>Task</th><th>Target</th><th>Status</th><th></th></tr></thead><tbody>{tasks.map(x => <tr key={x.id}><td>{x.instruction.slice(0, 60) || x.kind}</td><td>{x.target.name}</td><td>{x.status}</td><td><button className="btn ghost" onClick={() => setOpen(x.id)}>Open / stream</button></td></tr>)}</tbody></table>
      {!tasks.length && <div className="tag">No remote sessions yet. Start one from AI Workspace → Development → mode “Remote Claude”.</div>}</div>
    {open && <TaskView id={open} onChange={load} />}</>;
}

// ------------------------------------------------------------------ Server manager
export function Servers({ c }: PP) {
  const { err, busy, go } = useAsync(); const [tools, setTools] = useState<any[]>([]); const [out, setOut] = useState(""); const [unit, setUnit] = useState("nginx"); const [sel, setSel] = useState("srv_uptime"); const [tid, setTid] = useState<string | null>(null);
  useEffect(() => { api("/server-tools").then(setTools).catch(() => {}); }, []);
  const servers = c.devices.filter(d => d.kind === "server" || d.transport === "local");
  const tool = tools.find(x => x.id === sel); const needs = tool ? Object.keys(tool.params) : [];
  const params = () => { const p: any = {}; for (const k of needs) p[k] = k === "lines" ? 100 : k === "dest" ? `/var/backups/cit-${Date.now()}.tgz` : k === "path" || k === "src" ? "/etc/nginx" : k === "host" ? "example.com" : unit; return p; };
  return <><Err m={err} />
    <div className="card"><div className="row"><span className="tag">Servers:</span>{servers.map(s => <button key={s.id} className={"btn " + (c.dev?.id === s.id ? "" : "ghost")} onClick={() => c.select(s.id)}>{s.name}</button>)}{!servers.length && <span className="tag">Register an SSH device with kind = server (Devices).</span>}</div>
      <div className="tag">Least privilege: allowlisted actions only, sudo -n for root actions, production changes need typed confirmation. Never expose SSH to the Internet except key-only access to your own VPS.</div></div>
    {c.dev && <div className="card mt"><h3>Run an action on {c.dev.name}</h3><div className="row"><select style={{ width: 260 }} value={sel} onChange={e => setSel(e.target.value)}>{tools.map(x => <option key={x.id} value={x.id}>{x.title} [{x.risk}]</option>)}</select>
      {needs.length > 0 && <input style={{ width: 160 }} dir="ltr" value={unit} onChange={e => setUnit(e.target.value)} placeholder="unit / container" />}
      {tool?.risk === "read_only" ? <button className="btn" disabled={busy} onClick={() => go(async () => { const r = await api(`/devices/${c.dev!.id}/server-read`, "POST", { tool_id: sel, params: params() }); setOut(`exit ${r.exit}\n${r.output}`); })}>Run (read-only)</button>
        : <button className="btn" disabled={busy} onClick={() => go(async () => { const tk = await api<Task>("/tasks", "POST", { device_id: c.dev!.id, kind: "server", actions: [{ tool_id: sel, params: params() }] }); setTid(tk.id); })}>Create approval task</button>}</div>
      <pre dir="ltr">{out}</pre></div>}
    {tid && <TaskView id={tid} />}</>;
}

// ------------------------------------------------------------------ Remote desktop
export function Desktop({ c }: PP) {
  const { err, go } = useAsync(); const [id, setId] = useState(""); const [res, setRes] = useState<any>(null); const [caps, setCaps] = useState<any>({});
  useEffect(() => { api("/connections/local").then(setCaps).catch(() => {}); }, []);
  return <><Err m={err} /><div className="card"><h3>RustDesk</h3><div className="tag">RustDesk installed on this computer: {caps.rustdesk ? "✔" : "✖ (install from rustdesk.com)"} · Tailscale: {caps.tailscale ? "✔" : "✖"}</div>
    <div className="row mt"><input dir="ltr" placeholder="client's RustDesk ID" value={id} onChange={e => setId(e.target.value)} /><button className="btn" disabled={!id} onClick={() => go(async () => setRes(await api("/connections/rustdesk", "POST", { remote_id: id })))}>Open RustDesk</button></div>
    {res && <div className="mt">{res.launched ? res.note : `${res.reason} — ${res.install}`}</div>}
    <div className="tag mt">No password is passed on the command line; the client approves the connection on their own screen. Use the client's Tailscale or LAN address inside RustDesk where possible.</div></div></>;
}

// ------------------------------------------------------------------ File manager
export function Files({ c }: PP) {
  const { err, busy, go } = useAsync(); const [path, setPath] = useState(""); const [entries, setEntries] = useState<any[]>([]); const [view, setView] = useState<{ path: string; text: string } | null>(null);
  const did = c.dev?.id;
  const list = useCallback((p?: string) => go(async () => { const r = await api(`/devices/${did}/fs/list${p ? `?path=${encodeURIComponent(p)}` : ""}`); setPath(r.path); setEntries(r.entries); }), [did, go]);
  useEffect(() => { setView(null); if (did) list(); }, [did, list]);
  if (!c.dev) return <NoDev />;
  const sep = path.includes("\\") ? "\\" : "/"; const join = (n: string) => path.replace(/[\\/]$/, "") + sep + n;
  const parent = () => path.replace(/[\\/][^\\/]+[\\/]?$/, "") || "/";
  const upload = (f: File) => go(async () => { const b = new Uint8Array(await f.arrayBuffer()); let s = ""; b.forEach(x => (s += String.fromCharCode(x))); await api(`/devices/${did}/fs/write`, "PUT", { path: join(f.name), content_b64: btoa(s) }); list(path); });
  return <><Err m={err} /><div className="card"><div className="row"><button className="btn ghost" onClick={() => list(parent())}>↑</button><input dir="ltr" style={{ flex: 1 }} value={path} onChange={e => setPath(e.target.value)} onKeyDown={e => e.key === "Enter" && list(path)} />
    <label className="btn ghost" style={{ margin: 0 }}>Upload<input type="file" hidden onChange={e => e.target.files?.[0] && upload(e.target.files[0])} /></label></div>
    <div className="tag">Confined to the folders the owner authorized. Uploads need the 'write' permission.</div>
    <table className="mt"><tbody>{entries.map(e => <tr key={e.name}><td dir="ltr" style={{ cursor: "pointer" }} onClick={() => e.dir ? list(join(e.name)) : go(async () => { const r = await api(`/devices/${did}/fs/read?path=${encodeURIComponent(join(e.name))}`); setView({ path: join(e.name), text: atob(r.content_b64).slice(0, 20000) }); })}>{e.dir ? "📁" : "📄"} {e.name}</td><td>{e.dir ? "" : e.size + " B"}</td></tr>)}</tbody></table></div>
    {view && <div className="card mt"><h3 dir="ltr">{view.path}</h3><pre dir="ltr">{view.text}</pre></div>}{busy && null}</>;
}

// ------------------------------------------------------------------ Deployments & history
export function Deployments({ c }: PP) {
  const [tasks, setTasks] = useState<Task[]>([]); const [open, setOpen] = useState<string | null>(null); const { err, go } = useAsync(); const [pid, setPid] = useState(""); const [projects, setProjects] = useState<Project[]>([]);
  const load = useCallback(() => api<Task[]>(`/tasks?kind=deploy${c.dev ? `&device_id=${c.dev.id}` : ""}`).then(setTasks).catch(() => {}), [c.dev]);
  useEffect(() => { load(); if (c.dev) api<Project[]>(`/projects?device_id=${c.dev.id}`).then(p => { setProjects(p.filter(x => x.deploy_cmd)); setPid(p.find(x => x.deploy_cmd)?.id ?? ""); }); }, [load, c.dev]);
  return <><Err m={err} /><div className="card"><h3>New deployment</h3><div className="row"><select value={pid} onChange={e => setPid(e.target.value)} style={{ width: 240 }}>{projects.map(p => <option key={p.id} value={p.id}>{p.name}: {p.deploy_cmd?.join(" ")}</option>)}</select>
    <button className="btn" disabled={!pid} onClick={() => go(async () => { const tk = await api<Task>("/tasks", "POST", { device_id: c.dev!.id, kind: "deploy", project_id: pid }); setOpen(tk.id); load(); })}>Prepare deployment</button></div>
    <div className="tag">Steps: backup → deploy (production: per-action approval + typed project name) → tests. {projects.length === 0 && "Add a deploy command to a project first."}</div></div>
    <div className="card mt"><table><tbody>{tasks.map(x => <tr key={x.id}><td>{new Date(x.created_at * 1000).toLocaleString()}</td><td>{x.status}</td><td><button className="btn ghost" onClick={() => setOpen(x.id)}>Open</button></td></tr>)}</tbody></table></div>{open && <TaskView id={open} onChange={load} />}</>;
}

export function History({ c }: PP) {
  const [tasks, setTasks] = useState<Task[]>([]); const [open, setOpen] = useState<string | null>(null);
  useEffect(() => { api<Task[]>(`/tasks${c.dev ? `?device_id=${c.dev.id}` : ""}`).then(setTasks).catch(() => {}); }, [c.dev]);
  return <><div className="card"><table><thead><tr><th>When</th><th>Kind</th><th>Target</th><th>Mode</th><th>Status</th><th></th></tr></thead><tbody>{tasks.map(x => <tr key={x.id}><td>{new Date(x.created_at * 1000).toLocaleString()}</td><td>{x.kind}</td><td>{x.target.name}</td><td>{x.mode ?? "—"}</td><td>{x.status}</td><td><button className="btn ghost" onClick={() => setOpen(x.id)}>Open</button></td></tr>)}</tbody></table>
    {!tasks.length && <div className="tag">No tasks yet.</div>}</div>{open && <TaskView id={open} />}</>;
}

export function FeatureStatus() {
  const [f, setF] = useState<any[]>([]); useEffect(() => { api("/features").then(setF).catch(() => {}); }, []);
  return <div className="card mt"><h3>{t("features")}</h3><table><thead><tr><th>Area</th><th>Feature</th><th>Status</th><th>Note</th></tr></thead><tbody>{f.map(x => <tr key={x.id}><td>{x.area}</td><td>{x.title}</td><td><Badge s={x.status} /></td><td className="tag">{x.note}</td></tr>)}</tbody></table>
    <div className="tag mt">tested = automated tests pass · untested = code exists, never run against the real thing · mock = simulated only · planned = not built · blocked = needs something external</div></div>;
}
