import { ReactNode, useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { api } from "./api";

// ---- selected client (organization), shared by every RMM page
let orgId = ((): string => { try { return localStorage.getItem("org") || ""; } catch { return ""; } })();
const subs = new Set<() => void>();
export const setOrg = (id: string) => { orgId = id; try { localStorage.setItem("org", id); } catch {} subs.forEach(f => f()); };
export const useOrgId = () => useSyncExternalStore(f => { subs.add(f); return () => subs.delete(f); }, () => orgId);

export type Org = { id: string; name: string };
export type Me = { id: string; role: string; org_ids: string[] | null };

export function useMe() {
  const [me, setMe] = useState<Me | null>(null);
  useEffect(() => { api<Me>("/me").then(setMe).catch(() => setMe(null)); }, []);
  return me;
}

export function useOrgs() {
  const [orgs, setOrgs] = useState<Org[]>([]); const id = useOrgId();
  const load = useCallback(() => api<Org[]>("/orgs").then(l => { setOrgs(l); if (!l.find(o => o.id === orgId)) setOrg(l[0]?.id ?? ""); }).catch(() => {}), []);
  useEffect(() => { load(); }, [load]);
  return { orgs, id, reload: load };
}

/** Client picker shown at the top of every RMM page. */
export function OrgBar() {
  const { orgs, id } = useOrgs();
  if (!orgs.length) return <div className="banner">No client yet. Create one in “Clients &amp; sites”.</div>;
  return <div className="row" style={{ marginBottom: 12 }}><label style={{ margin: 0 }}>Client</label>
    <select aria-label="Client" style={{ width: 260 }} value={id} onChange={e => setOrg(e.target.value)}>{orgs.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}</select></div>;
}

/** Fetch with loading / error / empty states and optional polling. */
export function useData<T>(path: string | null, every = 0) {
  const [data, setData] = useState<T | null>(null); const [err, setErr] = useState(""); const [loading, setLoading] = useState(false);
  const load = useCallback(async () => { if (!path) { setData(null); return; } setLoading(true); try { setData(await api<T>(path)); setErr(""); } catch (e: any) { setErr(e.message); } finally { setLoading(false); } }, [path]);
  useEffect(() => { setData(null); load(); if (every) { const i = setInterval(load, every); return () => clearInterval(i); } }, [load, every]);
  return { data, err, loading, reload: load };
}

export const Err = ({ m }: { m: string }) => (m ? <div className="err" role="alert">{m}</div> : null);
export const Pill = ({ v }: { v: string }) => <span className={"pill " + (["online", "ok", "completed", "active", "resolved", "approved", "met"].includes(v) ? "ok" : ["failed", "offline", "unreachable", "critical", "expired", "cancelled", "timed_out"].includes(v) ? "bad" : "")}>{v}</span>;
export const when = (ts: number | null | undefined) => (ts ? new Date(ts * 1000).toLocaleString() : "—");

type Col<T> = { head: string; cell: (r: T) => ReactNode; text?: (r: T) => string };
/** Compact table with search, pagination and optional row selection (bulk actions). */
export function DataTable<T extends { id?: string }>({ rows, cols, loading, error, empty = "Nothing here yet.", pageSize = 12, onRow, selectable, selected, onSelect, filters }:
  { rows: T[] | null; cols: Col<T>[]; loading?: boolean; error?: string; empty?: string; pageSize?: number; onRow?: (r: T) => void; selectable?: boolean; selected?: string[]; onSelect?: (ids: string[]) => void; filters?: ReactNode }) {
  const [q, setQ] = useState(""); const [page, setPage] = useState(0);
  const shown = useMemo(() => (rows ?? []).filter(r => !q || cols.map(c => (c.text ? c.text(r) : "")).join(" ").toLowerCase().includes(q.toLowerCase())), [rows, q, cols]);
  useEffect(() => setPage(0), [q, rows?.length]);
  const pages = Math.max(1, Math.ceil(shown.length / pageSize)); const view = shown.slice(page * pageSize, (page + 1) * pageSize);
  const sel = new Set(selected ?? []); const toggle = (id: string) => { const n = new Set(sel); n.has(id) ? n.delete(id) : n.add(id); onSelect?.([...n]); };
  return <div><Err m={error ?? ""} /><div className="row" style={{ marginBottom: 8 }}><input aria-label="Search" placeholder="Search…" style={{ maxWidth: 260 }} value={q} onChange={e => setQ(e.target.value)} />{filters}
    <span className="tag" style={{ marginInlineStart: "auto" }}>{shown.length} item{shown.length === 1 ? "" : "s"}{selectable && sel.size ? ` · ${sel.size} selected` : ""}</span></div>
    <table><thead><tr>{selectable && <th style={{ width: 28 }}><input type="checkbox" aria-label="Select all on page" checked={view.length > 0 && view.every(r => sel.has(r.id!))} onChange={e => onSelect?.(e.target.checked ? [...new Set([...sel, ...view.map(r => r.id!)])] : [...sel].filter(i => !view.find(r => r.id === i)))} /></th>}{cols.map(c => <th key={c.head}>{c.head}</th>)}</tr></thead>
      <tbody>{view.map((r, i) => <tr key={r.id ?? i} style={onRow ? { cursor: "pointer" } : undefined} onClick={() => onRow?.(r)}>{selectable && <td onClick={e => e.stopPropagation()}><input type="checkbox" aria-label="Select row" checked={sel.has(r.id!)} onChange={() => toggle(r.id!)} /></td>}{cols.map(c => <td key={c.head}>{c.cell(r)}</td>)}</tr>)}
        {!view.length && <tr><td colSpan={cols.length + (selectable ? 1 : 0)} className="tag">{loading ? "Loading…" : rows === null && !error ? "Loading…" : empty}</td></tr>}</tbody></table>
    {pages > 1 && <div className="row mt"><button className="btn ghost" disabled={page === 0} onClick={() => setPage(page - 1)}>‹</button><span className="tag">{page + 1} / {pages}</span><button className="btn ghost" disabled={page >= pages - 1} onClick={() => setPage(page + 1)}>›</button></div>}</div>;
}

export function Spark({ values, label, max = 100 }: { values: (number | null)[]; label: string; max?: number }) {
  const pts = values.map((v, i) => (v === null ? null : `${(i / Math.max(1, values.length - 1)) * 300},${50 - (Math.min(v, max) / max) * 48}`)).filter(Boolean).join(" ");
  const last = [...values].reverse().find(v => v !== null);
  return <div><div className="tag">{label} {last !== undefined ? Math.round(last as number) + "%" : "—"}</div><svg className="spark" viewBox="0 0 300 52" role="img" aria-label={label}><polyline fill="none" stroke="var(--acc)" strokeWidth="1.5" points={pts} /></svg></div>;
}
