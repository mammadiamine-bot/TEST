import { useCallback, useEffect, useState } from "react";
import { api, hasToken, signIn, signOut } from "./api";
import { getLang, reg, setLang, t, useLang } from "./i18n";
import { Icon } from "./icons";
import { Tabs, useMe, isSenior } from "./kit";
import * as P from "./pages";
import * as Q from "./pages2";
import { Workers } from "./pages3";
import { extraSections } from "./registry";
import { Dashboard, Devices } from "./views1";
import { JobsHub, Monitoring, ReportsTickets } from "./views2";
import { AddComputer, Audit, Billing, Clients, OrgSettings, Team } from "./views3";
import "./modules";   // optional feature modules register their sections / device tabs (remote screen, android, AI chat)

reg({
  "app.name": { en: "Barmajli CIT", ar: "برمجلي CIT" }, "app.tag": { en: "IT management", ar: "إدارة تقنية المعلومات" },
  "nav.dashboard": { en: "Dashboard", ar: "لوحة المعلومات" }, "nav.devices": { en: "Devices", ar: "الأجهزة" }, "nav.remote": { en: "Remote Control", ar: "التحكم عن بُعد" }, "nav.android": { en: "Android Devices", ar: "أجهزة أندرويد" },
  "nav.ai": { en: "AI Assistant", ar: "المساعد الذكي" }, "nav.jobs": { en: "Jobs & Automation", ar: "المهام والأتمتة" }, "nav.monitoring": { en: "Monitoring & Alerts", ar: "المراقبة والتنبيهات" }, "nav.clients": { en: "Clients & Sites", ar: "العملاء والمواقع" },
  "nav.reports": { en: "Reports & Tickets", ar: "التقارير والتذاكر" }, "nav.admin": { en: "Administration & Settings", ar: "الإدارة والإعدادات" },
  "app.menu": { en: "Menu", ar: "القائمة" }, "app.theme": { en: "Dark / light", ar: "داكن / فاتح" }, "app.lang": { en: "العربية", ar: "English" }, "app.signout": { en: "Sign out", ar: "تسجيل الخروج" },
  "app.stop": { en: "Emergency stop", ar: "إيقاف طارئ" }, "app.stopTip": { en: "Revokes all sessions and disconnects every target immediately.", ar: "يُلغي جميع الجلسات ويفصل كل الأهداف فوراً." }, "app.stopConfirm": { en: "Revoke all sessions and disconnect every target now?", ar: "إلغاء جميع الجلسات وفصل كل الأهداف الآن؟" }, "app.stopped": { en: "All sessions were revoked and targets disconnected.", ar: "تم إلغاء جميع الجلسات وفصل الأهداف." },
  "app.backendDown": { en: "Cannot reach the CIT service. It is probably restarting — retrying automatically.", ar: "تعذر الوصول إلى خدمة CIT. ربما تُعاد تشغيلها — تتم إعادة المحاولة تلقائياً." },
  "login.token": { en: "Access token", ar: "رمز الوصول" }, "login.go": { en: "Sign in", ar: "دخول" }, "login.hint": { en: "Your administrator gives you a personal token (or an invitation to create one). It is kept only in this browser tab.", ar: "يعطيك مسؤولك رمزاً شخصياً (أو دعوة لإنشائه). يُحفظ في تبويب المتصفح هذا فقط." },
  "adm.sub.team": { en: "People & roles", ar: "الأشخاص والصلاحيات" }, "adm.sub.billing": { en: "Subscription", ar: "الاشتراك" }, "adm.sub.audit": { en: "Audit log", ar: "سجل التدقيق" }, "adm.sub.settings": { en: "Client settings", ar: "إعدادات العميل" },
  "adm.sub.add": { en: "Add computer", ar: "إضافة حاسوب" }, "adm.sub.advanced": { en: "Advanced tools", ar: "أدوات متقدمة" },
  "adv.pick": { en: "Tool", ar: "الأداة" }, "adv.note": { en: "Older technical tools. Their screens are in English only.", ar: "أدوات تقنية أقدم. شاشاتها بالإنجليزية فقط." },
  "adv.workers": { en: "AI Worker stations (classic)", ar: "محطات العمل (كلاسيكي)" }, "adv.quick": { en: "Quick connect", ar: "اتصال سريع" }, "adv.workspace": { en: "AI workspace", ar: "مساحة عمل الذكاء الاصطناعي" }, "adv.projects": { en: "Projects", ar: "المشاريع" },
  "adv.servers": { en: "Servers", ar: "الخوادم" }, "adv.fixes": { en: "Diagnostics & repairs", ar: "التشخيص والإصلاح" }, "adv.deployments": { en: "Deployments", ar: "عمليات النشر" }, "adv.settings": { en: "Feature status", ar: "حالة الميزات" },
});

type Page = (p: { c: P.Ctx }) => JSX.Element;
const Settings: Page = ({ c }) => <><P.Settings c={c} /><Q.FeatureStatus /></>;
const LegacyTabs = ({ c, tabs }: { c: P.Ctx; tabs: [string, Page][] }) => {
  const [i, setI] = useState(0); const Cur = tabs[i][1];
  return <><div className="row" style={{ marginBottom: 12 }}>{tabs.map(([k], j) => <button key={k} className={"btn sm " + (i === j ? "" : "ghost")} onClick={() => setI(j)}>{k}</button>)}</div><Cur c={c} /></>;
};
const LEGACY: [string, Page][] = [
  ["adv.workers", Workers], ["adv.quick", Q.QuickConnect], ["adv.workspace", Q.Workspace], ["adv.projects", Q.Projects], ["adv.servers", Q.Servers],
  ["adv.fixes", ({ c }) => <LegacyTabs c={c} tabs={[["Diagnostics", P.Diagnostics], ["Repairs", P.Repairs], ["Metrics", P.Metrics], ["Console", P.Console], ["Software", P.Software], ["Hardware", P.Hardware], ["Logs", P.Logs]]} />],
  ["adv.deployments", Q.Deployments], ["adv.settings", Settings],
];

function Advanced({ c }: { c: P.Ctx }) {
  useLang(); const [i, setI] = useState(0); const Cur = LEGACY[i][1];
  return <div className="card"><div className="banner">{t("adv.note")}</div><div className="row" style={{ marginBottom: 12 }}><label style={{ margin: 0 }}>{t("adv.pick")}</label><select style={{ width: 280 }} value={i} onChange={e => setI(+e.target.value)} aria-label={t("adv.pick")}>{LEGACY.map(([k], j) => <option key={k} value={j}>{t(k)}</option>)}</select></div><div dir="ltr"><Cur c={c} /></div></div>;
}

function Admin({ c, sub, setSub }: { c: P.Ctx; sub: string; setSub: (s: string) => void }) {
  useLang(); const me = useMe(); const senior = isSenior(me);
  const keys = ["team", "billing", "audit", "settings", ...(me?.role === "owner" || senior ? ["add"] : []), ...(me?.role === "owner" ? ["advanced"] : [])];
  return <><Tabs tabs={keys.map(k => [k, t("adm.sub." + k)] as [string, string])} value={sub} onChange={setSub} />{sub === "team" && <Team />}{sub === "billing" && <Billing />}{sub === "audit" && <Audit />}{sub === "settings" && <OrgSettings />}{sub === "add" && <AddComputer />}{sub === "advanced" && <Advanced c={c} />}</>;
}

function Login({ onDone }: { onDone: () => void }) {
  useLang(); const [tok, setTok] = useState(""); const [err, setErr] = useState(""); const [busy, setBusy] = useState(false);
  const go = async () => { setBusy(true); setErr(""); signIn(tok.trim()); try { await api("/me"); onDone(); } catch (e: any) { try { sessionStorage.removeItem("cit_token"); } catch {} setErr(e.message); } finally { setBusy(false); } };
  return <div style={{ maxWidth: 420, margin: "12vh auto", padding: 12 }}><div className="card"><div className="row"><div style={{ width: 36, height: 36, borderRadius: 10, background: "var(--acc)", color: "#fff", display: "grid", placeItems: "center", fontWeight: 800 }}>B</div><h2 style={{ margin: 0 }}>{t("app.name")}</h2></div>
    <label htmlFor="tok">{t("login.token")}</label><input id="tok" className="ltr" type="password" autoComplete="off" value={tok} onChange={e => setTok(e.target.value)} onKeyDown={e => e.key === "Enter" && tok && go()} />
    {err && <div className="err mt" role="alert">{err}</div>}<button className="btn mt" disabled={busy || !tok} onClick={go}>{t("login.go")}</button><div className="tag mt">{t("login.hint")}</div>
    <div className="row mt"><button className="btn ghost sm" onClick={() => setLang(getLang() === "en" ? "ar" : "en")}>{t("app.lang")}</button></div></div></div>;
}

export default function App() {
  const [authed, setAuthed] = useState(hasToken());
  return authed ? <Main /> : <Login onDone={() => setAuthed(true)} />;
}

const CUSTOMER = ["dashboard", "monitoring", "reports"];   // customers see their own summary only; the server enforces this independently

function Main() {
  const lang = useLang(); const me = useMe(); const customer = me?.role === "customer";
  const [page, setPage] = useState("dashboard"); const [open, setOpen] = useState(""); const [adminSub, setAdminSub] = useState("team"); const [menu, setMenu] = useState(false);
  const [devices, setDevices] = useState<P.Device[]>([]); const [sel, setSel] = useState(""); const [status, setStatus] = useState<P.Ctx["status"]>(null); const [err, setErr] = useState(""); const [note, setNote] = useState("");
  const [theme, setTheme] = useState(() => { try { return localStorage.getItem("theme") || "light"; } catch { return "light"; } });
  useEffect(() => { document.documentElement.dataset.theme = theme; try { localStorage.setItem("theme", theme); } catch {} }, [theme]);
  const refresh = useCallback(async () => {
    try { const [d, s] = await Promise.all([api<P.Device[]>("/devices"), api("/status")]); setDevices(d); setStatus(s); setErr(""); setSel(cur => cur || d.find((x: P.Device) => x.status === "paired" && x.transport !== "local")?.id || d[0]?.id || ""); }
    catch (e: any) { setErr(/fetch|network|failed/i.test(e.message) ? t("app.backendDown") : e.message); }
  }, []);
  useEffect(() => { refresh(); const i = setInterval(refresh, 10000); return () => clearInterval(i); }, [refresh]);
  const goto = (p: string) => { if (p === "add-computer") { setPage("admin"); setAdminSub("add"); } else { setPage(p); if (p !== "devices") setOpen(""); } setMenu(false); };
  const ctx: P.Ctx = { devices, dev: devices.find(d => d.id === sel), refresh, select: setSel, goto, status };
  const stop = async () => { if (!confirm(t("app.stopConfirm"))) return; try { await api("/emergency-stop", "POST"); setNote(t("app.stopped")); refresh(); } catch (e: any) { setErr(e.message); } };

  const base: { key: string; icon: string; order: number; label: string }[] = [
    { key: "dashboard", icon: "dashboard", order: 10, label: "nav.dashboard" }, { key: "devices", icon: "devices", order: 20, label: "nav.devices" },
    { key: "jobs", icon: "jobs", order: 60, label: "nav.jobs" }, { key: "monitoring", icon: "monitoring", order: 70, label: "nav.monitoring" }, { key: "clients", icon: "clients", order: 80, label: "nav.clients" },
    { key: "reports", icon: "reports", order: 90, label: "nav.reports" }, { key: "admin", icon: "admin", order: 100, label: "nav.admin" },
  ];
  const all = [...base, ...extraSections().map(s => ({ key: s.key, icon: s.icon, order: s.order, label: s.label }))].sort((a, b) => a.order - b.order).filter(s => !customer || CUSTOMER.includes(s.key));
  const ex = extraSections().find(s => s.key === page);
  const title = t(all.find(s => s.key === page)?.label ?? "nav.dashboard");
  return (
    <div className={"app" + (menu ? " open" : "")} key={lang}>
      <nav className="side" aria-label={t("app.menu")}>
        <div className="brand"><div className="logo">B</div><div><b>{t("app.name")}</b><small>{t("app.tag")}</small></div></div>
        {all.map(s => <button key={s.key} className={"item" + (page === s.key ? " on" : "")} aria-current={page === s.key ? "page" : undefined} onClick={() => goto(s.key)}><Icon name={s.icon} />{t(s.label)}</button>)}
        <div className="spacer" />
        <div className="foot"><button onClick={() => setTheme(theme === "light" ? "dark" : "light")} title={t("app.theme")}><Icon name="moon" size={14} /> {t("app.theme")}</button><button onClick={() => setLang(lang === "en" ? "ar" : "en")}><Icon name="globe" size={14} /> {t("app.lang")}</button><button onClick={signOut}><Icon name="out" size={14} /> {t("app.signout")}</button></div>
      </nav>
      {menu && <div className="scrim" onClick={() => setMenu(false)} />}
      <div className="content">
        <header className="top"><button className="menu-btn" aria-label={t("app.menu")} onClick={() => setMenu(true)}><Icon name="menu" /></button><h2>{title}</h2>
          {status && <span className={"pill " + (status.audit_chain_ok ? "ok" : "bad")} title="audit">●</span>}
          {!customer && <button className="btn danger sm" onClick={stop} title={t("app.stopTip")}><Icon name="stop" size={14} /> {t("app.stop")}</button>}</header>
        <main>
          {note && <div className="banner" role="status">{note}</div>}{err && <div className="err" role="alert">{err}</div>}
          <div className="fade" key={page}>
            {page === "dashboard" && <Dashboard />}{page === "devices" && <Devices open={open} setOpen={setOpen} />}{page === "jobs" && <JobsHub />}{page === "monitoring" && <Monitoring />}
            {page === "clients" && <Clients goto={goto} />}{page === "reports" && <ReportsTickets />}{page === "admin" && <Admin c={ctx} sub={adminSub} setSub={setAdminSub} />}{ex && ex.render({ goto })}
          </div>
        </main>
      </div>
    </div>
  );
}
