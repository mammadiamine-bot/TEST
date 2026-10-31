import { useState } from "react";
import { api } from "./api";
import { DataTable, Err, OrgBar, Pill, Spark, useData, useMe, useOrgId, when } from "./kit";
import type { Ctx } from "./pages";

type W = { id: string; name: string; address: string; state: string; path: string; site: string; tags: string };
type Job = { id: string; title: string; risk: string; status: string; approval: string; created_by: string; created_at: number };

const useAct = () => {
  const [err, setErr] = useState(""); const [busy, setBusy] = useState(false); const [ok, setOk] = useState("");
  const go = async <T,>(f: () => Promise<T>, done = ""): Promise<T | undefined> => { setBusy(true); setErr(""); setOk(""); try { const r = await f(); setOk(done); return r; } catch (e: any) { setErr(e.message); } finally { setBusy(false); } };
  return { err, busy, ok, go };
};
const NoOrg = () => <div className="card">Choose or create a client first.</div>;

// ---------------------------------------------------------------- Devices (fleet) + device details
export function Fleet({ c }: { c: Ctx }) {
  const oid = useOrgId(); const fleet = useData<{ sites: any[] }>(oid ? `/orgs/${oid}/fleet` : null, 10000);
  const [sel, setSel] = useState<string[]>([]); const [open, setOpen] = useState<string>(""); const [state, setState] = useState("all");
  const rows: W[] = (fleet.data?.sites ?? []).flatMap(s => s.workers.map((w: any) => ({ id: w.id, name: w.name, address: w.address, state: w.state ?? w.status, path: w.path, site: s.name, tags: "" })));
  const shown = rows.filter(r => state === "all" || r.state === state);
  return <><OrgBar />{!oid ? <NoOrg /> : <>
    <div className="card"><DataTable rows={fleet.data ? shown : null} loading={fleet.loading} error={fleet.err} selectable selected={sel} onSelect={setSel} onRow={r => setOpen(r.id)} empty="No Windows machines enrolled for this client yet — see “Clients & sites → Connect the next computer”."
      filters={<select aria-label="State filter" style={{ width: 150 }} value={state} onChange={e => setState(e.target.value)}>{["all", "online", "reconnecting", "offline", "needs_pairing"].map(s => <option key={s}>{s}</option>)}</select>}
      cols={[{ head: "Machine", cell: r => <b>{r.name}</b>, text: r => r.name }, { head: "Site", cell: r => r.site, text: r => r.site }, { head: "State", cell: r => <Pill v={r.state} />, text: r => r.state },
        { head: "Address", cell: r => <span dir="ltr">{r.address}</span>, text: r => r.address }, { head: "Reached via", cell: r => r.path, text: r => r.path }]} />
      {sel.length > 0 && <BulkBar ids={sel} oid={oid} />}</div>
    {open && <DeviceDetail wid={open} oid={oid} c={c} onClose={() => setOpen("")} />}</>}</>;
}

function BulkBar({ ids, oid }: { ids: string[]; oid: string }) {
  const { err, busy, ok, go } = useAct(); const t = useData<any[]>("/ops/templates"); const [tpl, setTpl] = useState("inventory.system");
  return <div className="row mt"><span className="tag">Bulk action on {ids.length} machine(s):</span><select aria-label="Template" style={{ width: 360 }} value={tpl} onChange={e => setTpl(e.target.value)}>{(t.data ?? []).map(x => <option key={x.id} value={x.id}>{x.title} [{x.risk}]</option>)}</select>
    <button className="btn" disabled={busy || ["service.restart", "service.stop"].includes(tpl)} onClick={() => go(() => api(`/orgs/${oid}/jobs`, "POST", { template: tpl, targets: ids }), "Job created — see Jobs.")}>Dispatch</button><Err m={err} />{ok && <span className="tag">{ok}</span>}</div>;
}

const KINDS = ["inventory.system", "inventory.apps", "services.list", "processes.top", "eventlog.recent", "windows.update.status", "disk.usage", "network.diag", "backup.status"];
function DeviceDetail({ wid, oid, c, onClose }: { wid: string; oid: string; c: Ctx; onClose: () => void }) {
  const [kind, setKind] = useState(KINDS[0]); const { err, busy, go } = useAct();
  const latest = useData<{ ts: number | null; data: any }>(`/workers/${wid}/inventory/${kind}`); const runs = useData<any[]>(`/workers/${wid}/runs`, 8000); const m = useData<any[]>(`/workers/${wid}/metrics?hours=6`, 15000);
  const data = latest.data?.data; const list = Array.isArray(data) ? data : null;
  return <div className="card mt"><div className="row"><h3 style={{ flex: 1 }}>Device details · <span dir="ltr">{wid}</span></h3><button className="btn ghost" onClick={() => c.goto("workers")}>Open terminal &amp; files</button><button className="btn ghost" onClick={onClose}>Close</button></div>
    <div className="grid"><Spark label="CPU (6 h)" values={(m.data ?? []).map(x => x.cpu)} /><Spark label="Memory (6 h)" values={(m.data ?? []).map(x => x.ram)} /><Spark label="Disk (6 h)" values={(m.data ?? []).map(x => x.disk)} /></div>
    <div className="row mt"><select aria-label="Inventory" style={{ width: 240 }} value={kind} onChange={e => setKind(e.target.value)}>{KINDS.map(k => <option key={k}>{k}</option>)}</select>
      <button className="btn" disabled={busy} onClick={() => go(async () => { const r = await api<any>(`/workers/${wid}/inventory/${kind}`, "POST"); if (r.status !== "ok") throw new Error(r.error || r.status); latest.reload(); runs.reload(); })}>{busy ? "Collecting…" : "Collect now (read-only)"}</button>
      <span className="tag">{latest.data?.ts ? "collected " + when(latest.data.ts) : "not collected yet"}</span></div><Err m={err || latest.err} />
    {list ? <div className="mt" style={{ maxHeight: 300, overflow: "auto" }}><DataTable rows={list.map((x, i) => ({ id: String(i), ...x }))} pageSize={10} cols={Object.keys(list[0] ?? {}).slice(0, 6).map(k => ({ head: k, cell: (r: any) => String(typeof r[k] === "object" ? JSON.stringify(r[k]) : r[k] ?? ""), text: (r: any) => String(r[k] ?? "") }))} /></div>
      : data ? <pre dir="ltr">{JSON.stringify(data, null, 2)}</pre> : null}
    <h3 className="mt">Job history on this machine</h3><DataTable rows={runs.data} loading={runs.loading} error={runs.err} pageSize={6} cols={[{ head: "When", cell: r => when(r.started_at) }, { head: "Job", cell: r => r.title, text: r => r.title }, { head: "Risk", cell: r => r.risk }, { head: "Result", cell: r => <Pill v={r.status} />, text: r => r.status }, { head: "Exit", cell: r => r.exit_code ?? "—" }]} /></div>;
}

// ---------------------------------------------------------------- Jobs, approvals, schedules
export function Jobs({ c }: { c: Ctx }) {
  const oid = useOrgId(); const jobs = useData<Job[]>(oid ? `/orgs/${oid}/jobs` : null, 4000); const fleet = useData<{ sites: any[] }>(oid ? `/orgs/${oid}/fleet` : null);
  const tpls = useData<any[]>("/ops/templates"); const scripts = useData<any[]>(oid ? `/orgs/${oid}/scripts` : null); const sch = useData<any[]>(oid ? `/orgs/${oid}/schedules` : null, 10000);
  const me = useMe(); const { err, busy, go } = useAct();
  const [f, setF] = useState({ mode: "template", template: "inventory.system", param: "", script: "", shell: "powershell", body: "", title: "", retries: 0, every: 0 }); const [targets, setTargets] = useState<string[]>([]); const [confirm, setConfirm] = useState<Record<string, string>>({}); const [view, setView] = useState("");
  const workers: any[] = (fleet.data?.sites ?? []).flatMap(s => s.workers); const senior = me?.role === "owner" || me?.role === "administrator";
  const needsName = f.mode === "template" && ["service.restart", "service.stop"].includes(f.template);
  const payload = () => ({ targets, retries: f.retries, title: f.title || undefined, ...(f.mode === "template" ? { template: f.template, params: needsName ? { name: f.param } : undefined } : f.mode === "script" ? { script_id: f.script } : { shell: f.shell, body: f.body }) });
  const act = (jid: string, a: string, body: any = {}) => go(async () => { await api(`/orgs/${oid}/jobs/${jid}/${a}`, "POST", body); jobs.reload(); });
  return <><OrgBar />{!oid ? <NoOrg /> : <>
    <div className="card"><h3>New job</h3><Err m={err} />
      <div className="grid"><div><label>Source</label><select value={f.mode} onChange={e => setF({ ...f, mode: e.target.value })}><option value="template">Built-in template</option><option value="script">Saved script</option><option value="adhoc">Ad-hoc command</option></select></div>
        {f.mode === "template" && <div><label>Template</label><select value={f.template} onChange={e => setF({ ...f, template: e.target.value })}>{(tpls.data ?? []).map(x => <option key={x.id} value={x.id}>{x.title} [{x.risk}]</option>)}</select></div>}
        {needsName && <div><label>Service name</label><input dir="ltr" value={f.param} onChange={e => setF({ ...f, param: e.target.value })} /></div>}
        {f.mode === "script" && <div><label>Script</label><select value={f.script} onChange={e => setF({ ...f, script: e.target.value })}><option value="">—</option>{(scripts.data ?? []).map(s => <option key={s.id} value={s.id}>{s.name} v{s.latest} [{s.risk}]</option>)}</select></div>}
        {f.mode === "adhoc" && <><div><label>Shell</label><select value={f.shell} onChange={e => setF({ ...f, shell: e.target.value })}><option>powershell</option><option>cmd</option></select></div><div style={{ gridColumn: "1/-1" }}><label>Command</label><textarea dir="ltr" rows={3} value={f.body} onChange={e => setF({ ...f, body: e.target.value })} /></div></>}
        <div><label>Retries on unreachable machine</label><input type="number" min={0} max={5} value={f.retries} onChange={e => setF({ ...f, retries: +e.target.value })} /></div></div>
      <label>Target machines ({targets.length})</label><div className="row">{workers.map(w => <label key={w.id} style={{ margin: 0 }} className="row"><input type="checkbox" style={{ width: "auto" }} checked={targets.includes(w.id)} onChange={e => setTargets(e.target.checked ? [...targets, w.id] : targets.filter(x => x !== w.id))} />{w.name}</label>)}{!workers.length && <span className="tag">No machines.</span>}</div>
      <div className="tag mt">Read-only templates run immediately. Anything that changes a machine waits for approval by a different administrator; reboot/shutdown also need the typed phrase.</div>
      <button className="btn mt" disabled={busy || !targets.length || (f.mode === "adhoc" && !f.body.trim()) || (f.mode === "script" && !f.script)} onClick={() => go(async () => { await api(`/orgs/${oid}/jobs`, "POST", payload()); jobs.reload(); })}>Create job</button></div>
    <div className="card mt"><h3>Jobs</h3><DataTable rows={jobs.data} loading={jobs.loading} error={jobs.err} onRow={r => setView(view === r.id ? "" : r.id)}
      cols={[{ head: "When", cell: r => when(r.created_at) }, { head: "Job", cell: r => <b>{r.title}</b>, text: r => r.title }, { head: "Risk", cell: r => <span className={"risk-" + (r.risk === "read" ? "read_only" : r.risk === "modify" ? "low" : "high")}>{r.risk}</span>, text: r => r.risk }, { head: "Status", cell: r => <Pill v={r.status} />, text: r => r.status }, { head: "By", cell: r => r.created_by, text: r => r.created_by },
        { head: "", cell: r => <div className="row" onClick={e => e.stopPropagation()}>
          {r.status === "pending_approval" && senior && <>{r.risk === "disruptive" && <input aria-label="Confirmation phrase" placeholder='type "I APPROVE"' style={{ width: 130 }} value={confirm[r.id] ?? ""} onChange={e => setConfirm({ ...confirm, [r.id]: e.target.value })} />}<button className="btn" onClick={() => act(r.id, "approve", { confirm: confirm[r.id] })}>Approve</button><button className="btn ghost" onClick={() => act(r.id, "reject")}>Reject</button></>}
          {["queued", "running", "pending_approval"].includes(r.status) && <button className="btn danger" onClick={() => act(r.id, "cancel")}>Cancel</button>}</div> }]} />
      {view && <JobDetail oid={oid} jid={view} />}</div>
    <div className="card mt"><h3>Schedules</h3><ScheduleForm oid={oid} workers={workers} tpls={tpls.data ?? []} onDone={sch.reload} senior={senior} />
      <DataTable rows={sch.data} loading={sch.loading} error={sch.err} cols={[{ head: "Title", cell: r => r.title, text: r => r.title }, { head: "Every", cell: r => r.every_minutes + " min" }, { head: "Risk", cell: r => r.risk }, { head: "State", cell: r => (r.enabled ? <Pill v="active" /> : <span className="pill bad">waiting for approval</span>) }, { head: "Next", cell: r => when(r.next_run) },
        { head: "", cell: r => <div className="row">{!r.enabled && senior && <button className="btn" onClick={() => go(async () => { await api(`/orgs/${oid}/schedules/${r.id}/approve`, "POST"); sch.reload(); })}>Approve</button>}{senior && <button className="btn danger" onClick={() => go(async () => { await api(`/orgs/${oid}/schedules/${r.id}`, "DELETE"); sch.reload(); })}>Delete</button>}</div> }]} /></div></>}</>;
}
function JobDetail({ oid, jid }: { oid: string; jid: string }) {
  const j = useData<any>(`/orgs/${oid}/jobs/${jid}`, 2500);
  return <div className="mt">{j.data?.runs.map((r: any) => <div key={r.id} className="card mt"><div className="row"><b dir="ltr">{r.worker_id}</b><Pill v={r.status} /><span className="tag">attempt {r.attempt} · exit {r.exit_code ?? "—"} · {when(r.finished_at)}</span></div>{r.error && <Err m={r.error} />}<pre dir="ltr">{r.output || "(no output)"}</pre></div>)}</div>;
}
function ScheduleForm({ oid, workers, tpls, onDone, senior }: { oid: string; workers: any[]; tpls: any[]; onDone: () => void; senior: boolean }) {
  const { err, busy, go } = useAct(); const [f, setF] = useState({ title: "", template: "inventory.system", every: 1440, target: "" });
  if (!senior) return <div className="tag">Only an administrator can create schedules.</div>;
  return <><Err m={err} /><div className="row"><input placeholder="Title" style={{ width: 200 }} value={f.title} onChange={e => setF({ ...f, title: e.target.value })} /><select aria-label="Schedule template" style={{ width: 300 }} value={f.template} onChange={e => setF({ ...f, template: e.target.value })}>{tpls.filter(t => t.risk === "read").map(t => <option key={t.id} value={t.id}>{t.title}</option>)}</select>
    <select aria-label="Schedule target" style={{ width: 180 }} value={f.target} onChange={e => setF({ ...f, target: e.target.value })}><option value="">machine…</option>{workers.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}</select><input type="number" aria-label="Every N minutes" style={{ width: 110 }} min={5} value={f.every} onChange={e => setF({ ...f, every: +e.target.value })} />
    <button className="btn" disabled={busy || !f.title || !f.target} onClick={() => go(async () => { await api(`/orgs/${oid}/schedules`, "POST", { title: f.title, template: f.template, targets: [f.target], every_minutes: f.every }); onDone(); })}>Add read-only schedule</button></div></>;
}

// ---------------------------------------------------------------- Scripts
export function Scripts() {
  const oid = useOrgId(); const s = useData<any[]>(oid ? `/orgs/${oid}/scripts` : null); const { err, busy, go } = useAct(); const me = useMe();
  const [f, setF] = useState({ name: "", shell: "powershell", body: "", risk: "modify" }); const [open, setOpen] = useState(""); const v = useData<any[]>(open && oid ? `/orgs/${oid}/scripts/${open}/versions` : null); const [nb, setNb] = useState("");
  const senior = me?.role === "owner" || me?.role === "administrator";
  return <><OrgBar />{!oid ? <NoOrg /> : <>
    <div className="card"><h3>New script</h3><Err m={err} /><div className="grid"><div><label>Name</label><input value={f.name} onChange={e => setF({ ...f, name: e.target.value })} /></div><div><label>Shell</label><select value={f.shell} onChange={e => setF({ ...f, shell: e.target.value })}><option>powershell</option><option>cmd</option></select></div>
      <div><label>Risk class</label><select value={f.risk} onChange={e => setF({ ...f, risk: e.target.value })}><option value="modify">modify (needs approval)</option><option value="read" disabled={!senior}>read-only (administrators only)</option><option value="disruptive">disruptive</option></select></div></div>
      <label>Body</label><textarea dir="ltr" rows={5} value={f.body} onChange={e => setF({ ...f, body: e.target.value })} />
      <button className="btn mt" disabled={busy || !f.name || !f.body.trim()} onClick={() => go(async () => { await api(`/orgs/${oid}/scripts`, "POST", f); setF({ ...f, name: "", body: "" }); s.reload(); })}>Save as version 1</button></div>
    <div className="card mt"><h3>Library</h3><DataTable rows={s.data} loading={s.loading} error={s.err} onRow={r => setOpen(r.id)} cols={[{ head: "Name", cell: r => <b>{r.name}</b>, text: r => r.name }, { head: "Shell", cell: r => r.shell }, { head: "Risk", cell: r => r.risk }, { head: "Latest", cell: r => "v" + r.latest }]} />
      {open && <div className="mt"><DataTable rows={(v.data ?? []).map(x => ({ id: String(x.version), ...x }))} loading={v.loading} error={v.err} pageSize={5} cols={[{ head: "Version", cell: r => "v" + r.version }, { head: "By", cell: r => r.author }, { head: "SHA-256", cell: r => <span dir="ltr" className="tag">{r.sha256.slice(0, 16)}…</span> }, { head: "Body", cell: r => <code dir="ltr">{r.body.slice(0, 80)}</code> }]} />
        <label>New version</label><textarea dir="ltr" rows={3} value={nb} onChange={e => setNb(e.target.value)} /><div className="tag">A technician's edit sets the script back to “modify” until an administrator re-classifies it.</div>
        <button className="btn mt" disabled={busy || !nb.trim()} onClick={() => go(async () => { await api(`/orgs/${oid}/scripts/${open}/versions`, "POST", { body: nb }); setNb(""); v.reload(); s.reload(); })}>Save new version</button></div>}</div></>}</>;
}

// ---------------------------------------------------------------- Monitoring and alerts
export function Monitoring() {
  const oid = useOrgId(); const rep = useData<any>(oid ? `/orgs/${oid}/reports/fleet` : null, 15000); const al = useData<any[]>(oid ? `/orgs/${oid}/alerts` : null, 8000);
  const rules = useData<any[]>(oid ? `/orgs/${oid}/alert-rules` : null); const pol = useData<any[]>(oid ? `/orgs/${oid}/notify-policies` : null); const ntf = useData<any[]>(oid ? `/orgs/${oid}/notifications` : null);
  const { err, busy, go } = useAct(); const me = useMe(); const senior = me?.role === "owner" || me?.role === "administrator"; const [state, setState] = useState("open");
  const [r, setR] = useState({ metric: "cpu", threshold: 90, consecutive: 3, severity: "warning" }); const [email, setEmail] = useState("");
  const list = (al.data ?? []).filter(a => state === "all" || (state === "open" ? a.state !== "resolved" : a.state === state));
  return <><OrgBar />{!oid ? <NoOrg /> : <>
    <div className="grid"><div className="card"><h3>Fleet availability (24 h)</h3><div className="big">{rep.data?.fleet_availability_pct ?? "—"}{rep.data?.fleet_availability_pct != null && "%"}</div></div><div className="card"><h3>Open alerts</h3><div className="big">{rep.data?.open_alerts ?? "—"}</div></div><div className="card"><h3>Machines monitored</h3><div className="big">{rep.data?.workers.length ?? "—"}</div></div></div>
    <div className="card mt"><h3>Fleet health</h3><DataTable rows={rep.data ? rep.data.workers.map((w: any) => ({ id: w.worker, ...w })) : null} loading={rep.loading} error={rep.err} pageSize={8} cols={[{ head: "Machine", cell: (w: any) => <b>{w.name}</b>, text: (w: any) => w.name }, { head: "Availability", cell: (w: any) => (w.availability_pct ?? "—") + "%" }, { head: "CPU", cell: (w: any) => w.avg_cpu ?? "—" }, { head: "RAM", cell: (w: any) => w.avg_ram ?? "—" }, { head: "Disk", cell: (w: any) => w.avg_disk ?? "—" }, { head: "Alerts", cell: (w: any) => w.open_alerts }]} /></div>
    <div className="card mt"><h3>Alerts</h3><Err m={err} /><DataTable rows={al.data ? list : null} loading={al.loading} error={al.err} empty="No alerts. Quiet is good."
      filters={<select aria-label="Alert state" style={{ width: 140 }} value={state} onChange={e => setState(e.target.value)}><option>open</option><option>resolved</option><option>all</option></select>}
      cols={[{ head: "Severity", cell: a => <span className={"sev " + (a.severity === "critical" ? "critical" : a.severity === "warning" ? "medium" : "info")}>{a.severity}</span>, text: a => a.severity }, { head: "Alert", cell: a => a.message, text: a => a.message }, { head: "Seen", cell: a => `${a.count}× · ${when(a.last_seen)}` }, { head: "State", cell: a => <Pill v={a.state} />, text: a => a.state },
        { head: "", cell: a => <div className="row">{a.state === "open" && <button className="btn ghost" onClick={() => go(async () => { await api(`/orgs/${oid}/alerts/${a.id}/ack`, "POST"); al.reload(); })}>Acknowledge</button>}<button className="btn ghost" onClick={() => go(async () => { await api(`/orgs/${oid}/alerts/${a.id}/ticket`, "POST"); }, "")}>→ Ticket</button></div> }]} /></div>
    <div className="grid mt"><div className="card"><h3>Threshold rules</h3><DataTable rows={rules.data} loading={rules.loading} error={rules.err} pageSize={5} cols={[{ head: "Rule", cell: x => `${x.metric.toUpperCase()} > ${x.threshold}% for ${x.consecutive} checks (${x.severity})` }, { head: "", cell: x => senior && <button className="btn danger" onClick={() => go(async () => { await api(`/orgs/${oid}/alert-rules/${x.id}`, "DELETE"); rules.reload(); })}>×</button> }]} />
      {senior ? <div className="row mt"><select aria-label="Metric" style={{ width: 90 }} value={r.metric} onChange={e => setR({ ...r, metric: e.target.value })}><option>cpu</option><option>ram</option><option>disk</option></select><input type="number" aria-label="Threshold" style={{ width: 80 }} value={r.threshold} onChange={e => setR({ ...r, threshold: +e.target.value })} /><select aria-label="Severity" style={{ width: 110 }} value={r.severity} onChange={e => setR({ ...r, severity: e.target.value })}><option>info</option><option>warning</option><option>critical</option></select>
        <button className="btn" disabled={busy} onClick={() => go(async () => { await api(`/orgs/${oid}/alert-rules`, "POST", r); rules.reload(); })}>Add rule</button></div> : <div className="tag mt">Rules are managed by administrators.</div>}</div>
      <div className="card"><h3>Email notifications</h3><DataTable rows={pol.data} loading={pol.loading} error={pol.err} pageSize={5} cols={[{ head: "To", cell: x => x.target }, { head: "From severity", cell: x => x.min_severity }, { head: "", cell: x => senior && <button className="btn danger" onClick={() => go(async () => { await api(`/orgs/${oid}/notify-policies/${x.id}`, "DELETE"); pol.reload(); })}>×</button> }]} />
        {senior && <div className="row mt"><input type="email" aria-label="Email" placeholder="ops@customer.example" value={email} onChange={e => setEmail(e.target.value)} /><button className="btn" disabled={busy || !email} onClick={() => go(async () => { await api(`/orgs/${oid}/notify-policies`, "POST", { target: email }); setEmail(""); pol.reload(); })}>Add</button></div>}
        <div className="tag mt">Delivery log: {(ntf.data ?? []).slice(0, 3).map(n => n.status).join(", ") || "none yet"}. Status “not_sent_no_smtp” means the server has no SMTP settings — nothing was sent.</div></div></div></>}</>;
}

// ---------------------------------------------------------------- AI Technician
export function AiTech() {
  const oid = useOrgId(); const fleet = useData<{ sites: any[] }>(oid ? `/orgs/${oid}/fleet` : null); const runs = useData<any[]>(oid ? `/orgs/${oid}/ai/runs` : null); const st = useData<any>("/ai/status");
  const { err, busy, ok, go } = useAct(); const [w, setW] = useState(""); const [q, setQ] = useState(""); const [res, setRes] = useState<any>(null); const me = useMe();
  const workers: any[] = (fleet.data?.sites ?? []).flatMap(s => s.workers);
  return <><OrgBar />{!oid ? <NoOrg /> : <>
    <div className="card"><h3>Ask the AI Technician</h3><div className="tag">Order of work: built-in checks first, then the local model ({st.data?.selected ?? "none installed"}). The AI only reads. Anything it suggests becomes a pending job that a person must approve.</div><Err m={err} />
      <div className="row mt"><select aria-label="Machine" style={{ width: 220 }} value={w} onChange={e => setW(e.target.value)}><option value="">machine…</option>{workers.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</select><input aria-label="Request" style={{ flex: 1 }} placeholder="e.g. The computer is slow since this morning" value={q} onChange={e => setQ(e.target.value)} />
        <button className="btn" disabled={busy || !w || q.length < 3} onClick={() => go(async () => { setRes(await api(`/orgs/${oid}/ai/ask`, "POST", { worker_id: w, request: q })); runs.reload(); })}>{busy ? "Working… (read-only checks)" : "Diagnose"}</button></div></div>
    {res && <div className="card mt"><div className="row"><h3 style={{ flex: 1 }}>Result</h3><span className="pill">{res.engine}{res.model ? ` · ${res.model}` : ""}</span><span className="tag">{res.tokens_in + res.tokens_out} tokens</span></div><pre>{res.answer}</pre>
      {res.plan.proposals.map((p: any) => <div key={p.n} className="card mt"><b>Proposed: {p.finding}</b><pre dir="ltr">{p.preview.body}</pre><div className="tag">Not executed. Submitting creates a job that waits for approval.</div>
        <button className="btn" disabled={busy || me?.role === "viewer"} onClick={() => go(async () => { await api(`/orgs/${oid}/ai/runs/${res.id}/proposals/${p.n}/submit`, "POST"); }, "Submitted — approve it in Jobs.")}>Submit for approval</button></div>)}{ok && <div className="tag">{ok}</div>}</div>}
    <div className="card mt"><h3>AI history</h3><DataTable rows={runs.data} loading={runs.loading} error={runs.err} pageSize={8} cols={[{ head: "When", cell: r => when(r.ts) }, { head: "Request", cell: r => r.request, text: r => r.request }, { head: "Engine", cell: r => r.engine }, { head: "Tokens", cell: r => r.tokens_in + r.tokens_out }, { head: "By", cell: r => r.actor }]} /></div></>}</>;
}

// ---------------------------------------------------------------- Tickets and reports
export function Tickets() {
  const oid = useOrgId(); const t = useData<any[]>(oid ? `/orgs/${oid}/tickets` : null, 10000); const sla = useData<any>(oid ? `/orgs/${oid}/reports/sla` : null, 20000); const me = useMe();
  const { err, busy, go } = useAct(); const [f, setF] = useState({ title: "", body: "", priority: "normal" });
  return <><OrgBar />{!oid ? <NoOrg /> : <>
    <div className="grid"><div className="card"><h3>Tickets</h3><div className="big">{sla.data?.tickets ?? "—"}</div></div><div className="card"><h3>SLA met</h3><div className="big">{sla.data?.sla_pct ?? "—"}{sla.data?.sla_pct != null && "%"}</div></div><div className="card"><h3>Overdue</h3><div className="big">{sla.data?.overdue_open ?? "—"}</div></div></div>
    <div className="card mt"><h3>New ticket</h3><Err m={err} /><div className="grid"><div><label>Title</label><input value={f.title} onChange={e => setF({ ...f, title: e.target.value })} /></div><div><label>Priority (SLA)</label><select value={f.priority} onChange={e => setF({ ...f, priority: e.target.value })}><option value="urgent">urgent · 4 h</option><option value="high">high · 8 h</option><option value="normal">normal · 24 h</option><option value="low">low · 72 h</option></select></div></div>
      <label>Details</label><textarea rows={2} value={f.body} onChange={e => setF({ ...f, body: e.target.value })} /><button className="btn mt" disabled={busy || !f.title} onClick={() => go(async () => { await api(`/orgs/${oid}/tickets`, "POST", f); setF({ ...f, title: "", body: "" }); t.reload(); sla.reload(); })}>Open ticket</button></div>
    <div className="card mt"><DataTable rows={t.data} loading={t.loading} error={t.err} cols={[{ head: "Opened", cell: r => when(r.created_at) }, { head: "Title", cell: r => <b>{r.title}</b>, text: r => r.title }, { head: "Priority", cell: r => r.priority }, { head: "Due", cell: r => when(r.due_at) }, { head: "Status", cell: r => <Pill v={r.status} />, text: r => r.status },
      { head: "", cell: r => me?.role !== "customer" && r.status !== "resolved" && <button className="btn ghost" onClick={() => go(async () => { await api(`/orgs/${oid}/tickets/${r.id}`, "PATCH", { status: "resolved" }); t.reload(); sla.reload(); })}>Resolve</button> }]} /></div></>}</>;
}

// ---------------------------------------------------------------- Technicians and roles
export function Team() {
  const oid = useOrgId(); const p = useData<any[]>("/principals"); const inv = useData<any[]>(oid ? `/orgs/${oid}/invitations` : null); const me = useMe(); const { err, busy, go } = useAct();
  const [f, setF] = useState({ role: "technician", note: "" }); const [secret, setSecret] = useState(""); const senior = me?.role === "owner" || me?.role === "administrator";
  const mine = (p.data ?? []).filter(x => x.org_ids.includes(oid));
  return <><OrgBar />{!oid ? <NoOrg /> : !senior ? <div className="card">Only owners and administrators manage people.</div> : <>
    <div className="card"><h3>Invite a person</h3><Err m={err} /><div className="row"><select aria-label="Role" style={{ width: 200 }} value={f.role} onChange={e => setF({ ...f, role: e.target.value })}>{["administrator", "technician", "viewer", "customer"].filter(r => r !== "administrator" || me?.role === "owner").map(r => <option key={r}>{r}</option>)}</select><input placeholder="Who is this for? (note)" value={f.note} onChange={e => setF({ ...f, note: e.target.value })} />
      <button className="btn" disabled={busy} onClick={() => go(async () => { const r = await api<any>(`/orgs/${oid}/invitations`, "POST", f); setSecret(r.token); inv.reload(); })}>Create invitation</button></div>
      <div className="tag mt">Roles: administrator (manages this client's people, sites, rules, approvals) · technician (runs jobs, cannot approve) · viewer (read-only) · customer (own summary, tickets, bills).</div>
      {secret && <div className="banner mt">Give this to the person now — shown once, single use: <code dir="ltr">{secret}</code> <button className="btn ghost" onClick={() => setSecret("")}>Done</button></div>}</div>
    <div className="card mt"><h3>People with access to this client</h3><DataTable rows={p.data ? mine : null} loading={p.loading} error={p.err} pageSize={8} cols={[{ head: "Name", cell: x => x.name, text: x => x.name }, { head: "Role", cell: x => x.role, text: x => x.role }, { head: "Last used", cell: x => when(x.last_used) }, { head: "State", cell: x => <Pill v={x.revoked ? "expired" : "active"} /> },
      { head: "", cell: x => !x.revoked && <div className="row"><button className="btn ghost" onClick={() => go(async () => { const r = await api<any>(`/principals/${x.id}/rotate`, "POST"); setSecret(r.token); })}>Rotate token</button><button className="btn danger" onClick={() => go(async () => { await api(`/principals/${x.id}`, "DELETE"); p.reload(); })}>Revoke</button></div> }]} /></div>
    <div className="card mt"><h3>Invitations</h3><DataTable rows={inv.data} loading={inv.loading} error={inv.err} pageSize={6} cols={[{ head: "Role", cell: x => x.role }, { head: "Created", cell: x => when(x.created_at) }, { head: "State", cell: x => (x.used_at ? "used" : x.expires_at < Date.now() / 1000 ? "expired/revoked" : "open") }, { head: "", cell: x => !x.used_at && <button className="btn ghost" onClick={() => go(async () => { await api(`/orgs/${oid}/invitations/${x.id}`, "DELETE"); inv.reload(); })}>Revoke</button> }]} /></div></>}</>;
}

// ---------------------------------------------------------------- Subscriptions
export function Billing() {
  const oid = useOrgId(); const s = useData<any>(oid ? `/orgs/${oid}/subscription` : null); const plans = useData<any>("/plans"); const h = useData<any[]>(oid ? `/orgs/${oid}/subscription/history` : null); const inv = useData<any[]>(oid ? `/orgs/${oid}/invoices` : null);
  const me = useMe(); const { err, busy, ok, go } = useAct(); const owner = me?.role === "owner"; const [plan, setPlan] = useState("professional"); const [lic, setLic] = useState("");
  const u = s.data?.usage; const e = s.data?.entitlements;
  return <><OrgBar />{!oid ? <NoOrg /> : <>
    <div className="grid"><div className="card"><h3>Plan</h3><div className="big">{s.data?.plan ?? "—"}</div><Pill v={s.data?.status ?? "…"} /><div className="tag">until {when(s.data?.period_end)}</div></div>
      <div className="card"><h3>Machines</h3><div className="big">{u?.devices_now ?? "—"} / {e?.max_devices ?? "—"}</div><div className="tag">peak this month {u?.devices_peak ?? "—"}</div></div>
      <div className="card"><h3>Usage ({u?.month})</h3><div>{u?.jobs ?? 0} jobs · {u?.ai_tokens_month ?? 0} AI tokens</div><div className="tag">features: {(e?.features ?? []).join(", ")}</div></div></div>
    <Err m={err || s.err} />{ok && <div className="tag">{ok}</div>}
    {owner ? <div className="card mt"><h3>Owner actions</h3><div className="row"><select aria-label="Plan" style={{ width: 180 }} value={plan} onChange={e => setPlan(e.target.value)}>{Object.keys(plans.data ?? {}).filter(p => p !== "trial").map(p => <option key={p}>{p}</option>)}</select>
      <button className="btn" disabled={busy} onClick={() => go(async () => { await api(`/orgs/${oid}/subscription`, "PUT", { plan }); s.reload(); h.reload(); }, "Plan changed.")}>Change plan (30 days)</button>
      <button className="btn ghost" disabled={busy} onClick={() => go(async () => { const r = await api<any>(`/orgs/${oid}/subscription/license`, "POST", { plan, days: 365 }); setLic(r.license); })}>Issue license (365 d)</button>
      <button className="btn ghost" disabled={busy} onClick={() => go(async () => { await api(`/orgs/${oid}/invoices`, "POST", {}); inv.reload(); }, "Draft invoice saved.")}>Draft this month's invoice</button></div>
      {lic && <div className="banner mt">License (client-bound, single use): <code dir="ltr" style={{ wordBreak: "break-all" }}>{lic}</code></div>}
      <div className="row mt"><input aria-label="License to activate" dir="ltr" placeholder="Paste a license to activate" value={lic} onChange={e => setLic(e.target.value)} /><button className="btn" disabled={busy || lic.length < 20} onClick={() => go(async () => { await api(`/orgs/${oid}/subscription`, "POST", { license: lic }); s.reload(); h.reload(); }, "License activated.")}>Activate</button></div>
      <div className="tag mt">No payment provider is connected: nothing is charged here. Invoices are drafts from list prices.</div></div> : <div className="card mt tag">Plan changes and invoices are handled by the service owner.</div>}
    <div className="grid mt"><div className="card"><h3>Invoices (drafts)</h3><DataTable rows={inv.data} loading={inv.loading} error={inv.err} pageSize={5} cols={[{ head: "Period", cell: x => x.period }, { head: "Total", cell: x => `${x.total} ${x.currency}` }, { head: "Status", cell: x => x.status }]} /></div>
      <div className="card"><h3>Subscription history</h3><DataTable rows={(h.data ?? []).map((x, i) => ({ id: String(i), ...x }))} loading={h.loading} error={h.err} pageSize={5} cols={[{ head: "When", cell: x => when(x.ts) }, { head: "By", cell: x => x.actor }, { head: "Change", cell: x => x.change }]} /></div></div></>}</>;
}

// ---------------------------------------------------------------- Customer audit log + client settings
export function OrgAudit() {
  const oid = useOrgId(); const a = useData<any[]>(oid ? `/orgs/${oid}/audit?limit=500` : null, 15000);
  return <><OrgBar />{!oid ? <NoOrg /> : <div className="card"><h3>Audit trail for this client</h3><div className="tag">Tamper-evident hash chain; shows only this client's records. Detail never includes secrets.</div><DataTable rows={a.data ? a.data.map(x => ({ id: String(x.seq), ...x })) : null} loading={a.loading} error={a.err}
    cols={[{ head: "When", cell: x => when(x.ts) }, { head: "Who", cell: x => x.actor, text: x => x.actor }, { head: "Action", cell: x => <b>{x.action}</b>, text: x => x.action }, { head: "Detail", cell: x => <code dir="ltr" className="tag">{x.detail}</code>, text: x => x.detail }]} /></div>}</>;
}

export function OrgSettings() {
  const oid = useOrgId(); const s = useData<any>(oid ? `/orgs/${oid}/settings` : null); const me = useMe(); const { err, busy, ok, go } = useAct(); const [name, setName] = useState(""); const [days, setDays] = useState(30);
  const owner = me?.role === "owner"; const senior = owner || me?.role === "administrator";
  return <><OrgBar />{!oid ? <NoOrg /> : <>
    <div className="card"><h3>Client settings</h3><Err m={err || s.err} />{ok && <div className="tag">{ok}</div>}
      <label className="row"><input type="checkbox" style={{ width: "auto" }} disabled={!senior} checked={!!s.data?.ai_enabled} onChange={e => go(async () => { await api(`/orgs/${oid}/settings`, "PUT", { ...s.data, ai_enabled: e.target.checked, ai_cloud: false }); s.reload(); })} />AI Technician enabled for this client</label>
      <div className="tag">Cloud AI is not available in this build; only the local model is used.</div></div>
    {owner && <div className="card mt"><h3>Offboard this client</h3><div className="tag">Revokes all access and Worker credentials, keeps data for the retention period, then it can be purged. Type the exact client name.</div>
      <div className="row mt"><input placeholder="Exact client name" value={name} onChange={e => setName(e.target.value)} /><input type="number" aria-label="Retention days" style={{ width: 100 }} value={days} onChange={e => setDays(+e.target.value)} />
        <button className="btn danger" disabled={busy || !name} onClick={() => go(async () => { const r = await api<any>(`/orgs/${oid}/offboard`, "POST", { confirm_name: name, retention_days: days }); return r; }, "Client offboarded. Workers that were offline must be revoked on the machine (cit-worker revoke-all).")}>Offboard</button></div></div>}</>}</>;
}
