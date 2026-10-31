import { ReactNode, useCallback, useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { api } from "./api";
import { has, reg, t, useLang } from "./i18n";

reg({
  "kit.search": { en: "Search…", ar: "بحث…" }, "kit.items": { en: "{n} items", ar: "{n} عنصر" }, "kit.selected": { en: "{n} selected", ar: "{n} محدد" },
  "kit.loading": { en: "Loading…", ar: "جارٍ التحميل…" }, "kit.empty": { en: "Nothing here yet.", ar: "لا يوجد شيء هنا بعد." },
  "kit.selectAll": { en: "Select all on this page", ar: "تحديد كل عناصر الصفحة" }, "kit.selectRow": { en: "Select row", ar: "تحديد الصف" },
  "kit.client": { en: "Client", ar: "العميل" }, "kit.noClient": { en: "No client yet. Create one in “Clients & Sites”.", ar: "لا يوجد عميل بعد. أنشئ واحداً من «العملاء والمواقع»." },
  "kit.chooseClient": { en: "Choose or create a client first.", ar: "اختر عميلاً أو أنشئ واحداً أولاً." },
  "kit.prev": { en: "Previous page", ar: "الصفحة السابقة" }, "kit.next": { en: "Next page", ar: "الصفحة التالية" },
  "kit.retry": { en: "Try again", ar: "حاول مرة أخرى" },
  "st.online": { en: "Online", ar: "متصل" }, "st.offline": { en: "Offline", ar: "غير متصل" }, "st.reconnecting": { en: "Reconnecting", ar: "جارٍ إعادة الاتصال" }, "st.degraded": { en: "Unstable", ar: "غير مستقر" },
  "st.needs_pairing": { en: "Needs pairing", ar: "يحتاج إلى إقران" }, "st.completed": { en: "Completed", ar: "مكتمل" }, "st.failed": { en: "Failed", ar: "فشل" }, "st.cancelled": { en: "Cancelled", ar: "ملغى" },
  "st.running": { en: "Running", ar: "قيد التنفيذ" }, "st.queued": { en: "Queued", ar: "في الانتظار" }, "st.pending_approval": { en: "Waiting for approval", ar: "بانتظار الموافقة" },
  "st.approved": { en: "Approved", ar: "تمت الموافقة" }, "st.open": { en: "Open", ar: "مفتوح" }, "st.resolved": { en: "Resolved", ar: "تم الحل" }, "st.acknowledged": { en: "Acknowledged", ar: "تم الإقرار" },
  "st.active": { en: "Active", ar: "نشط" }, "st.expired": { en: "Expired", ar: "منتهي" }, "st.trial": { en: "Trial", ar: "تجريبي" }, "st.ok": { en: "OK", ar: "سليم" },
  "st.unreachable": { en: "Unreachable", ar: "لا يمكن الوصول" }, "st.timed_out": { en: "Timed out", ar: "انتهت المهلة" }, "st.partial": { en: "Partly done", ar: "منجز جزئياً" },
  "st.pending": { en: "Pending", ar: "معلّق" }, "st.in_progress": { en: "In progress", ar: "قيد المعالجة" }, "st.closed": { en: "Closed", ar: "مغلق" }, "st.enrolled": { en: "Enrolled", ar: "مسجّل" },
  "st.lan": { en: "Local network", ar: "الشبكة المحلية" }, "st.overlay": { en: "Private network (VPN)", ar: "شبكة خاصة (VPN)" }, "st.loopback": { en: "This computer", ar: "هذا الحاسوب" }, "st.link-local": { en: "Direct cable", ar: "كابل مباشر" },
  "st.blocked": { en: "Blocked (public address)", ar: "محظور (عنوان عام)" }, "st.unresolvable": { en: "Cannot resolve name", ar: "تعذر تحليل الاسم" },
});

// ---- selected client (organization), shared by every page
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
export const isSenior = (me: Me | null) => me?.role === "owner" || me?.role === "administrator";

export function useOrgs() {
  const [orgs, setOrgs] = useState<Org[]>([]); const id = useOrgId();
  const load = useCallback(() => api<Org[]>("/orgs").then(l => { setOrgs(l); if (!l.find(o => o.id === orgId)) setOrg(l[0]?.id ?? ""); }).catch(() => {}), []);
  useEffect(() => { load(); }, [load]);
  return { orgs, id, reload: load };
}

/** Client picker shown on pages that work on one client. */
export function OrgBar() {
  useLang(); const { orgs, id } = useOrgs();
  if (!orgs.length) return <div className="banner">{t("kit.noClient")}</div>;
  return <div className="row" style={{ marginBottom: 12 }}><label style={{ margin: 0 }}>{t("kit.client")}</label>
    <select aria-label={t("kit.client")} style={{ width: 260 }} value={id} onChange={e => setOrg(e.target.value)}>{orgs.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}</select></div>;
}
export const NoOrg = () => <div className="card empty">{t("kit.chooseClient")}</div>;

/** Fetch with loading / error states and optional polling. Keeps the previous data while refreshing (no flicker). */
export function useData<T>(path: string | null, every = 0) {
  const [data, setData] = useState<T | null>(null); const [err, setErr] = useState(""); const [loading, setLoading] = useState(false);
  const load = useCallback(async () => { if (!path) { setData(null); return; } setLoading(true); try { setData(await api<T>(path)); setErr(""); } catch (e: any) { setErr(e.message); } finally { setLoading(false); } }, [path]);
  useEffect(() => { setData(null); load(); if (every) { const i = setInterval(load, every); return () => clearInterval(i); } }, [load, every]);
  return { data, err, loading, reload: load };
}

export function useAct() {
  const [err, setErr] = useState(""); const [busy, setBusy] = useState(false); const [ok, setOk] = useState("");
  const go = async <T,>(f: () => Promise<T>, done = ""): Promise<T | undefined> => { setBusy(true); setErr(""); setOk(""); try { const r = await f(); setOk(done); return r; } catch (e: any) { setErr(e.message); } finally { setBusy(false); } };
  return { err, busy, ok, go };
}

export const Err = ({ m }: { m: string }) => (m ? <div className="err" role="alert">{m}</div> : null);
export const stl = (v: string) => (has("st." + v) ? t("st." + v) : v);
const GOOD = ["online", "ok", "completed", "active", "resolved", "approved", "enrolled"], BAD = ["failed", "offline", "unreachable", "critical", "expired", "cancelled", "timed_out", "blocked"], WARN = ["degraded", "reconnecting", "pending_approval", "partial", "needs_pairing"];
export const Pill = ({ v }: { v: string }) => <span className={"pill " + (GOOD.includes(v) ? "ok" : BAD.includes(v) ? "bad" : WARN.includes(v) ? "warn" : "")}>{stl(v)}</span>;
export const when = (ts: number | null | undefined) => (ts ? new Date(ts * 1000).toLocaleString(undefined) : "—");
export const Help = ({ text }: { text: string }) => <span className="help" title={text} aria-label={text} role="img">?</span>;
export const Empty = ({ title, hint, children }: { title: string; hint?: string; children?: ReactNode }) => <div className="empty"><b>{title}</b>{hint && <span className="tag">{hint}</span>}{children}</div>;

export function Tabs({ tabs, value, onChange }: { tabs: [string, string][]; value: string; onChange: (k: string) => void }) {
  return <div className="tabs" role="tablist">{tabs.map(([k, label]) => <button key={k} role="tab" aria-selected={value === k} className={value === k ? "on" : ""} onClick={() => onChange(k)}>{label}</button>)}</div>;
}

type Col<T> = { head: string; cell: (r: T) => ReactNode; text?: (r: T) => string; num?: boolean };
/** Compact table: search, pagination, optional row selection, loading / error / empty states (with guidance). */
export function DataTable<T extends { id?: string }>({ rows, cols, loading, error, empty, pageSize = 12, onRow, selectable, selected, onSelect, filters }:
  { rows: T[] | null; cols: Col<T>[]; loading?: boolean; error?: string; empty?: string; pageSize?: number; onRow?: (r: T) => void; selectable?: boolean; selected?: string[]; onSelect?: (ids: string[]) => void; filters?: ReactNode }) {
  useLang(); const [q, setQ] = useState(""); const [page, setPage] = useState(0);
  const shown = useMemo(() => (rows ?? []).filter(r => !q || cols.map(c => (c.text ? c.text(r) : "")).join(" ").toLowerCase().includes(q.toLowerCase())), [rows, q, cols]);
  useEffect(() => setPage(0), [q, rows?.length]);
  const pages = Math.max(1, Math.ceil(shown.length / pageSize)); const view = shown.slice(page * pageSize, (page + 1) * pageSize);
  const sel = new Set(selected ?? []); const toggle = (id: string) => { const n = new Set(sel); n.has(id) ? n.delete(id) : n.add(id); onSelect?.([...n]); };
  const first = rows === null && !error;
  return <div><Err m={error ?? ""} />
    <div className="row" style={{ marginBottom: 8 }}><input aria-label={t("kit.search")} placeholder={t("kit.search")} style={{ maxWidth: 260 }} value={q} onChange={e => setQ(e.target.value)} />{filters}
      <span className="tag" style={{ marginInlineStart: "auto" }}>{t("kit.items", { n: shown.length })}{selectable && sel.size ? " · " + t("kit.selected", { n: sel.size }) : ""}</span></div>
    <div className="tbl-wrap"><table><thead><tr>{selectable && <th style={{ width: 28 }}><input type="checkbox" aria-label={t("kit.selectAll")} checked={view.length > 0 && view.every(r => sel.has(r.id!))} onChange={e => onSelect?.(e.target.checked ? [...new Set([...sel, ...view.map(r => r.id!)])] : [...sel].filter(i => !view.find(r => r.id === i)))} /></th>}{cols.map(c => <th key={c.head} className={c.num ? "num" : ""}>{c.head}</th>)}</tr></thead>
      <tbody>{view.map((r, i) => <tr key={r.id ?? i} style={onRow ? { cursor: "pointer" } : undefined} onClick={() => onRow?.(r)}>{selectable && <td onClick={e => e.stopPropagation()}><input type="checkbox" aria-label={t("kit.selectRow")} checked={sel.has(r.id!)} onChange={() => toggle(r.id!)} /></td>}{cols.map(c => <td key={c.head} className={c.num ? "num" : ""}>{c.cell(r)}</td>)}</tr>)}
        {!view.length && <tr><td colSpan={cols.length + (selectable ? 1 : 0)}><Empty title={loading || first ? t("kit.loading") : empty ?? t("kit.empty")} /></td></tr>}</tbody></table></div>
    {pages > 1 && <div className="row mt"><button className="btn ghost sm" aria-label={t("kit.prev")} disabled={page === 0} onClick={() => setPage(page - 1)}>‹</button><span className="tag">{page + 1} / {pages}</span><button className="btn ghost sm" aria-label={t("kit.next")} disabled={page >= pages - 1} onClick={() => setPage(page + 1)}>›</button></div>}</div>;
}

export function Spark({ values, label, max = 100 }: { values: (number | null)[]; label: string; max?: number }) {
  const pts = values.map((v, i) => (v === null ? null : `${(i / Math.max(1, values.length - 1)) * 300},${50 - (Math.min(v, max) / max) * 48}`)).filter(Boolean).join(" ");
  const last = [...values].reverse().find(v => v !== null);
  return <div><div className="tag">{label} <span className="ltr">{last !== undefined ? Math.round(last as number) + "%" : "—"}</span></div><svg className="spark" viewBox="0 0 300 52" role="img" aria-label={label}><polyline fill="none" stroke="var(--acc)" strokeWidth="1.5" points={pts} /></svg></div>;
}
