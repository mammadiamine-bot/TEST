import { useCallback, useEffect, useRef, useState } from "react";
import { api, downloadReport } from "./api";
import { t } from "./i18n";

export type Device = { kind?: string; live?: boolean; id: string; name: string; address: string; transport: string; status: string; owner_name: string; fingerprint?: string };
export type Finding = { id: string; module: string; severity: string; title: string; evidence: string; suggested: { tool_id: string }[] };
export type Step = { id: string; tool_id: string; title: string; risk: string; status: string; rationale: string; approved_by?: string; result?: { verified: boolean; detail: string } };
export type Plan = { id: string; status: string; steps: Step[]; before_findings: Finding[]; after?: { findings: Finding[] } };
export type Run = { id: string; created_at: number; findings: Finding[]; data: Record<string, any>; errors: Record<string, string>; source: string };
export type Ctx = { devices: Device[]; dev?: Device; refresh: () => void; select: (id: string) => void; goto: (p: string) => void; status: { mode: string; audit_chain_ok: boolean; offline_queue: number } | null };
type PP = { c: Ctx };

function useAsync() {
  const [err, setErr] = useState(""); const [busy, setBusy] = useState(false);
  const go = useCallback(async <T,>(f: () => Promise<T>): Promise<T | undefined> => {
    setBusy(true); setErr("");
    try { return await f(); } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  }, []);
  return { err, busy, go };
}
const Err = ({ m }: { m: string }) => (m ? <div className="err" role="alert">{m}</div> : null);
const Sev = ({ s }: { s: string }) => <span className={"sev " + s}>{s}</span>;
const NoDev = () => <div className="card">{t("none")}</div>;

function useLatestRun(did?: string) {
  const [run, setRun] = useState<Run | null>(null);
  const load = useCallback(async () => {
    if (!did) return setRun(null);
    try { const rs = await api<{ id: string }[]>(`/devices/${did}/runs`); setRun(rs[0] ? await api<Run>(`/runs/${rs[0].id}`) : null); } catch { setRun(null); }
  }, [did]);
  useEffect(() => { load(); }, [load]);
  return { run, reload: load };
}

function Spark({ v, max = 100, color = "#3b82f6" }: { v: number[]; max?: number; color?: string }) {
  const pts = v.map((y, i) => `${(i / Math.max(1, 59)) * 300},${58 - (Math.min(y, max) / max) * 56}`).join(" ");
  return <svg className="spark" viewBox="0 0 300 60" preserveAspectRatio="none" role="img" aria-label="trend"><polyline points={pts} fill="none" stroke={color} strokeWidth="2" /></svg>;
}

function FindingsTable({ f }: { f: Finding[] }) {
  if (!f.length) return <div className="card">—</div>;
  return <table><thead><tr><th>Severity</th><th>Finding</th><th>Evidence</th></tr></thead>
    <tbody>{f.map(x => <tr key={x.id}><td><Sev s={x.severity} /></td><td>{x.title}<div className="tag">{x.module}</div></td><td>{x.evidence}</td></tr>)}</tbody></table>;
}

// ------------------------------------------------------------------ Overview
export function Overview({ c }: PP) {
  const { run } = useLatestRun(c.dev?.id);
  const counts = ["critical", "high", "medium", "low"].map(s => [s, run?.findings.filter(f => f.severity === s).length ?? 0] as const);
  return <>
    <div className="grid">
      <div className="card"><h3>{t("inventory")}</h3><div className="big">{c.devices.length}</div><div className="tag">{c.devices.filter(d => d.status === "paired").length} paired</div></div>
      <div className="card"><h3>Audit</h3><div className="big">{c.status?.audit_chain_ok ? "✔" : "✖"}</div><div className="tag">hash-chain {c.status?.audit_chain_ok ? "intact" : "BROKEN"}</div></div>
      <div className="card"><h3>AI</h3><div className="tag">{c.status?.mode === "online" ? t("online") : t("offline")}</div><div className="tag">queued for review: {c.status?.offline_queue ?? 0}</div></div>
      {counts.map(([s, n]) => <div className="card" key={s}><h3><Sev s={s} /></h3><div className="big">{n}</div></div>)}
    </div>
    <div className="card mt"><h3>{c.dev ? c.dev.name : t("none")}</h3>
      {run ? <FindingsTable f={run.findings.filter(f => f.severity !== "info").slice(0, 6)} /> : <button className="btn" onClick={() => c.goto("diagnostics")}>{t("run")}</button>}</div>
  </>;
}

// ------------------------------------------------------------------ Discovery & pairing wizard
export function Discovery({ c }: PP) {
  const { err, busy, go } = useAsync();
  const [f, setF] = useState({ name: "", address: "", transport: "mock", owner_name: "", owner_contact: "", authorization_note: "", scenario: "broken" });
  const [created, setCreated] = useState<any>(null);
  const [code, setCode] = useState(""); const [fp, setFp] = useState(""); const [hk, setHk] = useState("");
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  return <>
    <div className="card"><h3>Connection guide</h3>
      <ol>
        <li><b>Direct Ethernet:</b> plug a cable between the two PCs, set static IPs on both (e.g. 169.254.10.1 / 169.254.10.2, mask 255.255.0.0). A cable alone does not give remote command access — the target must run OpenSSH Server or WinRM-HTTPS and be paired below.</li>
        <li><b>LAN / Wi-Fi:</b> use the device's private address (10.x, 172.16–31.x, 192.168.x).</li>
        <li><b>Remote:</b> both sides join Tailscale; use the 100.x address. Public IP addresses are rejected.</li>
        <li><b>Graphical:</b> RustDesk, started by the client, in the Support sessions tab.</li>
      </ol></div>
    <div className="card mt"><h3>1 · Register device &amp; owner authorization</h3><Err m={err} />
      <div className="grid">
        <div><label>Device name</label><input value={f.name} onChange={set("name")} /></div>
        <div><label>Transport</label><select value={f.transport} onChange={set("transport")}><option value="mock">Mock (test)</option><option value="ssh">OpenSSH</option><option value="winrm">WinRM (HTTPS)</option></select></div>
        <div><label>Address (private/Tailscale)</label><input value={f.address} onChange={set("address")} placeholder="192.168.1.20" /></div>
        {f.transport === "mock" && <div><label>Scenario</label><select value={f.scenario} onChange={set("scenario")}><option value="broken">Broken PC</option><option value="healthy">Healthy PC</option></select></div>}
        <div><label>Owner name</label><input value={f.owner_name} onChange={set("owner_name")} /></div>
        <div><label>Owner contact</label><input value={f.owner_contact} onChange={set("owner_contact")} /></div>
      </div>
      <label>Authorization record (how the owner authorized this support)</label><textarea rows={2} value={f.authorization_note} onChange={set("authorization_note")} />
      <button className="btn mt" disabled={busy} onClick={() => go(async () => { setCreated(await api("/devices", "POST", { ...f, address: f.transport === "mock" ? "mock" : f.address })); c.refresh(); })}>Register</button></div>
    {created && <div className="card mt"><h3>2 · Pair &amp; verify identity</h3>
      <p>Pairing code (read it to the owner / confirm on their machine): <b style={{ fontSize: 22 }}>{created.pairing_code}</b></p>
      <div className="grid"><div><label>Code</label><input value={code} onChange={e => setCode(e.target.value)} maxLength={6} /></div>
        {created.transport === "ssh" && <><div><label>Host key fingerprint confirmed by owner</label><input value={fp} onChange={e => setFp(e.target.value)} placeholder="SHA256:…" /></div>
          <div><label>known_hosts line</label><input value={hk} onChange={e => setHk(e.target.value)} /></div></>}</div>
      <button className="btn mt" disabled={busy} onClick={() => go(async () => { const d = await api<Device>(`/devices/${created.id}/pair`, "POST", { code, fingerprint: fp, host_key: hk || null }); setCreated(null); c.refresh(); c.select(d.id); c.goto("remote"); })}>Pair</button></div>}
  </>;
}

export function Inventory({ c }: PP) {
  const { go, err } = useAsync();
  return <div className="card"><Err m={err} /><table><thead><tr><th>Name</th><th>Address</th><th>Transport</th><th>Owner</th><th>Status</th><th></th></tr></thead>
    <tbody>{c.devices.map(d => <tr key={d.id}><td>{d.name}</td><td>{d.address}</td><td>{d.transport}</td><td>{d.owner_name}</td><td>{d.status}</td>
      <td className="row"><button className="btn ghost" onClick={() => c.select(d.id)}>Select</button>
        {d.status !== "revoked" && <button className="btn danger" onClick={() => go(async () => { await api(`/devices/${d.id}/revoke`, "POST"); c.refresh(); })}>{t("revoke")}</button>}</td></tr>)}</tbody></table></div>;
}

// ------------------------------------------------------------------ Live metrics
export function Metrics({ c }: PP) {
  const [cpu, setCpu] = useState<number[]>([]); const [ram, setRam] = useState<number[]>([]); const [err, setErr] = useState("");
  const [net, setNet] = useState<any>(null); const [disk, setDisk] = useState<any>(null);
  const did = c.dev?.id; const alive = useRef(true);
  useEffect(() => {
    alive.current = true; setCpu([]); setRam([]);
    if (!did) return;
    const tick = async () => {
      try { const m = await api(`/devices/${did}/metrics`); if (!alive.current) return; setErr(""); setCpu(a => [...a.slice(-59), m.cpu_percent]); setRam(a => [...a.slice(-59), m.ram_percent]); }
      catch (e: any) { setErr(e.message); }
    };
    tick(); const i = setInterval(tick, 3000); return () => { alive.current = false; clearInterval(i); };
  }, [did]);
  const { run } = useLatestRun(did);
  useEffect(() => { setNet(run?.data.network); setDisk(run?.data.storage); }, [run]);
  if (!c.dev) return <NoDev />;
  return <><Err m={err} /><div className="grid">
    <div className="card"><h3>{t("cpu")}</h3><div className="big">{cpu.at(-1) ?? "—"}%</div><Spark v={cpu} /></div>
    <div className="card"><h3>{t("ram")}</h3><div className="big">{ram.at(-1) ?? "—"}%</div><Spark v={ram} color="#22c55e" /></div>
    <div className="card"><h3>Disk (last diagnostic)</h3>{disk?.volumes?.map((v: any) => <div key={v.drive}>{v.drive}: {v.free_gb} / {v.size_gb} GB free</div>) ?? "run a diagnostic"}</div>
    <div className="card"><h3>Network (last diagnostic)</h3>{net ? <><div>Gateway: {String(net.gateway_reachable)}</div><div>Internet: {String(net.internet_reachable)}</div><div>DNS: {String(net.dns_resolves)}</div></> : "run a diagnostic"}</div>
  </div></>;
}

// ------------------------------------------------------------------ Diagnostics
export function Diagnostics({ c }: PP) {
  const { err, busy, go } = useAsync(); const { run, reload } = useLatestRun(c.dev?.id);
  if (!c.dev) return <NoDev />;
  return <><Err m={err} />
    <div className="row"><button className="btn" disabled={busy} onClick={() => go(async () => { await api(`/devices/${c.dev!.id}/diagnose`, "POST", {}); await reload(); })}>{busy ? t("running") : t("run")}</button>
      {run && <span className="tag">{t("scripted")} · {new Date(run.created_at * 1000).toLocaleString()}</span>}</div>
    {run && <div className="card mt"><FindingsTable f={run.findings} />
      {Object.entries(run.errors).map(([m, e]) => <div className="err" key={m}>{m}: {e}</div>)}</div>}
    {run && <button className="btn mt" onClick={() => c.goto("repairs")}>{t("createPlan")} →</button>}</>;
}

// ------------------------------------------------------------------ AI chat
export function Chat({ c }: PP) {
  const [log, setLog] = useState<{ me: boolean; text: string; tag?: string }[]>([]); const [m, setM] = useState(""); const { err, busy, go } = useAsync();
  if (!c.dev) return <NoDev />;
  return <><Err m={err} /><div className="card" style={{ minHeight: 280 }}>
    {log.map((x, i) => <div key={i} className={"msg " + (x.me ? "me" : "bot")}>{x.text}{x.tag && <div className="tag">{x.tag}</div>}</div>)}</div>
    <div className="row mt"><input style={{ flex: 1 }} value={m} onChange={e => setM(e.target.value)} onKeyDown={e => e.key === "Enter" && !busy && m && document.getElementById("send")?.click()} />
      <button id="send" className="btn" disabled={busy || !m} onClick={() => go(async () => {
        const q = m; setM(""); setLog(l => [...l, { me: true, text: q }]);
        const r = await api(`/chat`, "POST", { device_id: c.dev!.id, message: q });
        setLog(l => [...l, { me: false, text: r.text, tag: (r.source === "claude" ? "Claude AI" : t("scripted")) + (r.notice ? " · " + r.notice : "") + (r.fallback_reason ? " · " + r.fallback_reason : "") }]);
      })}>{t("send")}</button></div></>;
}

// ------------------------------------------------------------------ Repair plans
export function Repairs({ c }: PP) {
  const { err, busy, go } = useAsync(); const { run } = useLatestRun(c.dev?.id);
  const [plan, setPlan] = useState<Plan | null>(null); const [confirm, setConfirm] = useState<Record<string, boolean>>({});
  const did = c.dev?.id;
  useEffect(() => { setPlan(null); if (did) api<Plan[]>(`/devices/${did}/plans`).then(p => setPlan(p[0] ?? null)).catch(() => {}); }, [did]);
  if (!c.dev) return <NoDev />;
  const approve = (s: Step) => go(async () => setPlan(await api(`/plans/${plan!.id}/steps/${s.id}/approve`, "POST", { approver: "Technician", confirm_high_risk: !!confirm[s.id] })));
  return <><Err m={err} />
    <div className="row"><button className="btn" disabled={!run || busy} onClick={() => go(async () => setPlan(await api(`/devices/${did}/plans`, "POST", { run_id: run!.id })))}>{t("createPlan")}</button>
      {plan && <>
        <button className="btn ghost" disabled={busy} onClick={() => go(async () => setPlan(await api(`/plans/${plan.id}/approve-low-risk`, "POST", { approver: "Technician" })))}>{t("approveLow")}</button>
        <button className="btn" disabled={busy || !plan.steps.some(s => s.status === "approved")} onClick={() => go(async () => setPlan(await api(`/plans/${plan.id}/execute`, "POST")))}>{t("execute")}</button>
        <button className="btn ghost" onClick={() => downloadReport(did!, plan.id).catch(() => {})}>{t("download")}</button></>}</div>
    {plan && <div className="card mt"><div className="tag">Plan {plan.id} · {plan.status}</div>
      <table><thead><tr><th>Action</th><th>Risk</th><th>Why</th><th>Status</th><th></th></tr></thead><tbody>
        {plan.steps.map(s => <tr key={s.id}><td>{s.title}</td><td className={"risk-" + s.risk}>{s.risk}</td><td>{s.rationale}</td>
          <td>{s.status}{s.result && <div className="tag">{s.result.verified ? "✔ verified: " : "✖ NOT verified: "}{s.result.detail}</div>}</td>
          <td>{s.status === "proposed" && <div className="row">
            {s.risk === "high" && <label style={{ margin: 0 }}><input type="checkbox" style={{ width: "auto" }} checked={!!confirm[s.id]} onChange={e => setConfirm({ ...confirm, [s.id]: e.target.checked })} /> {t("highConfirm")}</label>}
            <button className="btn" disabled={s.risk === "high" && !confirm[s.id]} onClick={() => approve(s)}>{t("approve")}</button>
            <button className="btn ghost" onClick={() => go(async () => setPlan(await api(`/plans/${plan.id}/steps/${s.id}/reject`, "POST")))}>{t("reject")}</button></div>}</td></tr>)}
      </tbody></table>
      {plan.after && <><h3 className="mt">After re-scan</h3><FindingsTable f={plan.after.findings} /></>}</div>}</>;
}

// ------------------------------------------------------------------ Support sessions / remote
export function Remote({ c }: PP) {
  const { err, busy, go } = useAsync(); const [ses, setSes] = useState<any[]>([]); const [fresh, setFresh] = useState<any>(null);
  const [scope, setScope] = useState("diagnose"); const [min, setMin] = useState(30); const [code, setCode] = useState(""); const [person, setPerson] = useState("");
  const did = c.dev?.id;
  const load = useCallback(() => { if (did) api(`/devices/${did}/sessions`).then(setSes).catch(() => {}); }, [did]);
  useEffect(() => { load(); const i = setInterval(load, 5000); return () => clearInterval(i); }, [load]);
  if (!c.dev) return <NoDev />;
  const active = ses.find(s => s.status === "active");
  return <><Err m={err} />
    <div className="card" style={{ borderColor: active ? "#22c55e" : undefined }}><h3>Session indicator</h3>
      {active ? <div className="row"><span className="pill ok">● ACTIVE — {active.scope} — expires {new Date(active.expires_at * 1000).toLocaleTimeString()} · consent by {active.consenting_person}</span>
        <button className="btn danger" onClick={() => go(async () => { await api(`/sessions/${active.id}/revoke`, "POST"); load(); })}>{t("revoke")}</button></div> : <span className="tag">No active session. The device cannot be accessed.</span>}</div>
    <div className="card mt"><h3>Request session</h3><div className="row">
      <select value={scope} onChange={e => setScope(e.target.value)}><option value="diagnose">Diagnose (read-only)</option><option value="repair">Repair (with approvals)</option></select>
      <input type="number" min={1} max={240} value={min} onChange={e => setMin(+e.target.value)} style={{ width: 90 }} /> min
      <button className="btn" disabled={busy || c.dev.status !== "paired"} onClick={() => go(async () => { setFresh(await api(`/devices/${did}/sessions`, "POST", { scope, minutes: min })); load(); })}>Request</button></div>
      {fresh && fresh.status === "requested" && <div className="mt">Consent code to confirm with the client: <b style={{ fontSize: 22 }}>{fresh.consent_code}</b>
        <div className="row mt"><input style={{ width: 140 }} placeholder="code" value={code} onChange={e => setCode(e.target.value)} maxLength={6} /><input style={{ width: 200 }} placeholder="client name" value={person} onChange={e => setPerson(e.target.value)} />
          <button className="btn" onClick={() => go(async () => { await api(`/sessions/${fresh.id}/consent`, "POST", { code, person }); setFresh(null); load(); })}>Client consents</button></div></div>}</div>
    <div className="card mt"><h3>History</h3><table><tbody>{ses.map(s => <tr key={s.id}><td>{s.id}</td><td>{s.scope}</td><td>{s.status}</td><td>{s.consenting_person ?? "—"}</td></tr>)}</tbody></table></div>
    <div className="card mt"><h3>RustDesk / Tailscale</h3><p className="tag">Graphical access uses RustDesk started and approved by the client; connect over the Tailscale 100.x address. Launch integration is not implemented yet — see PROJECT_STATUS.md.</p></div></>;
}

// ------------------------------------------------------------------ Console
export function Console({ c }: PP) {
  const [cmd, setCmd] = useState("Get-Service -Name Spooler"); const [out, setOut] = useState(""); const { err, busy, go } = useAsync();
  if (!c.dev) return <NoDev />;
  return <><Err m={err} /><div className="card"><div className="tag">Only single read-only Get-* cmdlets (Get-Process, Get-Service, Get-NetAdapter, Get-Disk, Get-Volume, …). No pipes, variables, quotes or scripts.</div>
    <div className="row mt"><input style={{ flex: 1 }} dir="ltr" value={cmd} onChange={e => setCmd(e.target.value)} />
      <button className="btn" disabled={busy} onClick={() => go(async () => setOut((await api(`/devices/${c.dev!.id}/console`, "POST", { command: cmd })).output))}>Run</button></div><pre dir="ltr">{out}</pre></div></>;
}

// ------------------------------------------------------------------ Data views over the latest run
export function Software({ c }: PP) {
  const { run } = useLatestRun(c.dev?.id); const apps = run?.data.software?.apps ?? [];
  if (!c.dev) return <NoDev />;
  return <div className="card"><table><thead><tr><th>Name</th><th>Version</th><th>Publisher</th></tr></thead><tbody>{apps.map((a: any) => <tr key={a.name}><td>{a.name}</td><td>{a.version}</td><td>{a.publisher}</td></tr>)}</tbody></table>
    {!run && <div className="tag">Run a diagnostic first. Uninstalling is a high-risk repair step and goes through Repair plans.</div>}</div>;
}
export function Hardware({ c }: PP) {
  const { run } = useLatestRun(c.dev?.id); const d = run?.data.disk_health; const s = run?.data.storage;
  if (!c.dev) return <NoDev />;
  return <div className="grid"><div className="card"><h3>Disks / SMART</h3>{d?.disks.map((x: any) => <div key={x.model}>{x.model} — {x.media} — <b>{x.health}</b> ({x.operational})</div>) ?? "—"}<div className="tag">SMART predicts failure: {String(d?.smart_predict_failure)}</div></div>
    <div className="card"><h3>Storage</h3>{s?.volumes.map((v: any) => <div key={v.drive}>{v.drive}: {v.free_gb}/{v.size_gb} GB free</div>)}<div className="tag">Temp: {s?.temp_mb} MB</div></div>
    <div className="card"><h3>Drivers</h3>{run?.data.drivers?.problem_devices.length ? run.data.drivers.problem_devices.map((p: any) => <div key={p.name}>{p.name} (code {p.error_code})</div>) : "No problem devices"}</div></div>;
}
export function Logs({ c }: PP) {
  const { run } = useLatestRun(c.dev?.id); const e = run?.data.event_log;
  if (!c.dev) return <NoDev />;
  return <div className="card">{e ? <><div>Critical: <b>{e.critical}</b> · Errors: <b>{e.errors}</b> (24 h)</div>
    <table><thead><tr><th>Source</th><th>Count</th><th>Sample</th></tr></thead><tbody>{e.top_sources.map((s: any) => <tr key={s.source}><td>{s.source}</td><td>{s.count}</td><td>{s.sample}</td></tr>)}</tbody></table></> : "Run a diagnostic first."}</div>;
}

export function Reports({ c }: PP) {
  const { err, go } = useAsync();
  const [notes, setNotes] = useState<any[]>([]); const [body, setBody] = useState("");
  const did = c.dev?.id; const load = useCallback(() => { if (did) api(`/devices/${did}/notes`).then(setNotes).catch(() => {}); }, [did]); useEffect(load, [load]);
  if (!c.dev) return <NoDev />;
  return <><Err m={err} /><div className="card"><button className="btn" onClick={() => go(() => downloadReport(did!))}>{t("download")}</button>
    <div className="tag mt">PDF is English-only for now (Arabic shaping needs a bundled font) and lists findings as rule-based, not AI.</div></div>
    <div className="card mt"><h3>Technician notes</h3>{notes.map(n => <div key={n.id}><b>{n.author}:</b> {n.body}</div>)}
      <div className="row mt"><input style={{ flex: 1 }} value={body} onChange={e => setBody(e.target.value)} /><button className="btn" disabled={!body} onClick={() => go(async () => { await api(`/devices/${did}/notes`, "POST", { author: "Technician", body }); setBody(""); load(); })}>Add</button></div></div></>;
}

export function Audit({ c }: PP) {
  const [rows, setRows] = useState<any[]>([]);
  useEffect(() => { api(`/audit${c.dev ? `?device_id=${c.dev.id}` : ""}`).then(setRows).catch(() => {}); }, [c.dev]);
  return <div className="card"><table><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Detail</th></tr></thead>
    <tbody>{rows.map(r => <tr key={r.seq}><td>{new Date(r.ts * 1000).toLocaleString()}</td><td>{r.actor}</td><td>{r.action}</td><td className="tag">{JSON.stringify(r.detail)}</td></tr>)}</tbody></table></div>;
}

export function Settings({ c }: PP) {
  const [tools, setTools] = useState<any[]>([]); useEffect(() => { api("/tools").then(setTools).catch(() => {}); }, []);
  return <><div className="card"><h3>Status</h3><div>Mode: {c.status?.mode}</div><div>Audit chain: {c.status?.audit_chain_ok ? "intact" : "BROKEN"}</div>
    <div className="tag">Cloud AI is configured in the backend (CIT_CLAUDE_ENABLED=1). Credentials are stored with Windows DPAPI; on other platforms storing them is refused.</div></div>
    <div className="card mt"><h3>Tool allowlist</h3><table><thead><tr><th>Tool</th><th>Kind</th><th>Risk</th><th>Timeout</th></tr></thead>
      <tbody>{tools.map(x => <tr key={x.id}><td>{x.title}<div className="tag">{x.id}</div></td><td>{x.kind}</td><td className={"risk-" + x.risk}>{x.risk}</td><td>{x.timeout_s}s</td></tr>)}</tbody></table></div></>;
}
