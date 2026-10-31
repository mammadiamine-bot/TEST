import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { DataTable, Empty, Err, NoOrg, OrgBar, Pill, Spark, Tabs, isSenior, stl, useAct, useData, useMe, useOrgId, when } from "./kit";
import { reg, t, useLang } from "./i18n";

reg({
  "dash.machines": { en: "Machines", ar: "الأجهزة" }, "dash.allowed": { en: "of {n} allowed", ar: "من أصل {n} مسموح" }, "dash.avail": { en: "Availability (24 h)", ar: "التوفر (24 ساعة)" },
  "dash.alerts": { en: "Open alerts", ar: "التنبيهات المفتوحة" }, "dash.sla": { en: "SLA met", ar: "الالتزام باتفاقية الخدمة" }, "dash.plan": { en: "Plan", ar: "الباقة" },
  "dash.online": { en: "Online now", ar: "متصل الآن" }, "dash.welcome": { en: "Welcome. Pick a computer in “Devices” to see it, control it, or ask the AI about it.", ar: "مرحباً. اختر حاسوباً من «الأجهزة» لعرضه أو التحكم به أو سؤال الذكاء الاصطناعي عنه." },
  "dev.title": { en: "Devices", ar: "الأجهزة" }, "dev.machine": { en: "Computer", ar: "الحاسوب" }, "dev.site": { en: "Site", ar: "الموقع" }, "dev.state": { en: "State", ar: "الحالة" }, "dev.address": { en: "Address", ar: "العنوان" },
  "dev.via": { en: "Connection", ar: "نوع الاتصال" }, "dev.filter": { en: "Filter by state", ar: "تصفية حسب الحالة" }, "dev.all": { en: "All", ar: "الكل" },
  "dev.empty": { en: "No Windows computers connected for this client yet. Open “Clients & Sites” → “Connect the next computer”.", ar: "لا توجد حواسيب ويندوز متصلة لهذا العميل بعد. افتح «العملاء والمواقع» ← «ربط حاسوب جديد»." },
  "dev.bulk": { en: "Run on {n} selected computer(s):", ar: "تشغيل على {n} حاسوب محدد:" }, "dev.dispatch": { en: "Run", ar: "تشغيل" }, "dev.dispatched": { en: "Job created — see “Jobs & Automation”.", ar: "تم إنشاء المهمة — راجع «المهام والأتمتة»." },
  "dev.template": { en: "Template", ar: "القالب" },
  "ws.back": { en: "All devices", ar: "كل الأجهزة" }, "ws.overview": { en: "Overview", ar: "نظرة عامة" }, "ws.terminal": { en: "Terminal", ar: "الطرفية" }, "ws.files": { en: "Files", ar: "الملفات" },
  "ws.diag": { en: "Diagnostics", ar: "التشخيص" }, "ws.monitor": { en: "Monitoring", ar: "المراقبة" }, "ws.jobs": { en: "Jobs", ar: "المهام" }, "ws.history": { en: "History", ar: "السجل" },
  "ws.live": { en: "Live Screen", ar: "الشاشة المباشرة" }, "ws.android": { en: "Android", ar: "أندرويد" }, "ws.ai": { en: "AI Assistant", ar: "المساعد الذكي" },
  "ov.cpu": { en: "CPU (6 h)", ar: "المعالج (6 ساعات)" }, "ov.ram": { en: "Memory (6 h)", ar: "الذاكرة (6 ساعات)" }, "ov.disk": { en: "Disk (6 h)", ar: "القرص (6 ساعات)" },
  "ov.collect": { en: "Collect system info", ar: "جمع معلومات النظام" }, "ov.collecting": { en: "Collecting…", ar: "جارٍ الجمع…" }, "ov.collected": { en: "collected {when}", ar: "جُمعت في {when}" }, "ov.never": { en: "not collected yet", ar: "لم تُجمع بعد" },
  "ov.host": { en: "Computer name", ar: "اسم الحاسوب" }, "ov.os": { en: "Windows", ar: "ويندوز" }, "ov.ram_gb": { en: "Memory (GB)", ar: "الذاكرة (جيجابايت)" }, "ov.disks": { en: "Disks", ar: "الأقراص" }, "ov.net": { en: "Network", ar: "الشبكة" },
  "ov.free": { en: "{free} GB free of {size} GB", ar: "{free} جيجابايت متاحة من {size}" }, "ov.lastseen": { en: "Last seen", ar: "آخر ظهور" },
  "conn.title": { en: "Connection", ar: "الاتصال" }, "conn.lastseen": { en: "Last seen", ar: "آخر ظهور" }, "conn.latency": { en: "Response time", ar: "زمن الاستجابة" }, "conn.next": { en: "Next check in {s} s", ar: "الفحص التالي بعد {s} ثانية" },
  "conn.reconnect": { en: "Reconnect now", ar: "إعادة الاتصال الآن" }, "conn.diagnose": { en: "Diagnose connection", ar: "تشخيص الاتصال" }, "conn.working": { en: "Checking…", ar: "جارٍ الفحص…" }, "conn.verdict": { en: "Result", ar: "النتيجة" }, "conn.ms": { en: "{n} ms", ar: "{n} مللي ثانية" },
  "conn.never": { en: "never", ar: "أبداً" }, "conn.stillDown": { en: "Still not reachable.", ar: "ما زال غير قابل للوصول." }, "conn.backOnline": { en: "Connected.", ar: "تم الاتصال." },
  "conn.step.policy": { en: "Address allowed (private network)", ar: "العنوان مسموح (شبكة خاصة)" }, "conn.step.tcp": { en: "Computer answers on the network", ar: "الحاسوب يستجيب على الشبكة" }, "conn.step.tls": { en: "Secure identity matches", ar: "الهوية الآمنة مطابقة" },
  "conn.step.auth": { en: "Saved credential accepted", ar: "بيانات الاعتماد المحفوظة مقبولة" }, "conn.step.latency": { en: "Response speed", ar: "سرعة الاستجابة" },
  "hint.timeout": { en: "No reply at all: the computer is asleep, switched off, out of Wi-Fi range or on another network. Wake it, or check its power and network settings (see the “Power and network” check).", ar: "لا يوجد أي رد: الحاسوب في وضع السكون أو مغلق أو خارج نطاق الواي فاي أو على شبكة أخرى. أيقظه أو افحص إعدادات الطاقة والشبكة (راجع فحص «الطاقة والشبكة»)." },
  "hint.refused": { en: "The computer answers, but the CIT Worker program is not running or not listening. Start the Worker (it starts when the user logs on).", ar: "الحاسوب يستجيب لكن برنامج CIT Worker غير شغّال أو لا يستمع. شغّل البرنامج (يبدأ عند تسجيل دخول المستخدم)." },
  "hint.identity": { en: "The certificate is different from the one you confirmed. Do NOT connect until you verify it on the computer.", ar: "الشهادة تختلف عن التي أكدتها. لا تتصل قبل التحقق منها على الحاسوب." },
  "hint.auth": { en: "The Worker rejected the saved credential (revoked, expired, or its data was reset). Enroll the computer again.", ar: "رفض Worker بيانات الاعتماد المحفوظة (أُلغيت أو انتهت أو أُعيدت تهيئة بياناته). سجّل الحاسوب من جديد." },
  "hint.unreachable": { en: "No route to the computer: wrong address, or this PC is not on the same network or VPN.", ar: "لا يوجد مسار إلى الحاسوب: عنوان خاطئ أو هذا الجهاز ليس على نفس الشبكة أو الـ VPN." },
  "hint.tls": { en: "The connection started but the secure handshake failed. Another program may be using that port.", ar: "بدأ الاتصال لكن فشل التفاوض الآمن. قد يكون برنامج آخر يستخدم هذا المنفذ." },
  "hint.locked": { en: "The Worker is locked or its authorization window ended. Unlock it on the computer.", ar: "الـ Worker مقفل أو انتهت فترة التفويض. افتحه على الحاسوب." },
  "hint.other": { en: "The Worker did not respond as expected.", ar: "لم يستجب Worker كما هو متوقع." },
  "diag.pick": { en: "Check", ar: "الفحص" }, "diag.run": { en: "Run read-only check", ar: "تشغيل فحص للقراءة فقط" }, "diag.hint": { en: "These checks only read information. Nothing is changed on the computer.", ar: "هذه الفحوصات تقرأ المعلومات فقط. لا يتم تغيير أي شيء على الحاسوب." },
  "diag.inventory.system": { en: "System information", ar: "معلومات النظام" }, "diag.inventory.apps": { en: "Installed software", ar: "البرامج المثبتة" }, "diag.services.list": { en: "Windows services", ar: "خدمات ويندوز" },
  "diag.processes.top": { en: "Running programs (top memory)", ar: "البرامج العاملة (الأعلى استهلاكاً للذاكرة)" }, "diag.eventlog.recent": { en: "Recent errors (event log)", ar: "الأخطاء الأخيرة (سجل الأحداث)" },
  "diag.windows.update.status": { en: "Windows Update status", ar: "حالة تحديث ويندوز" }, "diag.disk.usage": { en: "Disk usage and cleanup candidates", ar: "استخدام القرص وما يمكن تنظيفه" },
  "diag.power.diag": { en: "Power and network (why offline?)", ar: "الطاقة والشبكة (لماذا غير متصل؟)" }, "diag.network.diag": { en: "Network check", ar: "فحص الشبكة" }, "diag.backup.status": { en: "Backup status", ar: "حالة النسخ الاحتياطي" },
  "hist.when": { en: "When", ar: "الوقت" }, "hist.job": { en: "Job", ar: "المهمة" }, "hist.risk": { en: "Risk", ar: "الخطورة" }, "hist.result": { en: "Result", ar: "النتيجة" }, "hist.exit": { en: "Exit code", ar: "رمز الخروج" },
  "hist.who": { en: "Who", ar: "بواسطة" }, "hist.action": { en: "Action", ar: "الإجراء" }, "hist.detail": { en: "Detail", ar: "التفاصيل" },
  "term.note": { en: "Each command runs as one process (CMD or PowerShell) in this client's workspace on the computer. It is not a persistent shell: a folder change does not carry over to the next command.", ar: "كل أمر يعمل كعملية واحدة (CMD أو PowerShell) داخل مساحة عمل هذا العميل على الحاسوب. ليست طرفية دائمة: تغيير المجلد لا ينتقل إلى الأمر التالي." },
  "term.shell": { en: "Shell", ar: "الصدفة" }, "term.cmd": { en: "Enter a CMD command", ar: "اكتب أمر CMD" }, "term.ps": { en: "Enter a PowerShell command", ar: "اكتب أمر PowerShell" }, "term.run": { en: "Run", ar: "تشغيل" }, "term.cancel": { en: "Cancel", ar: "إلغاء" },
  "term.history": { en: "Command history", ar: "سجل الأوامر" }, "term.none": { en: "No commands run yet.", ar: "لم يتم تشغيل أي أمر بعد." }, "term.waiting": { en: "Waiting for output…", ar: "بانتظار المخرجات…" }, "term.nooutput": { en: "[no output]", ar: "[لا توجد مخرجات]" },
  "term.offline": { en: "The computer is offline, so commands cannot run.", ar: "الحاسوب غير متصل، لذلك لا يمكن تشغيل الأوامر." },
  "term.cancelling": { en: "Cancelling", ar: "جارٍ الإلغاء" }, "term.limit": { en: "Stopped waiting", ar: "توقف الانتظار" },
  "files.note": { en: "Files are managed inside this client's workspace on the computer. Uploads and downloads are checked against that boundary.", ar: "تُدار الملفات داخل مساحة عمل هذا العميل على الحاسوب. تُفحص عمليات الرفع والتنزيل ضمن هذه الحدود." },
  "files.path": { en: "Folder", ar: "المجلد" }, "files.up": { en: "Up", ar: "للأعلى" }, "files.refresh": { en: "Refresh", ar: "تحديث" }, "files.upload": { en: "Upload file", ar: "رفع ملف" }, "files.name": { en: "Name", ar: "الاسم" },
  "files.size": { en: "Size", ar: "الحجم" }, "files.modified": { en: "Modified", ar: "آخر تعديل" }, "files.download": { en: "Download", ar: "تنزيل" }, "files.delete": { en: "Delete", ar: "حذف" }, "files.confirmDelete": { en: "Delete “{name}” from the computer? This cannot be undone.", ar: "حذف «{name}» من الحاسوب؟ لا يمكن التراجع عن ذلك." },
  "files.search": { en: "Search by name", ar: "البحث بالاسم" }, "files.empty": { en: "This folder is empty. Upload a file to start.", ar: "هذا المجلد فارغ. ارفع ملفاً للبدء." }, "files.folder": { en: "Folder", ar: "مجلد" }, "files.file": { en: "File", ar: "ملف" },
  "files.tooBig": { en: "Files larger than 8 MB cannot be uploaded from the browser.", ar: "لا يمكن رفع ملفات أكبر من 8 ميجابايت من المتصفح." }, "files.uploaded": { en: "Uploaded.", ar: "تم الرفع." },
});

export const orgClient = (oid: string) => "org_" + (oid.split("_").pop() ?? "").toLowerCase().slice(0, 30);   // the client workspace id used on every Worker (mirrors backend ops.org_client)
export type DevRow = { id: string; name: string; address: string; state: string; path: string; site: string; last_seen?: number | null };

// ---------------------------------------------------------------- Dashboard
export function Dashboard() {
  useLang(); const oid = useOrgId(); const rep = useData<any>(oid ? `/orgs/${oid}/reports/fleet` : null, 15000); const sub = useData<any>(oid ? `/orgs/${oid}/subscription` : null); const sla = useData<any>(oid ? `/orgs/${oid}/reports/sla` : null);
  const fleet = useData<{ sites: any[] }>(oid ? `/orgs/${oid}/fleet` : null, 10000);
  const ws: any[] = (fleet.data?.sites ?? []).flatMap(s => s.workers); const on = ws.filter(w => (w.state ?? w.status) === "online").length;
  return <><OrgBar />{!oid ? <NoOrg /> : <>
    <div className="grid">
      <div className="card"><h3>{t("dash.machines")}</h3><div className="big">{ws.length}</div><div className="tag">{t("dash.allowed", { n: sub.data?.entitlements?.max_devices ?? "—" })}</div></div>
      <div className="card"><h3>{t("dash.online")}</h3><div className="big">{on}</div></div>
      <div className="card"><h3>{t("dash.avail")}</h3><div className="big ltr">{rep.data?.fleet_availability_pct ?? "—"}{rep.data?.fleet_availability_pct != null && "%"}</div></div>
      <div className="card"><h3>{t("dash.alerts")}</h3><div className="big">{rep.data?.open_alerts ?? "—"}</div></div>
      <div className="card"><h3>{t("dash.sla")}</h3><div className="big ltr">{sla.data?.sla_pct ?? "—"}{sla.data?.sla_pct != null && "%"}</div></div>
      <div className="card"><h3>{t("dash.plan")}</h3><div className="big">{sub.data?.plan ?? "—"}</div><div className="tag">{sub.data?.status && stl(sub.data.status)}</div></div></div>
    <div className="banner mt">{t("dash.welcome")}</div></>}</>;
}

// ---------------------------------------------------------------- Devices list
export function Devices({ open, setOpen }: { open: string; setOpen: (id: string) => void }) {
  useLang(); const oid = useOrgId(); const fleet = useData<{ sites: any[] }>(oid ? `/orgs/${oid}/fleet` : null, 10000);
  const [sel, setSel] = useState<string[]>([]); const [state, setState] = useState("all");
  const rows: DevRow[] = (fleet.data?.sites ?? []).flatMap(s => s.workers.map((w: any) => ({ id: w.id, name: w.name, address: w.address, state: w.state ?? w.status, path: w.path, site: s.name, last_seen: w.last_seen })));
  const shown = rows.filter(r => state === "all" || r.state === state);
  if (open) return <Workspace wid={open} oid={oid} onBack={() => setOpen("")} row={rows.find(r => r.id === open)} />;
  return <><OrgBar />{!oid ? <NoOrg /> : <div className="card">
    <DataTable rows={fleet.data ? shown : null} loading={fleet.loading} error={fleet.err} selectable selected={sel} onSelect={setSel} onRow={r => setOpen(r.id)} empty={t("dev.empty")}
      filters={<select aria-label={t("dev.filter")} style={{ width: 170 }} value={state} onChange={e => setState(e.target.value)}><option value="all">{t("dev.all")}</option>{["online", "degraded", "reconnecting", "offline", "needs_pairing"].map(s => <option key={s} value={s}>{stl(s)}</option>)}</select>}
      cols={[{ head: t("dev.machine"), cell: r => <b>{r.name}</b>, text: r => r.name }, { head: t("dev.site"), cell: r => r.site, text: r => r.site }, { head: t("dev.state"), cell: r => <Pill v={r.state} />, text: r => stl(r.state) },
        { head: t("dev.address"), cell: r => <span className="ltr">{r.address}</span>, text: r => r.address }, { head: t("dev.via"), cell: r => stl(r.path), text: r => stl(r.path) }]} />
    {sel.length > 0 && <BulkBar ids={sel} oid={oid} />}</div>}</>;
}

function BulkBar({ ids, oid }: { ids: string[]; oid: string }) {
  useLang(); const { err, busy, ok, go } = useAct(); const tp = useData<any[]>("/ops/templates"); const [tpl, setTpl] = useState("inventory.system");
  return <div className="row mt"><span className="tag">{t("dev.bulk", { n: ids.length })}</span><select aria-label={t("dev.template")} style={{ width: 360 }} value={tpl} onChange={e => setTpl(e.target.value)}>{(tp.data ?? []).map(x => <option key={x.id} value={x.id}>{x.title} [{x.risk}]</option>)}</select>
    <button className="btn" disabled={busy || ["service.restart", "service.stop"].includes(tpl)} onClick={() => go(() => api(`/orgs/${oid}/jobs`, "POST", { template: tpl, targets: ids }), t("dev.dispatched"))}>{t("dev.dispatch")}</button><Err m={err} />{ok && <span className="tag">{ok}</span>}</div>;
}

// ---------------------------------------------------------------- Device workspace (tabs)
export type WsTab = { key: string; label: string; node: (p: { wid: string; oid: string; row?: DevRow }) => JSX.Element };
const EXTRA: WsTab[] = [];
/** Later modules (live screen, android, AI) register their tab here so the workspace stays one coherent place. */
export const registerWorkspaceTab = (tab: WsTab) => { if (!EXTRA.find(x => x.key === tab.key)) EXTRA.push(tab); };

function Workspace({ wid, oid, onBack, row }: { wid: string; oid: string; onBack: () => void; row?: DevRow }) {
  useLang(); const [tab, setTab] = useState("overview");
  const base: [string, string][] = [["overview", t("ws.overview")], ...EXTRA.map(x => [x.key, t("ws." + x.key)] as [string, string]), ["terminal", t("ws.terminal")], ["files", t("ws.files")], ["diag", t("ws.diag")], ["monitor", t("ws.monitor")], ["jobs", t("ws.jobs")], ["history", t("ws.history")]];
  const ex = EXTRA.find(x => x.key === tab);
  return <div className="fade"><div className="row" style={{ marginBottom: 10 }}><button className="btn ghost sm" onClick={onBack}><span className="flip">‹</span> {t("ws.back")}</button><h3 style={{ margin: 0, fontSize: 17, textTransform: "none", color: "var(--tx)" }}>{row?.name ?? wid}</h3>{row && <Pill v={row.state} />}<span className="tag ltr">{wid}</span></div>
    <Tabs tabs={base} value={tab} onChange={setTab} />
    {tab === "overview" && <Overview wid={wid} oid={oid} row={row} />}{ex && ex.node({ wid, oid, row })}
    {tab === "terminal" && <Terminal wid={wid} oid={oid} online={row?.state === "online" || row?.state === "degraded"} />}{tab === "files" && <Files wid={wid} oid={oid} />}
    {tab === "diag" && <Diagnostics wid={wid} />}{tab === "monitor" && <WsMonitor wid={wid} oid={oid} />}{tab === "jobs" && <WsJobs wid={wid} />}{tab === "history" && <WsHistory wid={wid} oid={oid} />}</div>;
}

function useLatest(wid: string, kind: string) { return useData<{ ts: number | null; data: any }>(`/workers/${wid}/inventory/${kind}`); }

function ConnectionCard({ wid }: { wid: string }) {
  useLang(); const c = useData<any>(`/workers/${wid}/connection`, 5000); const { err, busy, go } = useAct(); const [diag, setDiag] = useState<any>(null); const [msg, setMsg] = useState(""); const d = c.data;
  return <div className="card"><div className="row"><h3 style={{ flex: 1, margin: 0 }}>{t("conn.title")}</h3>{d && <Pill v={d.state} />}
      <button className="btn ghost sm" disabled={busy} onClick={() => go(async () => { const r = await api<any>(`/workers/${wid}/reconnect`, "POST"); setMsg(r.state === "online" ? t("conn.backOnline") : t("conn.stillDown")); c.reload(); })}>{busy ? t("conn.working") : t("conn.reconnect")}</button>
      <button className="btn ghost sm" disabled={busy} onClick={() => go(async () => setDiag(await api(`/workers/${wid}/diagnose`, "POST")))}>{t("conn.diagnose")}</button></div>
    <Err m={err || c.err} />{d && <div className="grid mt"><div><div className="tag">{t("conn.lastseen")}</div><b>{d.last_seen ? when(d.last_seen) : t("conn.never")}</b></div><div><div className="tag">{t("conn.latency")}</div><b className="ltr">{d.latency_ms != null ? t("conn.ms", { n: d.latency_ms }) : "—"}</b></div>
      {d.next_check_in != null && <div><div className="tag">{t("conn.next", { s: d.next_check_in })}</div></div>}</div>}
    {d?.error_code && <div className="banner mt">{t("hint." + d.error_code)}</div>}{msg && <div className="tag mt">{msg}</div>}
    {diag && <div className="mt"><div className="tbl-wrap"><table><tbody>{diag.steps.map((s: any) => <tr key={s.step}><td>{s.ok ? "✔" : "✖"}</td><td>{t("conn.step." + s.step)}</td><td className="num ltr">{t("conn.ms", { n: s.ms })}</td><td className="tag ltr">{s.detail}</td></tr>)}</tbody></table></div>{diag.verdict !== "ok" && <div className="banner mt">{t("conn.verdict")}: {t("hint." + diag.verdict)}</div>}</div>}</div>;
}

function Overview({ wid, oid, row }: { wid: string; oid: string; row?: DevRow }) {
  useLang(); const m = useData<any[]>(`/workers/${wid}/metrics?hours=6`, 15000); const inv = useLatest(wid, "inventory.system"); const { err, busy, go } = useAct(); const d = inv.data?.data;
  return <><ConnectionCard wid={wid} /><div className="grid mt"><div className="card"><Spark label={t("ov.cpu")} values={(m.data ?? []).map(x => x.cpu)} /></div><div className="card"><Spark label={t("ov.ram")} values={(m.data ?? []).map(x => x.ram)} /></div><div className="card"><Spark label={t("ov.disk")} values={(m.data ?? []).map(x => x.disk)} /></div></div>
    <div className="card mt"><div className="row"><button className="btn" disabled={busy} onClick={() => go(async () => { const r = await api<any>(`/workers/${wid}/inventory/inventory.system`, "POST"); if (r.status !== "ok") throw new Error(r.error || r.status); inv.reload(); })}>{busy ? t("ov.collecting") : t("ov.collect")}</button>
      <span className="tag">{inv.data?.ts ? t("ov.collected", { when: when(inv.data.ts) }) : t("ov.never")}</span>{row?.last_seen && <span className="tag">{t("ov.lastseen")}: {when(row.last_seen)}</span>}</div><Err m={err} />
      {d && <div className="grid mt"><div><div className="tag">{t("ov.host")}</div><b className="ltr">{d.host}</b></div><div><div className="tag">{t("ov.os")}</div><b className="ltr">{d.os?.Caption} {d.os?.Version}</b></div><div><div className="tag">CPU</div><b className="ltr">{d.cpu?.Name}</b></div><div><div className="tag">{t("ov.ram_gb")}</div><b className="ltr">{d.ram_gb}</b></div>
        <div><div className="tag">{t("ov.disks")}</div>{(d.disks ?? []).map((k: any) => <div key={k.DeviceID} className="ltr">{k.DeviceID} {t("ov.free", { free: k.free_gb, size: k.size_gb })}</div>)}</div><div><div className="tag">{t("ov.net")}</div>{(d.net ?? []).map((n: any) => <div key={n.InterfaceAlias} className="ltr">{n.InterfaceAlias}: {n.ipv4}</div>)}</div></div>}</div></>;
}

const DIAG = ["power.diag", "inventory.system", "inventory.apps", "services.list", "processes.top", "eventlog.recent", "windows.update.status", "disk.usage", "network.diag", "backup.status"];
function Diagnostics({ wid }: { wid: string }) {
  useLang(); const [kind, setKind] = useState(DIAG[0]); const latest = useLatest(wid, kind); const { err, busy, go } = useAct(); const data = latest.data?.data; const list = Array.isArray(data) ? data : null;
  return <div className="card"><div className="tag">{t("diag.hint")}</div><div className="row mt"><label style={{ margin: 0 }}>{t("diag.pick")}</label><select aria-label={t("diag.pick")} style={{ width: 300 }} value={kind} onChange={e => setKind(e.target.value)}>{DIAG.map(k => <option key={k} value={k}>{t("diag." + k)}</option>)}</select>
    <button className="btn" disabled={busy} onClick={() => go(async () => { const r = await api<any>(`/workers/${wid}/inventory/${kind}`, "POST"); if (r.status !== "ok") throw new Error(r.error || r.status); latest.reload(); })}>{busy ? t("ov.collecting") : t("diag.run")}</button>
    <span className="tag">{latest.data?.ts ? t("ov.collected", { when: when(latest.data.ts) }) : t("ov.never")}</span></div><Err m={err || latest.err} />
    {list ? <div className="mt"><DataTable rows={list.map((x, i) => ({ id: String(i), ...x }))} pageSize={10} cols={Object.keys(list[0] ?? {}).slice(0, 6).map(k => ({ head: k, cell: (r: any) => <span className="ltr">{String(typeof r[k] === "object" ? JSON.stringify(r[k]) : r[k] ?? "")}</span>, text: (r: any) => String(r[k] ?? "") }))} /></div> : data ? <pre>{JSON.stringify(data, null, 2)}</pre> : null}</div>;
}

function WsMonitor({ wid, oid }: { wid: string; oid: string }) {
  useLang(); const m = useData<any[]>(`/workers/${wid}/metrics?hours=24`, 15000); const al = useData<any[]>(`/orgs/${oid}/alerts`, 10000);
  return <><div className="grid"><div className="card"><Spark label={t("ov.cpu")} values={(m.data ?? []).map(x => x.cpu)} /></div><div className="card"><Spark label={t("ov.ram")} values={(m.data ?? []).map(x => x.ram)} /></div><div className="card"><Spark label={t("ov.disk")} values={(m.data ?? []).map(x => x.disk)} /></div></div>
    <div className="card mt"><DataTable rows={al.data ? al.data.filter(a => a.worker_id === wid) : null} loading={al.loading} error={al.err} pageSize={6} empty={t("kit.empty")} cols={[{ head: t("dev.state"), cell: a => <Pill v={a.state} />, text: a => a.state }, { head: t("hist.detail"), cell: a => a.message, text: a => a.message }, { head: t("hist.when"), cell: a => when(a.last_seen) }]} /></div></>;
}
function WsJobs({ wid }: { wid: string }) {
  useLang(); const runs = useData<any[]>(`/workers/${wid}/runs`, 8000);
  return <div className="card"><DataTable rows={runs.data} loading={runs.loading} error={runs.err} pageSize={10} cols={[{ head: t("hist.when"), cell: r => when(r.started_at) }, { head: t("hist.job"), cell: r => r.title, text: r => r.title }, { head: t("hist.risk"), cell: r => r.risk }, { head: t("hist.result"), cell: r => <Pill v={r.status} />, text: r => stl(r.status) }, { head: t("hist.exit"), num: true, cell: r => r.exit_code ?? "—" }]} /></div>;
}
function WsHistory({ wid, oid }: { wid: string; oid: string }) {
  useLang(); const a = useData<any[]>(`/orgs/${oid}/audit?limit=500`, 15000); const me = useMe();
  if (!isSenior(me) && me && me.role !== "technician" && me.role !== "viewer") return <div className="card empty">{t("kit.empty")}</div>;
  return <div className="card"><DataTable rows={a.data ? a.data.filter(x => x.detail.includes(wid)).map(x => ({ id: String(x.seq), ...x })) : null} loading={a.loading} error={a.err} pageSize={10} cols={[{ head: t("hist.when"), cell: x => when(x.ts) }, { head: t("hist.who"), cell: x => x.actor, text: x => x.actor }, { head: t("hist.action"), cell: x => <b>{x.action}</b>, text: x => x.action }, { head: t("hist.detail"), cell: x => <code className="tag">{x.detail}</code>, text: x => x.detail }]} /></div>;
}

// ---------------------------------------------------------------- Terminal (CMD / PowerShell)
type Run = { id: string; command: string; started: number; status: string; output: string; exit: number | null };
function Terminal({ wid, oid, online }: { wid: string; oid: string; online: boolean }) {
  useLang(); const cid = orgClient(oid); const [cmd, setCmd] = useState(""); const [shell, setShell] = useState<"cmd" | "powershell">("cmd"); const [runs, setRuns] = useState<Run[]>([]); const [err, setErr] = useState(""); const [busy, setBusy] = useState(false);
  const [active, setActive] = useState<{ job: string; runId: string } | null>(null); const running = useRef(false); const ready = useRef(false);
  useEffect(() => { ready.current = false; api(`/workers/${wid}/w/clients`, "POST", { id: cid, name: "CIT " + cid }).catch(() => {}).finally(() => { ready.current = true; }); }, [wid, cid]);   // 409 = already there
  const W = (p: string) => `/workers/${wid}/w${p}`;
  const run = async () => {
    const command = cmd.trim(); if (!command || running.current) return; running.current = true; setBusy(true); setErr(""); setCmd("");
    const runId = `${Date.now()}-${Math.random().toString(36).slice(2)}`; setRuns(o => [...o, { id: runId, command, started: Date.now(), status: "running", output: "", exit: null }]);
    const upd = (p: Partial<Run>) => setRuns(o => o.map(x => x.id === runId ? { ...x, ...p } : x));
    try {
      const { job } = await api<{ job: string }>(W(`/clients/${cid}/exec`), "POST", { argv: shell === "cmd" ? ["cmd.exe", "/d", "/c", command] : ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], timeout: 3600 });
      setActive({ job, runId }); let since = 0, out = "";
      for (let i = 0; i < 7200; i++) {
        const j = await api<any>(W(`/clients/${cid}/jobs/${job}?since=${since}`)); out += j.output ?? ""; since = j.next;
        upd({ output: out, status: j.done ? (j.cancelled ? "cancelled" : j.timed_out ? "timed_out" : "completed") : "running", exit: j.exit }); if (j.done) break; await new Promise(r => setTimeout(r, 500));
      }
    } catch (e: any) { upd({ status: "failed", output: e.message }); setErr(e.message); } finally { setActive(null); running.current = false; setBusy(false); }
  };
  const cancel = async () => { if (!active) return; setRuns(o => o.map(x => x.id === active.runId ? { ...x, status: "running" } : x)); try { await api(W(`/clients/${cid}/jobs/${active.job}/cancel`), "POST"); } catch (e: any) { setErr(e.message); } };
  return <div className="card"><Err m={err} />{!online && <div className="banner">{t("term.offline")}</div>}<div className="tag">{t("term.note")}</div>
    <div className="row mt"><select aria-label={t("term.shell")} style={{ width: 140 }} value={shell} onChange={e => setShell(e.target.value as any)} disabled={busy}><option value="cmd">CMD</option><option value="powershell">PowerShell</option></select>
      <input aria-label={t("term.shell")} className="ltr grow" value={cmd} onChange={e => setCmd(e.target.value)} onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); void run(); } }} placeholder={shell === "cmd" ? t("term.cmd") : t("term.ps")} disabled={busy || !online} />
      <button className="btn" disabled={!cmd.trim() || busy || !online} onClick={() => void run()}>{t("term.run")}</button>{active && <button className="btn danger" onClick={() => void cancel()}>{t("term.cancel")}</button>}</div>
    <h3 className="mt">{t("term.history")}</h3><div style={{ maxHeight: 420, overflow: "auto" }}>{runs.length ? runs.slice().reverse().map(x => <div key={x.id} className="card mt"><div className="row"><code>{x.command}</code><Pill v={x.status} />{x.exit !== null && <span className="tag">{t("hist.exit")}: {x.exit}</span>}<time className="tag">{new Date(x.started).toLocaleTimeString()}</time></div>
      <pre>{x.output || (x.status === "running" ? t("term.waiting") : t("term.nooutput"))}</pre></div>) : <Empty title={t("term.none")} />}</div></div>;
}

// ---------------------------------------------------------------- Files
type Ent = { name: string; dir: boolean; size: number; mtime: number; link: boolean };
function b64(buf: ArrayBuffer): string { let s = ""; const a = new Uint8Array(buf); for (let i = 0; i < a.length; i += 0x8000) s += String.fromCharCode(...a.subarray(i, i + 0x8000)); return btoa(s); }
function Files({ wid, oid }: { wid: string; oid: string }) {
  useLang(); const cid = orgClient(oid); const [path, setPath] = useState(""); const [ents, setEnts] = useState<Ent[] | null>(null); const [err, setErr] = useState(""); const [ok, setOk] = useState(""); const [q, setQ] = useState(""); const fileIn = useRef<HTMLInputElement>(null);
  const W = (p: string) => `/workers/${wid}/w/clients/${cid}${p}`; const join = (n: string) => (path ? path + "/" + n : n);
  const load = useCallback(async (p: string) => { try { await api(`/workers/${wid}/w/clients`, "POST", { id: cid, name: "CIT " + cid }).catch(() => {}); const r = await api<{ entries: Ent[] }>(W(`/fs/list?path=${encodeURIComponent(p)}`)); setEnts(r.entries); setPath(p); setErr(""); } catch (e: any) { setErr(e.message); setEnts([]); } }, [wid, cid]);
  useEffect(() => { load(""); }, [load]);
  const up = () => load(path.split("/").slice(0, -1).join("/"));
  const download = async (n: string) => { try { const r = await api<any>(W(`/fs/read?path=${encodeURIComponent(join(n))}`)); const bin = atob(r.content_b64); const a = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) a[i] = bin.charCodeAt(i); const url = URL.createObjectURL(new Blob([a])); const l = document.createElement("a"); l.href = url; l.download = n; l.click(); URL.revokeObjectURL(url); } catch (e: any) { setErr(e.message); } };
  const del = async (n: string) => { if (!confirm(t("files.confirmDelete", { name: n }))) return; try { await api(W("/fs/delete"), "POST", { path: join(n) }); load(path); } catch (e: any) { setErr(e.message); } };
  const upload = async (f: File) => { if (f.size > 8 * 1024 * 1024) { setErr(t("files.tooBig")); return; } try { await api(W("/fs/write"), "PUT", { path: join(f.name), content_b64: b64(await f.arrayBuffer()) }); setOk(t("files.uploaded")); load(path); } catch (e: any) { setErr(e.message); } };
  const shown = (ents ?? []).filter(e => !q || e.name.toLowerCase().includes(q.toLowerCase()));
  return <div className="card"><div className="tag">{t("files.note")}</div><Err m={err} />
    <div className="row mt"><button className="btn ghost sm" disabled={!path} onClick={up}>↑ {t("files.up")}</button><span>{t("files.path")}: <code>/{path}</code></span><button className="btn ghost sm" onClick={() => load(path)}>{t("files.refresh")}</button>
      <input aria-label={t("files.search")} placeholder={t("files.search")} style={{ maxWidth: 220 }} value={q} onChange={e => setQ(e.target.value)} />
      <input ref={fileIn} type="file" hidden onChange={e => { const f = e.target.files?.[0]; if (f) void upload(f); e.target.value = ""; }} /><button className="btn sm" onClick={() => fileIn.current?.click()}>{t("files.upload")}</button>{ok && <span className="tag">{ok}</span>}</div>
    <div className="mt"><div className="tbl-wrap"><table><thead><tr><th>{t("files.name")}</th><th className="num">{t("files.size")}</th><th>{t("files.modified")}</th><th /></tr></thead><tbody>
      {shown.map(e => <tr key={e.name}><td className="ltr">{e.dir ? <button className="btn ghost sm" onClick={() => load(join(e.name))}>📁 {e.name}</button> : <>📄 {e.name}</>}</td><td className="num ltr">{e.dir ? "" : e.size.toLocaleString()}</td><td>{when(e.mtime)}</td>
        <td><div className="row">{!e.dir && <button className="btn ghost sm" onClick={() => download(e.name)}>{t("files.download")}</button>}<button className="btn danger sm" onClick={() => del(e.name)}>{t("files.delete")}</button></div></td></tr>)}
      {ents && !shown.length && <tr><td colSpan={4}><Empty title={t("files.empty")} /></td></tr>}</tbody></table></div></div></div>;
}
