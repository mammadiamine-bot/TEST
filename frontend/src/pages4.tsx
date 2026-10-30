import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import type { Ctx } from "./pages";

type Org = { id: string; name: string };
type Site = { id: string | null; name: string; devices: Item[]; workers: Item[] };
type Item = { id: string; name: string; transport?: string; address: string; status: string; state?: string; online?: boolean; path: string };
type Principal = { id: string; name: string; role: string; org_ids: string[]; revoked: number };

const Err = ({ m }: { m: string }) => (m ? <div className="err" role="alert">{m}</div> : null);
const PATH: Record<string, string> = { lan: "LAN", overlay: "private overlay (Tailscale/WireGuard)", loopback: "this computer", "link-local": "Ethernet link", blocked: "BLOCKED (public)", unresolvable: "cannot resolve" };

export function Clients({ c }: { c: Ctx }) {
  const [orgs, setOrgs] = useState<Org[]>([]); const [oid, setOid] = useState("");
  const [fleet, setFleet] = useState<{ sites: Site[] } | null>(null); const [err, setErr] = useState("");
  const [name, setName] = useState(""); const [site, setSite] = useState("");
  const [steps, setSteps] = useState<string[]>([]); const [pr, setPr] = useState<Principal[]>([]);
  const [np, setNp] = useState({ name: "", role: "technician" }); const [minted, setMinted] = useState("");
  const [loose, setLoose] = useState<{ devices: any[]; workers: any[] }>({ devices: [], workers: [] });
  const run = async (f: () => Promise<unknown>) => { try { setErr(""); await f(); } catch (e: any) { setErr(e.message); } };

  const loadOrgs = useCallback(() => run(async () => { const l = await api<Org[]>("/orgs"); setOrgs(l); setOid(x => x || l[0]?.id || ""); }), []);
  const loadFleet = useCallback(() => run(async () => {
    if (!oid) return setFleet(null);
    setFleet(await api(`/orgs/${oid}/fleet`));
    api<Principal[]>("/principals").then(setPr).catch(() => setPr([]));   // owner only: scoped technicians get 403 and simply see nothing here
    Promise.all([api<any[]>("/devices"), api<any[]>("/workers")]).then(([d, w]) => setLoose({ devices: d.filter(x => !x.org_id && x.transport !== "local"), workers: w.filter(x => !x.org_id) })).catch(() => {});
  }), [oid]);
  useEffect(() => { loadOrgs(); }, [loadOrgs]);
  useEffect(() => { loadFleet(); const i = setInterval(loadFleet, 10000); return () => clearInterval(i); }, [loadFleet]);

  const siteOpts = (fleet?.sites ?? []).filter(s => s.id);
  const assign = (kind: "devices" | "workers", id: string, site_id: string) => run(async () => { await api(`/${kind}/${id}/assign`, "PUT", { org_id: oid, site_id: site_id || null }); loadFleet(); c.refresh(); });
  return <>
    <Err m={err} />
    <div className="card"><div className="tag">A client is a company or person you support. Everything below belongs to one client; technicians you create here can only ever see their own clients.</div>
      <div className="row mt">{orgs.map(o => <button key={o.id} className={"btn " + (oid === o.id ? "" : "ghost")} onClick={() => setOid(o.id)}>{o.name}</button>)}{!orgs.length && <span className="tag">No clients yet.</span>}</div>
      <div className="row mt"><input placeholder="New client name" value={name} onChange={e => setName(e.target.value)} />
        <button className="btn" disabled={!name} onClick={() => run(async () => { const o = await api<Org>("/orgs", "POST", { name }); setName(""); setOid(o.id); loadOrgs(); })}>Add client</button></div></div>
    {oid && <>
      <div className="card mt"><h3>Sites</h3>
        <div className="row"><input placeholder="New site (office, branch, server room…)" value={site} onChange={e => setSite(e.target.value)} />
          <button className="btn" disabled={!site} onClick={() => run(async () => { await api(`/orgs/${oid}/sites`, "POST", { name: site }); setSite(""); loadFleet(); })}>Add site</button></div></div>
      {fleet?.sites.map(s => <div className="card mt" key={s.id ?? "none"}><h3>{s.name}</h3>
        <table><thead><tr><th>Computer</th><th>Kind</th><th>State</th><th>Reached via</th></tr></thead><tbody>
          {s.workers.map(w => <tr key={w.id}><td>{w.name}<div className="tag" dir="ltr">{w.address}</div></td><td>Worker</td><td>{w.state ?? w.status}</td><td>{PATH[w.path] ?? w.path}</td></tr>)}
          {s.devices.map(d => <tr key={d.id} style={{ cursor: "pointer" }} onClick={() => { c.select(d.id); c.goto("devices"); }}><td>{d.name}<div className="tag" dir="ltr">{d.address}</div></td><td>{d.transport}</td><td>{d.status}</td><td>{PATH[d.path] ?? d.path}</td></tr>)}
          {!s.workers.length && !s.devices.length && <tr><td colSpan={4} className="tag">Nothing here yet.</td></tr>}</tbody></table></div>)}
      {(loose.devices.length > 0 || loose.workers.length > 0) && <div className="card mt"><h3>Not yet assigned to any client</h3>
        <div className="tag">Unassigned computers are visible to you (the owner) only. Put each one under this client{siteOpts.length ? " and a site" : ""}.</div>
        {[...loose.workers.map(x => ({ ...x, k: "workers" as const })), ...loose.devices.map(x => ({ ...x, k: "devices" as const }))].map(x => <AssignRow key={x.id} x={x} sites={siteOpts} onAssign={(sid) => assign(x.k, x.id, sid)} />)}</div>}
      <div className="card mt"><h3>Connect the next computer</h3>
        <div className="row"><button className="btn ghost" onClick={() => run(async () => setSteps((await api<any>(`/orgs/${oid}/onboarding?kind=worker`)).steps))}>Worker PC steps</button>
          <button className="btn ghost" onClick={() => run(async () => setSteps((await api<any>(`/orgs/${oid}/onboarding?kind=agent`)).steps))}>Temporary agent steps</button>
          <button className="btn" onClick={() => c.goto("workers")}>Open “Add worker”</button></div>
        {steps.length > 0 && <ol className="mt">{steps.map((x, i) => <li key={i}>{x}</li>)}</ol>}</div>
      <div className="card mt"><h3>Technician access for this client</h3>
        <div className="tag">Each person gets their own token, limited to chosen clients; it is shown once and only a hash is stored. “Viewer” can look but not change anything.</div>
        <table><tbody>{pr.filter(p => p.org_ids.includes(oid)).map(p => <tr key={p.id}><td>{p.name}</td><td>{p.role}</td><td>{p.revoked ? "revoked" : "active"}</td>
          <td>{!p.revoked && <button className="btn danger" onClick={() => run(async () => { await api(`/principals/${p.id}`, "DELETE"); loadFleet(); })}>Revoke now</button>}</td></tr>)}</tbody></table>
        <div className="row mt"><input placeholder="Person / purpose" value={np.name} onChange={e => setNp({ ...np, name: e.target.value })} />
          <select value={np.role} onChange={e => setNp({ ...np, role: e.target.value })}><option value="technician">technician</option><option value="viewer">viewer (read-only)</option></select>
          <button className="btn" disabled={!np.name} onClick={() => run(async () => { const r = await api<any>("/principals", "POST", { name: np.name, role: np.role, org_ids: [oid] }); setMinted(r.token); setNp({ ...np, name: "" }); loadFleet(); })}>Create access</button></div>
        {minted && <div className="banner mt">Copy this token now — it will not be shown again: <code dir="ltr">{minted}</code> <button className="btn ghost" onClick={() => setMinted("")}>I saved it</button></div>}</div></>}
  </>;
}

function AssignRow({ x, sites, onAssign }: { x: { name: string; address: string; k: string }; sites: Site[]; onAssign: (site: string) => void }) {
  const [s, setS] = useState("");
  return <div className="row mt"><span>{x.k === "workers" ? "Worker" : "Device"}: <b>{x.name}</b> <span className="tag" dir="ltr">{x.address}</span></span>
    <select value={s} onChange={e => setS(e.target.value)}><option value="">(no site)</option>{sites.map(o => <option key={o.id!} value={o.id!}>{o.name}</option>)}</select>
    <button className="btn" onClick={() => onAssign(s)}>Assign to this client</button></div>;
}
