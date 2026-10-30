import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { setLang, t, useLang } from "./i18n";
import * as P from "./pages";

const NAV = ["overview", "discovery", "inventory", "metrics", "diagnostics", "chat", "repairs", "remote", "console",
  "software", "hardware", "logs", "reports", "audit", "settings"] as const;

export default function App() {
  const lang = useLang();
  const [page, setPage] = useState<(typeof NAV)[number]>("overview");
  const [devices, setDevices] = useState<P.Device[]>([]);
  const [sel, setSel] = useState<string>("");
  const [status, setStatus] = useState<{ mode: string; audit_chain_ok: boolean; offline_queue: number } | null>(null);
  const [err, setErr] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [d, s] = await Promise.all([api<P.Device[]>("/devices"), api("/status")]);
      setDevices(d); setStatus(s); setErr("");
      setSel(cur => cur || d.find((x: P.Device) => x.status === "paired")?.id || d[0]?.id || "");
    } catch (e: any) { setErr(e.message); }
  }, []);
  useEffect(() => { refresh(); const i = setInterval(refresh, 10000); return () => clearInterval(i); }, [refresh]);

  const dev = devices.find(d => d.id === sel);
  const ctx: P.Ctx = { devices, dev, refresh, select: setSel, goto: p => setPage(p as any), status };
  const Page = ({ overview: P.Overview, discovery: P.Discovery, inventory: P.Inventory, metrics: P.Metrics, diagnostics: P.Diagnostics,
    chat: P.Chat, repairs: P.Repairs, remote: P.Remote, console: P.Console, software: P.Software, hardware: P.Hardware,
    logs: P.Logs, reports: P.Reports, audit: P.Audit, settings: P.Settings } as Record<string, (p: { c: P.Ctx }) => JSX.Element>)[page];

  return (
    <div className="app" key={lang}>
      <nav>
        <h1>{t("app")}<small>v0.1 MVP</small></h1>
        {NAV.map(n => <button key={n} className={page === n ? "on" : ""} onClick={() => setPage(n)}>{t(n)}</button>)}
        <div style={{ flex: 1 }} />
        <button onClick={() => setLang(lang === "en" ? "ar" : "en")}>{t("lang")}</button>
      </nav>
      <main>
        <header className="top">
          <h2>{t(page)}</h2>
          <select style={{ width: 220 }} value={sel} onChange={e => setSel(e.target.value)} aria-label={t("device")}>
            {devices.length === 0 && <option value="">{t("none")}</option>}
            {devices.map(d => <option key={d.id} value={d.id}>{d.name} ({d.status})</option>)}
          </select>
          {status && <span className={"pill " + (status.mode === "online" ? "ok" : "bad")}>{t(status.mode)}</span>}
        </header>
        {devices.some(d => d.transport === "mock") && <div className="banner">{t("mockBanner")}</div>}
        {err && <div className="err">{err}</div>}
        <Page c={ctx} />
      </main>
    </div>
  );
}
