import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { setLang, t, useLang } from "./i18n";
import * as P from "./pages";
import * as Q from "./pages2";
import { Workers } from "./pages3";

type Page = (p: { c: P.Ctx }) => JSX.Element;
const Tabs = ({ c, tabs }: { c: P.Ctx; tabs: [string, Page][] }) => {
  const [i, setI] = useState(0); const Cur = tabs[i][1];
  return <><div className="row" style={{ marginBottom: 12 }}>{tabs.map(([k], j) => <button key={k} className={"btn " + (i === j ? "" : "ghost")} onClick={() => setI(j)}>{t(k)}</button>)}</div><Cur c={c} /></>;
};
const tabbed = (tabs: [string, Page][]): Page => ({ c }) => <Tabs c={c} tabs={tabs} />;
const Settings: Page = ({ c }) => <><P.Settings c={c} /><Q.FeatureStatus /></>;
const Overview: Page = ({ c }) => <><P.Overview c={c} /></>;

// The 14 primary sections. Every page from the first milestone is still reachable inside them.
const SECTIONS: [string, Page][] = [
  ["overview", Overview], ["workers", Workers], ["devices", tabbed([["inventory", P.Inventory], ["discovery", P.Discovery]])], ["quick", Q.QuickConnect],
  ["workspace", Q.Workspace], ["projects", Q.Projects], ["rclaude", Q.RemoteClaude], ["servers", Q.Servers], ["desktop", Q.Desktop], ["files", Q.Files],
  ["fixes", tabbed([["diagnostics", P.Diagnostics], ["repairs", P.Repairs], ["metrics", P.Metrics], ["chat", P.Chat], ["console", P.Console], ["software", P.Software], ["hardware", P.Hardware], ["logs", P.Logs]])],
  ["deployments", Q.Deployments], ["history", Q.History], ["reports", tabbed([["reports", P.Reports], ["audit", P.Audit]])], ["settings", Settings],
];

export default function App() {
  const lang = useLang();
  const [page, setPage] = useState(0);
  const [devices, setDevices] = useState<P.Device[]>([]);
  const [sel, setSel] = useState<string>("");
  const [status, setStatus] = useState<P.Ctx["status"]>(null);
  const [err, setErr] = useState(""); const [note, setNote] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [d, s] = await Promise.all([api<P.Device[]>("/devices"), api("/status")]);
      setDevices(d); setStatus(s); setErr("");
      setSel(cur => cur || d.find((x: P.Device) => x.status === "paired" && x.transport !== "local")?.id || d[0]?.id || "");
    } catch (e: any) { setErr(e.message); }
  }, []);
  useEffect(() => { refresh(); const i = setInterval(refresh, 10000); return () => clearInterval(i); }, [refresh]);

  const dev = devices.find(d => d.id === sel);
  const goto = (p: string) => { const i = SECTIONS.findIndex(([k]) => k === p); if (i >= 0) setPage(i); else if (["repairs", "diagnostics"].includes(p)) setPage(SECTIONS.findIndex(([k]) => k === "fixes")); else if (["remote", "discovery", "inventory"].includes(p)) setPage(p === "remote" ? 0 : 1); };
  const ctx: P.Ctx = { devices, dev, refresh, select: setSel, goto, status };
  const [key, Page] = SECTIONS[page];
  const stop = async () => { if (!confirm(t("stop") + "?\n\n" + t("stopped") + ".")) return; try { await api("/emergency-stop", "POST"); setNote(t("stopped")); refresh(); } catch (e: any) { setErr(e.message); } };

  return (
    <div className="app" key={lang}>
      <nav>
        <h1>{t("app")}<small>v0.2 · M2</small></h1>
        {SECTIONS.map(([k], i) => <button key={k} className={page === i ? "on" : ""} onClick={() => setPage(i)}>{t(k)}</button>)}
        <div style={{ flex: 1 }} />
        <button onClick={() => setLang(lang === "en" ? "ar" : "en")}>{t("lang")}</button>
      </nav>
      <main>
        <header className="top">
          <h2>{t(key)}</h2>
          <select style={{ width: 240 }} value={sel} onChange={e => setSel(e.target.value)} aria-label={t("device")}>
            {devices.length === 0 && <option value="">{t("none")}</option>}
            {devices.map(d => <option key={d.id} value={d.id}>{d.name} ({d.status}{d.transport === "agent" ? (d as any).live ? ", live" : ", offline" : ""})</option>)}
          </select>
          {status && <span className={"pill " + (status.mode === "online" ? "ok" : "bad")}>{t(status.mode)}</span>}
          <button className="btn danger" onClick={stop} title={t("stopped")}>⏻ {t("stop")}</button>
        </header>
        {devices.some(d => d.transport === "mock") && <div className="banner">{t("mockBanner")}</div>}
        {note && <div className="banner" role="status">{note}</div>}
        {err && <div className="err">{err}</div>}
        <Page c={ctx} />
      </main>
    </div>
  );
}
