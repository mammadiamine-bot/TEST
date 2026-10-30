import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { t } from "./i18n";
import type { Ctx } from "./pages";

type Res = { worker_id: string; cpu: number; ram: number; disk: number; active_tasks: number; queue: Record<string, number>; locked: string | null; authorized_until: number | null; secret_store: string; os: string; host: string; auto_resume: boolean; tools: Record<string, boolean>; clients: { id: string; name: string }[] };
type Worker = { id: string; name: string; address: string; status: string; online: boolean; resources: Res | null; rustdesk_id: string | null; fingerprint: string; credential_loaded: boolean };
type WTask = { id: string; client_id: string; project: string; kind: string; status: string; created: number; data: { instruction: string; production: boolean; destructive: boolean; session_id: string | null; result: { detail: string } | null }; events: { seq: number; ts: number; level: string; msg: string }[]; last_seq: number };

function useAsync() {
  const [err, setErr] = useState(""); const [busy, setBusy] = useState(false);
  const go = useCallback(async <T,>(f: () => Promise<T>): Promise<T | undefined> => { setBusy(true); setErr(""); try { return await f(); } catch (e: any) { setErr(e.message); } finally { setBusy(false); } }, []);
  return { err, busy, go };
}
const Err = ({ m }: { m: string }) => (m ? <div className="err" role="alert">{m}</div> : null);
const Bar = ({ v, label }: { v: number; label: string }) => <div><div className="tag">{label} {Math.round(v)}%</div><div style={{ height: 8, background: "#06122b", borderRadius: 4 }}><div style={{ width: `${Math.min(100, v)}%`, height: 8, borderRadius: 4, background: v > 85 ? "var(--bad)" : v > 65 ? "var(--warn)" : "var(--acc)" }} /></div></div>;
const W = (id: string, p: string) => `/workers/${id}/w${p}`;
const TABS = ["Overview", "Add worker", "Clients & projects", "Task queue", "Live log", "Terminal", "Remote desktop", "Security"] as const;

export function Workers({ c }: { c: Ctx }) {
  const [tab, setTab] = useState<(typeof TABS)[number]>("Overview");
  const [ws, setWs] = useState<Worker[]>([]); const [sel, setSel] = useState(""); const [err, setErr] = useState("");
  const load = useCallback(async () => { try { await api("/workers/poll", "POST"); const l = await api<Worker[]>("/workers"); setWs(l); setSel(s => s || l[0]?.id || ""); setErr(""); } catch (e: any) { setErr(e.message); } }, []);
  useEffect(() => { load(); const i = setInterval(load, 8000); return () => clearInterval(i); }, [load]);
  const w = ws.find(x => x.id === sel);
  return <>
    <Err m={err} />
    <div className="card"><div className="row">{ws.map(x => <button key={x.id} className={"btn " + (sel === x.id ? "" : "ghost")} onClick={() => setSel(x.id)}>{x.online ? "🟢" : "⚫"} {x.name}</button>)}
      {!ws.length && <span className="tag">No worker stations yet — use “Add worker”.</span>}</div>
      <div className="row mt">{TABS.map(x => <button key={x} className={"btn " + (tab === x ? "" : "ghost")} onClick={() => setTab(x)}>{x}</button>)}</div></div>
    {tab === "Add worker" && <AddWorker onDone={id => { setSel(id); setTab("Overview"); load(); }} />}
    {tab !== "Add worker" && !w && <div className="card mt">{t("none")}</div>}
    {w && tab === "Overview" && <Overview w={w} all={ws} reload={load} />}
    {w && tab === "Clients & projects" && <ClientsProjects w={w} />}
    {w && tab === "Task queue" && <Queue w={w} />}
    {w && tab === "Live log" && <LiveLog w={w} />}
    {w && tab === "Terminal" && <Terminal w={w} />}
    {w && tab === "Remote desktop" && <Desktop w={w} reload={load} />}
    {w && tab === "Security" && <Security w={w} reload={load} />}
  </>;
}

function Overview({ w, all, reload }: { w: Worker; all: Worker[]; reload: () => void }) {
  const r = w.resources; const { err, go } = useAsync();
  return <><Err m={err} /><div className="grid mt">
    {all.map(x => <div className="card" key={x.id}><h3>{x.name}</h3><div>{x.online ? "🟢 online" : "⚫ offline"} · {x.status}</div><div className="tag" dir="ltr">{x.address}</div>
      {x.resources && <><Bar v={x.resources.cpu} label="CPU" /><Bar v={x.resources.ram} label="RAM" /><Bar v={x.resources.disk} label="Disk" />
        <div className="tag">active tasks {x.resources.active_tasks} · queue {Object.entries(x.resources.queue).map(([k, v]) => `${k}:${v}`).join(" ") || "empty"}</div>
        {x.resources.locked && <div className="pill bad">LOCKED: {x.resources.locked}</div>}</>}</div>)}</div>
    {r && <div className="card mt"><h3>{w.name}</h3><div>{r.os} · {r.host} · worker id <span dir="ltr">{r.worker_id}</span></div>
      <div className="tag">authorized until: {r.authorized_until ? new Date(r.authorized_until * 1000).toLocaleString() : "no window set — set one on the worker with `authorize --days N`"} · secrets: {r.secret_store}</div>
      <div className="tag">tools: {Object.entries(r.tools).map(([k, v]) => `${k}:${v ? "✔" : "✖"}`).join(" ")}</div>
      <div className="tag">clients: {r.clients.map(x => x.name).join(", ") || "none"}</div>
      <div className="row mt">{r.locked && <button className="btn" onClick={() => go(async () => { await api(W(w.id, "/unlock"), "POST", { confirm: true }); reload(); })}>Unlock</button>}
        <button className="btn danger" onClick={() => go(async () => { if (confirm("Stop all jobs and lock this worker?")) { await api(W(w.id, "/emergency-stop"), "POST", {}); reload(); } })}>⏻ Emergency stop this worker</button></div></div>}</>;
}

function AddWorker({ onDone }: { onDone: (id: string) => void }) {
  const { err, busy, go } = useAsync(); const [f, setF] = useState({ name: "", address: "", fingerprint: "", rustdesk_id: "" }); const [code, setCode] = useState(""); const [days, setDays] = useState(""); const [id, setId] = useState("");
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  return <div className="card mt"><h3>Add a Worker PC</h3><Err m={err} />
    <ol><li>On the Worker (Windows, visible console): <code dir="ltr">cit-worker.exe run --bind &lt;its Tailscale 100.x IP&gt;</code>. It prints a <b>fingerprint</b>.</li>
      <li>Also on the Worker: <code dir="ltr">cit-worker.exe authorize --days 14</code> (the client-site authorization window) and <code dir="ltr">cit-worker.exe enroll</code> → a one-time <b>8-digit code</b>.</li>
      <li>Enter its private/Tailscale address and the fingerprint below, then the code. Public addresses are refused.</li></ol>
    {!id ? <><div className="grid"><div><label>Name</label><input value={f.name} onChange={set("name")} placeholder="Acme office worker" /></div><div><label>Address (host:8777)</label><input dir="ltr" value={f.address} onChange={set("address")} placeholder="100.101.102.103:8777" /></div>
      <div><label>RustDesk ID (optional)</label><input dir="ltr" value={f.rustdesk_id} onChange={set("rustdesk_id")} /></div></div>
      <label>Fingerprint shown on the worker</label><input dir="ltr" value={f.fingerprint} onChange={set("fingerprint")} />
      <button className="btn mt" disabled={busy || !f.name || !f.address || !f.fingerprint} onClick={() => go(async () => { const w = await api<Worker>("/workers", "POST", { ...f, rustdesk_id: f.rustdesk_id || null }); setId(w.id); })}>Register</button></>
      : <><div className="grid"><div><label>Enrollment code (8 digits)</label><input value={code} maxLength={8} onChange={e => setCode(e.target.value)} /></div><div><label>Credential valid for (days, optional)</label><input value={days} onChange={e => setDays(e.target.value)} /></div></div>
        <button className="btn mt" disabled={busy || code.length !== 8} onClick={() => go(async () => { const r = await api(`/workers/${id}/enroll`, "POST", { code, days: days ? +days : null }); if (!r.token_persisted) alert("This computer cannot store the credential in Windows-protected storage; it stays in memory only and you must enroll again after restarting the app."); onDone(id); })}>Enroll &amp; connect</button></>}</div>;
}

function ClientsProjects({ w }: { w: Worker }) {
  const { err, busy, go } = useAsync(); const [clients, setClients] = useState<any[]>([]); const [cid, setCid] = useState(""); const [projects, setProjects] = useState<any[]>([]); const [secrets, setSecrets] = useState<string[]>([]);
  const [nc, setNc] = useState({ id: "", name: "", systems: "", days: "" }); const [np, setNp] = useState({ name: "", git_url: "", test_cmd: "", deploy_cmd: "" }); const [sec, setSec] = useState({ name: "ANTHROPIC_API_KEY", value: "" });
  const [path, setPath] = useState(""); const [entries, setEntries] = useState<any[]>([]); const [view, setView] = useState("");
  const lc = useCallback(() => api<any[]>(W(w.id, "/clients")).then(l => { setClients(l); setCid(x => x || l[0]?.id || ""); }).catch(() => {}), [w.id]);
  useEffect(() => { lc(); }, [lc]);
  const lp = useCallback(() => { if (cid) { api<any[]>(W(w.id, `/clients/${cid}/projects`)).then(setProjects).catch(() => {}); api<string[]>(W(w.id, `/clients/${cid}/secrets`)).then(setSecrets).catch(() => {}); api(W(w.id, `/clients/${cid}/fs/list`)).then(r => { setEntries(r.entries); setPath(""); }).catch(() => {}); } }, [w.id, cid]);
  useEffect(() => { lp(); setView(""); }, [lp]);
  const split = (s: string) => s.trim() ? s.trim().split(/\s+/) : null;
  return <><Err m={err} /><div className="card mt"><div className="tag">Every client has its own workspace, secrets and Claude login folder on the worker. Nothing is shared between clients.</div>
    <div className="row mt">{clients.map(x => <button key={x.id} className={"btn " + (cid === x.id ? "" : "ghost")} onClick={() => setCid(x.id)}>{x.name}</button>)}</div></div>
    <details className="card mt" open={!clients.length}><summary>Add client</summary><div className="grid"><div><label>Client id (a-z 0-9 - _)</label><input dir="ltr" value={nc.id} onChange={e => setNc({ ...nc, id: e.target.value })} /></div><div><label>Name</label><input value={nc.name} onChange={e => setNc({ ...nc, name: e.target.value })} /></div>
      <div><label>Approved systems (host:port, space separated)</label><input dir="ltr" value={nc.systems} onChange={e => setNc({ ...nc, systems: e.target.value })} placeholder="192.168.1.50:22 git.example.com:443" /></div><div><label>Authorization (days)</label><input value={nc.days} onChange={e => setNc({ ...nc, days: e.target.value })} /></div></div>
      <div className="tag">Only the listed systems may be contacted for this client; the worker never scans the client's network.</div>
      <button className="btn mt" disabled={busy || !nc.id || !nc.name} onClick={() => go(async () => { await api(W(w.id, "/clients"), "POST", { id: nc.id, name: nc.name, days: nc.days ? +nc.days : null, systems: nc.systems.split(/\s+/).filter(Boolean).map(s => ({ host: s.split(":")[0], port: +(s.split(":")[1] || 22) })) }); setCid(nc.id); lc(); })}>Create</button></details>
    {cid && <><div className="grid mt"><div className="card"><h3>Projects</h3>{projects.map(p => <div key={p.name}><b>{p.name}</b> <span className="tag">{p.git_url ?? "no git"} · test: {p.test_cmd?.join(" ") ?? "—"} · deploy: {p.deploy_cmd?.join(" ") ?? "—"}</span></div>)}
      <div className="grid mt"><div><label>Project name</label><input dir="ltr" value={np.name} onChange={e => setNp({ ...np, name: e.target.value })} /></div><div><label>Git URL (optional)</label><input dir="ltr" value={np.git_url} onChange={e => setNp({ ...np, git_url: e.target.value })} /></div>
        <div><label>Test command</label><input dir="ltr" value={np.test_cmd} onChange={e => setNp({ ...np, test_cmd: e.target.value })} /></div><div><label>Deploy command</label><input dir="ltr" value={np.deploy_cmd} onChange={e => setNp({ ...np, deploy_cmd: e.target.value })} /></div></div>
      <button className="btn mt" disabled={busy || !np.name} onClick={() => go(async () => { await api(W(w.id, `/clients/${cid}/projects`), "POST", { name: np.name, git_url: np.git_url || null, test_cmd: split(np.test_cmd), deploy_cmd: split(np.deploy_cmd) }); lp(); })}>Add project</button></div>
      <div className="card"><h3>Client secrets (encrypted on the worker)</h3><div className="tag">{secrets.length ? secrets.join(", ") : "none"} — values are never shown again.</div>
        <div className="row mt"><input dir="ltr" style={{ width: 200 }} value={sec.name} onChange={e => setSec({ ...sec, name: e.target.value })} /><input type="password" autoComplete="off" value={sec.value} onChange={e => setSec({ ...sec, value: e.target.value })} /><button className="btn" disabled={!sec.value} onClick={() => go(async () => { await api(W(w.id, `/clients/${cid}/secrets/${sec.name}`), "PUT", { value: sec.value }); setSec({ ...sec, value: "" }); lp(); })}>Save</button></div>
        <div className="tag mt">ANTHROPIC_API_KEY = this client's own Claude API key (never yours).</div>
        <button className="btn danger mt" onClick={() => go(async () => { if (confirm(`Delete client ${cid} and ALL its files on the worker?`)) { await api(W(w.id, `/clients/${cid}`), "DELETE"); setCid(""); lc(); } })}>Remove client &amp; wipe its workspace</button></div></div>
      <div className="card mt"><h3>Files <span dir="ltr">{path}</span></h3><table><tbody>{entries.map(e => <tr key={e.name} style={{ cursor: "pointer" }} onClick={() => e.dir ? api(W(w.id, `/clients/${cid}/fs/list?path=${encodeURIComponent(path ? path + "/" + e.name : e.name)}`)).then(r => { setEntries(r.entries); setPath(path ? path + "/" + e.name : e.name); }) : api(W(w.id, `/clients/${cid}/fs/read?path=${encodeURIComponent((path ? path + "/" : "") + e.name)}`)).then(r => setView(atob(r.content_b64).slice(0, 20000)))}><td dir="ltr">{e.dir ? "📁" : "📄"} {e.name}</td></tr>)}</tbody></table>{view && <pre dir="ltr">{view}</pre>}</div></>}</>;
}

function Queue({ w }: { w: Worker }) {
  const { err, busy, go } = useAsync(); const [tasks, setTasks] = useState<WTask[]>([]); const [clients, setClients] = useState<any[]>([]); const [projects, setProjects] = useState<any[]>([]);
  const [f, setF] = useState({ client_id: "", project: "", kind: "claude", instruction: "", argv: "" }); const [conf, setConf] = useState<Record<string, string>>({});
  const load = useCallback(() => api<WTask[]>(W(w.id, "/tasks")).then(setTasks).catch(() => {}), [w.id]);
  useEffect(() => { load(); const i = setInterval(load, 2000); api<any[]>(W(w.id, "/clients")).then(c => { setClients(c); setF(x => ({ ...x, client_id: x.client_id || c[0]?.id || "" })); }).catch(() => {}); return () => clearInterval(i); }, [w.id, load]);
  useEffect(() => { if (f.client_id) api<any[]>(W(w.id, `/clients/${f.client_id}/projects`)).then(p => { setProjects(p); setF(x => ({ ...x, project: p[0]?.name ?? "" })); }).catch(() => {}); }, [w.id, f.client_id]);
  const act = (id: string, a: string, body: any = {}) => go(async () => { await api(W(w.id, `/tasks/${id}/${a}`), "POST", body); load(); });
  const counts = tasks.reduce((m: Record<string, number>, x) => ({ ...m, [x.status]: (m[x.status] ?? 0) + 1 }), {});
  return <><Err m={err} /><div className="card mt"><h3>Queue a task</h3><div className="tag">Nothing runs until you approve it. Approved tasks keep running on the worker even if this laptop disconnects. Deployments always require typing “Client name/project”.</div>
    <div className="grid"><div><label>Client</label><select value={f.client_id} onChange={e => setF({ ...f, client_id: e.target.value })}>{clients.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></div>
      <div><label>Project</label><select value={f.project} onChange={e => setF({ ...f, project: e.target.value })}>{projects.map(p => <option key={p.name}>{p.name}</option>)}</select></div>
      <div><label>Type</label><select value={f.kind} onChange={e => setF({ ...f, kind: e.target.value })}><option value="claude">Claude Code</option><option value="command">Command</option><option value="git_clone">git clone</option><option value="git_pull">git pull</option><option value="deploy">Deploy (production)</option></select></div></div>
    {f.kind === "claude" && <><label>Instruction</label><textarea rows={2} value={f.instruction} onChange={e => setF({ ...f, instruction: e.target.value })} /></>}
    {f.kind === "command" && <><label>Command (program + arguments, no shell)</label><input dir="ltr" value={f.argv} onChange={e => setF({ ...f, argv: e.target.value })} /></>}
    <button className="btn mt" disabled={busy || !f.project} onClick={() => go(async () => { await api(W(w.id, "/tasks"), "POST", { client_id: f.client_id, project: f.project, kind: f.kind, instruction: f.instruction, argv: f.kind === "command" ? f.argv.trim().split(/\s+/) : null }); load(); })}>Queue</button></div>
    <div className="card mt"><div className="row">{Object.entries(counts).map(([k, v]) => <span className="pill" key={k}>{k}: {v}</span>)}</div>
      <table className="mt"><thead><tr><th>Task</th><th>Client / project</th><th>Status</th><th></th></tr></thead><tbody>{tasks.map(x => <tr key={x.id}><td>{x.kind}{x.data.production && <span className="pill bad"> production</span>}<div className="tag">{x.data.instruction.slice(0, 60)}</div></td><td>{x.client_id}/{x.project}</td>
        <td>{x.status}{x.data.result && <div className="tag">{x.data.result.detail}</div>}</td><td><div className="row">
          {x.status === "awaiting_approval" && <>{x.data.production && <input style={{ width: 150 }} placeholder="Client name/project" value={conf[x.id] ?? ""} onChange={e => setConf({ ...conf, [x.id]: e.target.value })} />}<button className="btn" onClick={() => act(x.id, "approve", { approver: "Technician", confirm_text: conf[x.id] })}>{t("approve")}</button></>}
          {["running", "queued"].includes(x.status) && <button className="btn danger" onClick={() => act(x.id, "cancel")}>Cancel</button>}
          {(x.status === "queued" || (x.status === "running" && x.data.session_id)) && <button className="btn ghost" onClick={() => act(x.id, "pause")}>Pause</button>}
          {["paused", "interrupted"].includes(x.status) && !x.data.destructive && <button className="btn ghost" onClick={() => act(x.id, "resume")}>Resume</button>}
          {["failed", "interrupted", "needs_review", "cancelled"].includes(x.status) && <button className="btn ghost" title="needs a new approval" onClick={() => act(x.id, "retry")}>Retry…</button>}</div></td></tr>)}</tbody></table>
      {tasks.some(x => x.status === "needs_review") && <div className="tag mt">needs_review: the worker restarted while a destructive step was running. It was NOT repeated; inspect the target, then retry with a new approval.</div>}</div></>;
}

function LiveLog({ w }: { w: Worker }) {
  const [tasks, setTasks] = useState<WTask[]>([]); const [sel, setSel] = useState(""); const [lines, setLines] = useState<string[]>([]); const [status, setStatus] = useState(""); const last = useRef(0); const box = useRef<HTMLPreElement>(null);
  useEffect(() => { api<WTask[]>(W(w.id, "/tasks")).then(l => { setTasks(l); setSel(s => s || l.find(x => x.status === "running")?.id || l[0]?.id || ""); }).catch(() => {}); }, [w.id]);
  useEffect(() => { setLines([]); last.current = 0; if (!sel) return; let live = true;
    const tick = async () => { try { const d = await api<WTask>(W(w.id, `/tasks/${sel}?since=${last.current}`)); if (!live) return; setStatus(d.status); if (d.events.length) { last.current = d.last_seq; setLines(l => [...l, ...d.events.map(e => `${new Date(e.ts * 1000).toLocaleTimeString()} ${e.level === "error" ? "ERR " : e.level === "warn" ? "WARN " : ""}${e.msg}`)]); } } catch {} };
    tick(); const i = setInterval(tick, 1200); return () => { live = false; clearInterval(i); }; }, [w.id, sel]);
  useEffect(() => { if (box.current) box.current.scrollTop = box.current.scrollHeight; }, [lines.length]);
  return <div className="card mt"><div className="row"><select style={{ width: 360 }} value={sel} onChange={e => setSel(e.target.value)}>{tasks.map(x => <option key={x.id} value={x.id}>{x.client_id}/{x.project} · {x.kind} · {x.status}</option>)}</select><span className="pill">{status}</span>
    <span className="tag">Reconnecting later replays the full history from the worker.</span></div><pre ref={box} dir="ltr" style={{ maxHeight: 460 }}>{lines.join("\n")}</pre></div>;
}

function Terminal({ w }: { w: Worker }) {
  type Entry = { name: string; dir: boolean; size: number; mtime: number; link: boolean };
  type Run = { id: string; command: string; started: number; status: string; output: string; exit: number | null };
  const [clients, setClients] = useState<any[]>([]); const [cid, setCid] = useState(""); const [cmd, setCmd] = useState("");
  const [runs, setRuns] = useState<Run[]>([]); const [files, setFiles] = useState<Entry[]>([]); const [err, setErr] = useState(""); const [fileErr, setFileErr] = useState("");
  const [busy, setBusy] = useState(false); const [active, setActive] = useState<{ job: string; client: string; runId: string } | null>(null);
  const running = useRef(false); const [shell, setShell] = useState<"cmd" | "powershell">("cmd");
  const refreshFiles = useCallback(async (clientId: string) => {
    const r = await api<{ entries: Entry[] }>(W(w.id, `/clients/${clientId}/fs/list`));
    setFiles(r.entries); setFileErr("");
  }, [w.id]);
  useEffect(() => { api<any[]>(W(w.id, "/clients")).then(c => { setClients(c); setCid(c[0]?.id ?? ""); }).catch((e: any) => setErr(e.message)); }, [w.id]);
  useEffect(() => { setFiles([]); setFileErr(""); if (cid) refreshFiles(cid).catch((e: any) => setFileErr(e.message)); }, [cid, refreshFiles]);
  const runCommand = useCallback(async (line = cmd) => {
    const command = line.trim(); const clientId = cid;
    if (!command || !clientId || running.current) return;
    running.current = true; setBusy(true); setErr("");
    const runId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    setRuns(old => [...old, { id: runId, command, started: Date.now(), status: "Starting", output: "", exit: null }]);
    if (!line) setCmd("");
    const update = (patch: Partial<Run>) => setRuns(old => old.map(x => x.id === runId ? { ...x, ...patch } : x));
    try {
      const { job } = await api<{ job: string }>(W(w.id, `/clients/${clientId}/exec`), "POST", { argv: shell === "cmd" ? ["cmd.exe", "/d", "/c", command] : ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], timeout: 3600 });
      setActive({ job, client: clientId, runId }); update({ status: "Running" });
      let since = 0, output = "", done = false;
      for (let i = 0; i < 7200; i++) {
        const j = await api<any>(W(w.id, `/clients/${clientId}/jobs/${job}?since=${since}`));
        output += j.output ?? ""; since = j.next;
        update({ output, status: j.done ? (j.cancelled ? "Cancelled" : j.timed_out ? "Timed out" : "Completed") : "Running", exit: j.exit });
        if (j.done) { done = true; break; }
        await new Promise(resolve => setTimeout(resolve, 500));
      }
      if (!done) update({ status: "Polling limit reached" });
      try { await refreshFiles(clientId); } catch (e: any) { setFileErr(e.message); }
    } catch (e: any) { update({ status: "Failed", output: e.message }); setErr(e.message); }
    finally { setActive(null); running.current = false; setBusy(false); }
  }, [cid, cmd, shell, refreshFiles, w.id]);
  const cancel = async () => {
    if (!active) return;
    setRuns(old => old.map(x => x.id === active.runId ? { ...x, status: "Cancelling" } : x));
    try { await api(W(w.id, `/clients/${active.client}/jobs/${active.job}/cancel`), "POST"); }
    catch (e: any) { setErr(e.message); }
  };
  return <><Err m={err} /><div className="card mt">
    <div className="tag">Each run is one command (CMD or PowerShell) in a fresh process. This is not a persistent shell: interactive prompts and a `cd` that carries over to the next command are not supported. Commands use the selected client's workspace as their working directory; each run uses the existing Worker terminal permission, authenticated connection and audit log.</div>
    <div className="row mt"><label>Worker</label><span dir="ltr">{w.resources?.host ?? w.name} · {w.online ? "online" : "offline"}</span><label>Client workspace</label>
      <select style={{ width: 220 }} value={cid} onChange={e => setCid(e.target.value)} disabled={busy}>{clients.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></div>
    <div className="row mt"><select aria-label="Shell" style={{ width: 130 }} value={shell} onChange={e => setShell(e.target.value as "cmd" | "powershell")} disabled={busy}><option value="cmd">CMD</option><option value="powershell">PowerShell</option></select>
      <input aria-label="Windows command" dir="ltr" style={{ flex: 1 }} value={cmd} onChange={e => setCmd(e.target.value)} onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); void runCommand(); } }} placeholder={shell === "cmd" ? "Enter a CMD command" : "Enter a PowerShell command"} disabled={busy} />
      <button className="btn" disabled={!cid || !cmd.trim() || busy || !w.online} onClick={() => void runCommand()}>Run</button>
      {active && <button className="btn danger" onClick={() => void cancel()}>Cancel</button>}</div>
    <h3 className="mt">Command history</h3><div style={{ maxHeight: 360, overflow: "auto" }}>{runs.length ? runs.slice().reverse().map(x => <div key={x.id} className="card mt">
      <div className="row"><code dir="ltr">{x.command}</code><span className="pill">{x.status}{x.exit !== null ? ` · exit ${x.exit}` : ""}</span><time>{new Date(x.started).toLocaleTimeString()}</time></div>
      <pre dir="ltr" style={{ whiteSpace: "pre-wrap" }}>{x.output || (x.status === "Running" ? "Waiting for output…" : "[no output]")}</pre></div>) : <div className="tag">No commands run in this view yet.</div>}</div>
  </div><div className="card mt"><div className="row"><h3>Workspace files</h3><button className="btn ghost" disabled={!cid} onClick={() => refreshFiles(cid).catch((e: any) => setFileErr(e.message))}>Refresh</button></div>
    <Err m={fileErr} /><div className="tag">This list is read through the selected client's existing workspace-confined file API.</div>
    {files.length ? <div style={{ maxHeight: 220, overflow: "auto" }}>{files.map(f => <div key={f.name} className="row"><span>{f.dir ? "DIR" : "FILE"}{f.link ? " · LINK" : ""}</span><code dir="ltr">{f.name}</code><span className="tag">{f.dir ? "" : `${f.size} bytes`} · {new Date(f.mtime * 1000).toLocaleString()}</span></div>)}</div> : <div className="tag">Workspace is empty, or has not been loaded yet.</div>}</div></>;
}

function Desktop({ w, reload }: { w: Worker; reload: () => void }) {
  const { err, go } = useAsync(); const [id, setId] = useState(w.rustdesk_id ?? ""); const [res, setRes] = useState<any>(null);
  return <><Err m={err} /><div className="card mt"><h3>Remote desktop (RustDesk)</h3><div className="row"><input dir="ltr" value={id} onChange={e => setId(e.target.value)} placeholder="RustDesk ID of the worker" />
    <button className="btn ghost" onClick={() => go(async () => { await api(`/workers/${w.id}`, "PATCH", { rustdesk_id: id }); reload(); })}>Save</button>
    <button className="btn" disabled={!id} onClick={() => go(async () => setRes(await api("/connections/rustdesk", "POST", { remote_id: id })))}>Open</button></div>
    {res && <div className="mt">{res.launched ? res.note : `${res.reason} — ${res.install}`}</div>}
    <div className="tag mt">Use RustDesk only for tasks that need the screen (e.g. signing Claude Code in with the client's account). No unattended-access password is set by this app.</div></div></>;
}

function Security({ w, reload }: { w: Worker; reload: () => void }) {
  const { err, go } = useAsync(); const [creds, setCreds] = useState<any[]>([]); const [audit, setAudit] = useState<any[]>([]); const [fw, setFw] = useState(""); const [infra, setInfra] = useState("");
  const load = useCallback(() => { api<any[]>(W(w.id, "/credentials")).then(setCreds).catch(() => {}); api<any[]>(W(w.id, "/audit")).then(setAudit).catch(() => {}); }, [w.id]); useEffect(load, [load]);
  return <><Err m={err} /><div className="card mt"><h3>Access controls</h3><ul className="tag"><li>Network: private/Tailscale addresses only; nothing is exposed to the Internet.</li><li>Identity: TLS certificate pinned to the fingerprint you verified.</li><li>Locking: STOP file in the worker folder, <code>lock</code>, authorization-window expiry, emergency stop.</li><li>Removal: <code>revoke-all</code>, <code>service remove</code>, <code>wipe --name {w.name}</code> on the worker.</li></ul></div>
    <div className="card mt"><h3>Controller credentials</h3><table><tbody>{creds.map(c => <tr key={c.id}><td dir="ltr">{c.id}</td><td>{c.controller}</td><td>{c.revoked ? "revoked" : c.expires ? "expires " + new Date(c.expires * 1000).toLocaleDateString() : "no expiry"}</td><td>{!c.revoked && <button className="btn danger" onClick={() => go(async () => { await api(W(w.id, `/credentials/${c.id}`), "DELETE"); load(); reload(); })}>Revoke</button>}</td></tr>)}</tbody></table>
      <button className="btn danger mt" onClick={() => go(async () => { if (confirm(`Remove ${w.name} from this dashboard and revoke its credential?`)) { await api(`/workers/${w.id}`, "DELETE"); reload(); } })}>Remove worker from dashboard (revokes)</button></div>
    <div className="card mt"><h3>Client-LAN isolation (Windows Firewall)</h3><div className="tag">Generates a script that blocks outbound traffic to the client's private LAN except the approved systems and the gateway/DNS you list. Review, then run it on the worker as Administrator.</div>
      <div className="row mt"><input dir="ltr" style={{ flex: 1 }} placeholder="gateway/DNS IPs, comma separated" value={infra} onChange={e => setInfra(e.target.value)} /><button className="btn" onClick={() => go(async () => setFw((await api(W(w.id, `/firewall-script?infra=${encodeURIComponent(infra)}`))).script))}>Generate</button></div>{fw && <pre dir="ltr">{fw}</pre>}</div>
    <div className="card mt"><h3>Worker audit log</h3><table><tbody>{audit.slice(0, 60).map((a, i) => <tr key={i}><td>{new Date(a.ts * 1000).toLocaleString()}</td><td>{a.actor}</td><td>{a.action}</td><td className="tag">{a.detail}</td></tr>)}</tbody></table></div></>;
}
