import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiBlob } from "./api";
import { DataTable, Empty, Err, NoOrg, OrgBar, Pill, Tabs, isSenior, useAct, useData, useMe, useOrgId, when } from "./kit";
import { reg, t, useLang } from "./i18n";
import { registerSection } from "./registry";
import { registerWorkspaceTab } from "./views1";

reg({
  "and.none": { en: "No Android devices found. Connect a phone by USB to the Main PC or to a connected Windows computer and turn on USB debugging.", ar: "لم يُعثر على أجهزة أندرويد. وصّل هاتفاً بكابل USB بالحاسوب الرئيسي أو بحاسوب ويندوز متصل وفعّل تصحيح USB." },
  "and.host": { en: "Connected to", ar: "متصل بـ" }, "and.model": { en: "Device", ar: "الجهاز" }, "and.serial": { en: "Serial", ar: "الرقم التسلسلي" }, "and.state": { en: "State", ar: "الحالة" }, "and.open": { en: "Open", ar: "فتح" },
  "and.unauthorized": { en: "Unauthorized — unlock the phone and tap “Allow USB debugging”", ar: "غير مصرّح — افتح قفل الهاتف واضغط «السماح بتصحيح USB»" }, "and.offline": { en: "Offline — reconnect the cable", ar: "غير متصل — أعد توصيل الكابل" },
  "and.device": { en: "Ready", ar: "جاهز" }, "and.not_connected": { en: "Not connected", ar: "غير موصول" },
  "and.unassigned": { en: "Phones on this PC not yet assigned to a client", ar: "هواتف على هذا الجهاز غير مرتبطة بعميل بعد" }, "and.assign": { en: "Assign to this client", ar: "ربط بهذا العميل" }, "and.scan": { en: "Scan USB phones on this PC", ar: "فحص هواتف USB على هذا الجهاز" },
  "and.back": { en: "All devices", ar: "كل الأجهزة" }, "and.error": { en: "Could not reach this computer: {e}", ar: "تعذر الوصول إلى هذا الحاسوب: {e}" },
  "and.tabScreen": { en: "Live screen", ar: "الشاشة المباشرة" }, "and.tabApps": { en: "Apps", ar: "التطبيقات" }, "and.tabShell": { en: "Shell", ar: "الصدفة" }, "and.tabLog": { en: "Logcat", ar: "سجل Logcat" }, "and.tabInfo": { en: "Info", ar: "المعلومات" }, "and.tabReq": { en: "Approvals", ar: "الموافقات" },
  "and.pause": { en: "Pause", ar: "إيقاف مؤقت" }, "and.resume": { en: "Resume", ar: "استئناف" }, "and.shot": { en: "Save screenshot", ar: "حفظ لقطة" }, "and.full": { en: "Full screen", ar: "ملء الشاشة" }, "and.fps": { en: "{ms} ms per frame", ar: "{ms} مللي ثانية لكل إطار" },
  "and.hint": { en: "Click to tap, drag to swipe. The picture refreshes about once a second (screenshots over ADB), so there is a short delay.", ar: "انقر للضغط واسحب للتمرير. تُحدَّث الصورة تقريباً كل ثانية (لقطات عبر ADB) لذا يوجد تأخير بسيط." },
  "and.sendText": { en: "Type on the phone", ar: "اكتب على الهاتف" }, "and.send": { en: "Send", ar: "إرسال" }, "and.textHint": { en: "Letters, digits and . , @ _ - + = : / ? ! # % only.", ar: "أحرف وأرقام و . , @ _ - + = : / ? ! # % فقط." },
  "and.key.HOME": { en: "Home", ar: "الرئيسية" }, "and.key.BACK": { en: "Back", ar: "رجوع" }, "and.key.APP_SWITCH": { en: "Recent apps", ar: "التطبيقات الأخيرة" }, "and.key.POWER": { en: "Power", ar: "الطاقة" }, "and.key.VOLUME_UP": { en: "Volume +", ar: "الصوت +" }, "and.key.VOLUME_DOWN": { en: "Volume −", ar: "الصوت −" },
  "and.apps": { en: "Installed apps (user)", ar: "التطبيقات المثبّتة (المستخدم)" }, "and.launch": { en: "Open", ar: "تشغيل" }, "and.stop": { en: "Stop", ar: "إيقاف" }, "and.uninstall": { en: "Request uninstall", ar: "طلب إزالة" }, "and.clear": { en: "Request clear data", ar: "طلب مسح البيانات" },
  "and.install": { en: "Install an APK (needs approval)", ar: "تثبيت ملف APK (يحتاج موافقة)" }, "and.pickApk": { en: "Choose APK…", ar: "اختر ملف APK…" }, "and.requested": { en: "Request sent — it waits for approval by another administrator.", ar: "تم إرسال الطلب — بانتظار موافقة مسؤول آخر." },
  "and.cmd": { en: "Read-only command, e.g. getprop ro.product.model", ar: "أمر للقراءة فقط مثل getprop ro.product.model" }, "and.run": { en: "Run", ar: "تشغيل" }, "and.shellNote": { en: "Only read-only commands run here. Anything else must be requested and approved.", ar: "تعمل هنا الأوامر للقراءة فقط. أي شيء آخر يجب طلبه والموافقة عليه." },
  "and.reqShell": { en: "Request approval for this command", ar: "طلب موافقة لهذا الأمر" }, "and.level": { en: "Level", ar: "المستوى" }, "and.lines": { en: "Lines", ar: "الأسطر" }, "and.refresh": { en: "Refresh", ar: "تحديث" }, "and.copy": { en: "Copy", ar: "نسخ" },
  "and.reboot": { en: "Request reboot", ar: "طلب إعادة التشغيل" }, "and.manu": { en: "Manufacturer", ar: "الشركة المصنّعة" }, "and.ver": { en: "Android version", ar: "إصدار أندرويد" }, "and.battery": { en: "Battery", ar: "البطارية" }, "and.screen": { en: "Screen", ar: "الشاشة" }, "and.density": { en: "Density", ar: "الكثافة" }, "and.build": { en: "Build", ar: "البناء" },
  "and.req.kind": { en: "Change", ar: "التغيير" }, "and.req.target": { en: "Target", ar: "الهدف" }, "and.req.by": { en: "Requested by", ar: "طلبها" }, "and.req.status": { en: "Status", ar: "الحالة" }, "and.req.result": { en: "Result", ar: "النتيجة" }, "and.req.none": { en: "No requests.", ar: "لا توجد طلبات." },
  "and.approve": { en: "Approve", ar: "موافقة" }, "and.reject": { en: "Reject", ar: "رفض" }, "and.phrase": { en: "type: I APPROVE", ar: "اكتب: I APPROVE" },
  "and.k.install": { en: "Install", ar: "تثبيت" }, "and.k.uninstall": { en: "Uninstall", ar: "إزالة" }, "and.k.clear": { en: "Clear data", ar: "مسح البيانات" }, "and.k.reboot": { en: "Reboot", ar: "إعادة تشغيل" }, "and.k.shell": { en: "Shell command", ar: "أمر صدفة" },
  "st.pending_approval2": { en: "Waiting for approval", ar: "بانتظار الموافقة" }, "st.rejected": { en: "Rejected", ar: "مرفوض" }, "st.done": { en: "Done", ar: "تم" },
  "ws.android": { en: "Android", ar: "أندرويد" },
});

type Dev = { id?: string; host: string; host_name: string; serial: string; name?: string; state?: string; model?: string; error?: string };
const STATE = (s?: string) => (s === "device" ? t("and.device") : s === "unauthorized" ? t("and.unauthorized") : s === "offline" ? t("and.offline") : t("and.not_connected"));

function Screen({ oid, d, canControl }: { oid: string; d: Dev; canControl: boolean }) {
  useLang(); const [src, setSrc] = useState(""); const [err, setErr] = useState(""); const [run, setRun] = useState(true); const [ms, setMs] = useState(0); const [full, setFull] = useState(false); const [text, setText] = useState(""); const img = useRef<HTMLImageElement>(null); const down = useRef<{ x: number; y: number; t: number } | null>(null); const blob = useRef<Blob | null>(null);
  const P = `/orgs/${oid}/android/${d.host}/${d.serial}`;
  useEffect(() => {
    let stop = false; let url = "";
    (async () => { while (!stop) { if (run) { const t0 = performance.now(); try { const b = await apiBlob(`${P}/screen?w=${full ? 900 : 540}`); blob.current = b; const u = URL.createObjectURL(b); if (url) URL.revokeObjectURL(url); url = u; setSrc(u); setMs(Math.round(performance.now() - t0)); setErr(""); } catch (e: any) { setErr(e.message); await new Promise(r => setTimeout(r, 2500)); } } await new Promise(r => setTimeout(r, run ? 150 : 400)); } })();
    return () => { stop = true; if (url) URL.revokeObjectURL(url); };
  }, [P, run, full]);
  const frac = (e: React.PointerEvent) => { const r = img.current!.getBoundingClientRect(); return { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) }; };
  const send = (ev: any) => api(`${P}/input`, "POST", ev).catch((e: any) => setErr(e.message));
  const keys = ["HOME", "BACK", "APP_SWITCH", "VOLUME_UP", "VOLUME_DOWN", "POWER"];
  return <div className={full ? "" : ""}><Err m={err} /><div className="row" style={{ marginBottom: 8 }}><button className="btn ghost sm" onClick={() => setRun(!run)}>{run ? t("and.pause") : t("and.resume")}</button>
    <button className="btn ghost sm" disabled={!blob.current} onClick={() => { if (!blob.current) return; const u = URL.createObjectURL(blob.current); const a = document.createElement("a"); a.href = u; a.download = `android-${d.serial}.jpg`; a.click(); URL.revokeObjectURL(u); }}>{t("and.shot")}</button>
    <button className="btn ghost sm" onClick={() => setFull(!full)}>{t("and.full")}</button><span className="tag ltr">{t("and.fps", { ms })}</span></div>
    <div className={"screen" + (full ? " full" : "")} style={full ? {} : { minHeight: 380 }}>{run && <span className="live-badge">LIVE</span>}
      {src ? <img ref={img} src={src} alt="Android screen" draggable={false} style={{ maxHeight: full ? "100vh" : 640, cursor: canControl ? "pointer" : "default" }}
        onPointerDown={e => { if (canControl) down.current = { ...frac(e), t: Date.now() }; }}
        onPointerUp={e => { const s = down.current; down.current = null; if (!s || !canControl) return; const f = frac(e); const moved = Math.hypot(f.x - s.x, f.y - s.y) > 0.02; void send(moved ? { kind: "swipe", x: s.x, y: s.y, x2: f.x, y2: f.y, ms: Math.min(1200, Date.now() - s.t + 80) } : { kind: "tap", x: f.x, y: f.y }); }} /> : <Empty title={t("kit.loading")} />}
      {full && <button className="btn danger sm" style={{ position: "absolute", top: 8, insetInlineEnd: 8 }} onClick={() => setFull(false)}>✕</button>}</div>
    {canControl && <><div className="row mt">{keys.map(k => <button key={k} className="btn ghost sm" onClick={() => send({ kind: "key", key: k })}>{t("and.key." + k)}</button>)}</div>
      <div className="row mt"><input className="ltr grow" aria-label={t("and.sendText")} placeholder={t("and.sendText")} value={text} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && text) { void send({ kind: "text", text }); setText(""); } }} /><button className="btn sm" disabled={!text} onClick={() => { void send({ kind: "text", text }); setText(""); }}>{t("and.send")}</button><span className="tag">{t("and.textHint")}</span></div></>}
    <div className="tag mt">{t("and.hint")}</div></div>;
}

function Apps({ oid, d }: { oid: string; d: Dev }) {
  useLang(); const P = `/orgs/${oid}/android/${d.host}/${d.serial}`; const pk = useData<string[]>(`${P}/packages`); const { err, busy, ok, go } = useAct(); const f = useRef<HTMLInputElement>(null);
  const req = (kind: string, arg: string, apk?: string) => go(() => api(`${P}/requests`, "POST", { kind, arg, apk_b64: apk }), t("and.requested"));
  const upload = (file: File) => { const r = new FileReader(); r.onload = () => req("install", file.name, String(r.result).split(",")[1]); r.readAsDataURL(file); };
  return <div><Err m={err || pk.err} />{ok && <div className="banner">{ok}</div>}<div className="row"><input ref={f} type="file" accept=".apk" hidden onChange={e => { const x = e.target.files?.[0]; if (x) upload(x); e.target.value = ""; }} /><button className="btn" disabled={busy} onClick={() => f.current?.click()}>{t("and.pickApk")}</button><span className="tag">{t("and.install")}</span></div>
    <h3 className="mt">{t("and.apps")}</h3><DataTable rows={pk.data ? pk.data.map(p => ({ id: p, p })) : null} loading={pk.loading} pageSize={8} cols={[{ head: "Package", cell: r => <span className="ltr">{r.p}</span>, text: r => r.p },
      { head: "", cell: r => <div className="row"><button className="btn ghost sm" onClick={() => go(() => api(`${P}/app`, "POST", { action: "launch", package: r.p }))}>{t("and.launch")}</button><button className="btn ghost sm" onClick={() => go(() => api(`${P}/app`, "POST", { action: "stop", package: r.p }))}>{t("and.stop")}</button>
        <button className="btn ghost sm" onClick={() => req("clear", r.p)}>{t("and.clear")}</button><button className="btn danger sm" onClick={() => req("uninstall", r.p)}>{t("and.uninstall")}</button></div> }]} /></div>;
}

function Shell({ oid, d }: { oid: string; d: Dev }) {
  useLang(); const P = `/orgs/${oid}/android/${d.host}/${d.serial}`; const [cmd, setCmd] = useState(""); const [out, setOut] = useState(""); const { err, busy, ok, go } = useAct();
  return <div><div className="tag">{t("and.shellNote")}</div><Err m={err} />{ok && <div className="banner mt">{ok}</div>}<div className="row mt"><input className="ltr grow" aria-label={t("and.cmd")} placeholder={t("and.cmd")} value={cmd} onChange={e => setCmd(e.target.value)} onKeyDown={e => e.key === "Enter" && cmd && go(async () => setOut((await api<any>(`${P}/shell`, "POST", { cmd })).output))} />
    <button className="btn" disabled={busy || !cmd} onClick={() => go(async () => setOut((await api<any>(`${P}/shell`, "POST", { cmd })).output))}>{t("and.run")}</button><button className="btn ghost" disabled={busy || !cmd} onClick={() => go(() => api(`${P}/requests`, "POST", { kind: "shell", arg: cmd }), t("and.requested"))}>{t("and.reqShell")}</button></div>{out && <pre className="mt">{out}</pre>}</div>;
}

function Logcat({ oid, d }: { oid: string; d: Dev }) {
  useLang(); const P = `/orgs/${oid}/android/${d.host}/${d.serial}`; const [level, setLevel] = useState("W"); const [lines, setLines] = useState(300); const [n, setN] = useState(0); const l = useData<{ output: string }>(`${P}/logcat?lines=${lines}&level=${level}&_=${n}`);
  return <div><div className="row"><label style={{ margin: 0 }}>{t("and.level")}</label><select style={{ width: 90 }} value={level} onChange={e => setLevel(e.target.value)}>{["V", "D", "I", "W", "E", "F"].map(x => <option key={x}>{x}</option>)}</select><label style={{ margin: 0 }}>{t("and.lines")}</label><input type="number" style={{ width: 100 }} min={10} max={2000} value={lines} onChange={e => setLines(+e.target.value)} />
    <button className="btn ghost sm" onClick={() => setN(n + 1)}>{t("and.refresh")}</button><button className="btn ghost sm" onClick={() => navigator.clipboard?.writeText(l.data?.output ?? "")}>{t("and.copy")}</button></div><Err m={l.err} /><pre className="mt" style={{ maxHeight: 460 }}>{l.data?.output || (l.loading ? t("kit.loading") : "")}</pre></div>;
}

function Info({ oid, d }: { oid: string; d: Dev }) {
  useLang(); const P = `/orgs/${oid}/android/${d.host}/${d.serial}`; const i = useData<any>(`${P}/info`); const { err, busy, ok, go } = useAct(); const x = i.data;
  return <div><Err m={i.err || err} />{ok && <div className="banner">{ok}</div>}{x && <div className="grid"><div><div className="tag">{t("and.manu")}</div><b>{x.manufacturer}</b></div><div><div className="tag">{t("and.model")}</div><b>{x.model}</b></div><div><div className="tag">{t("and.ver")}</div><b className="ltr">{x.android} (SDK {x.sdk})</b></div>
    <div><div className="tag">{t("and.serial")}</div><b className="ltr">{x.hardware_serial || x.serial}</b></div><div><div className="tag">{t("and.battery")}</div><b className="ltr">{x.battery.level}%</b></div><div><div className="tag">{t("and.screen")}</div><b className="ltr">{x.screen.join(" × ")}</b></div><div><div className="tag">{t("and.density")}</div><b className="ltr">{x.density}</b></div></div>}
    {x && <div className="tag mt ltr">{t("and.build")}: {x.build}</div>}<div className="mt"><button className="btn danger" disabled={busy} onClick={() => go(() => api(`${P}/requests`, "POST", { kind: "reboot" }), t("and.requested"))}>{t("and.reboot")}</button></div></div>;
}

function Requests({ oid }: { oid: string }) {
  useLang(); const r = useData<any[]>(`/orgs/${oid}/android/requests`, 5000); const me = useMe(); const { err, go } = useAct(); const [ph, setPh] = useState<Record<string, string>>({});
  const act = (id: string, a: string, body: any = {}) => go(async () => { await api(`/orgs/${oid}/android/requests/${id}/${a}`, "POST", body); r.reload(); });
  return <div><Err m={err} /><DataTable rows={r.data} loading={r.loading} error={r.err} empty={t("and.req.none")} pageSize={8} cols={[{ head: t("and.req.kind"), cell: x => t("and.k." + x.kind), text: x => x.kind }, { head: t("and.req.target"), cell: x => <span className="ltr">{x.serial} {x.arg}</span> }, { head: t("and.req.by"), cell: x => <span className="ltr">{x.created_by}</span> },
    { head: t("and.req.status"), cell: x => <Pill v={x.status} />, text: x => x.status }, { head: t("and.req.result"), cell: x => <span className="tag ltr">{(x.result ?? "").slice(0, 80)}</span> },
    { head: "", cell: x => x.status === "pending_approval" && isSenior(me) && <div className="row">{x.kind === "reboot" && <input aria-label={t("and.phrase")} placeholder={t("and.phrase")} style={{ width: 140 }} value={ph[x.id] ?? ""} onChange={e => setPh({ ...ph, [x.id]: e.target.value })} />}<button className="btn sm" onClick={() => act(x.id, "approve", { confirm: ph[x.id] })}>{t("and.approve")}</button><button className="btn ghost sm" onClick={() => act(x.id, "reject")}>{t("and.reject")}</button></div> }]} /></div>;
}

export function AndroidDevices({ oid, hostFilter }: { oid: string; hostFilter?: string }) {
  useLang(); const l = useData<Dev[]>(`/orgs/${oid}/android/devices`, 12000); const me = useMe(); const [sel, setSel] = useState<Dev | null>(null); const [tab, setTab] = useState("screen"); const { err, go } = useAct(); const [loose, setLoose] = useState<any[] | null>(null);
  const canControl = me?.role !== "viewer" && me?.role !== "customer"; const rows = (l.data ?? []).filter(d => !hostFilter || d.host === hostFilter).map(d => ({ ...d, id: d.host + "/" + d.serial })); const bad = rows.filter(d => d.error); const good = rows.filter(d => !d.error);
  if (sel) return <div className="fade"><div className="row" style={{ marginBottom: 10 }}><button className="btn ghost sm" onClick={() => setSel(null)}><span className="flip">‹</span> {t("and.back")}</button><b>{sel.name ?? sel.model ?? sel.serial}</b><Pill v={sel.state === "device" ? "online" : "offline"} /><span className="tag ltr">{sel.serial} · {sel.host_name}</span></div>
    <Tabs tabs={[["screen", t("and.tabScreen")], ["apps", t("and.tabApps")], ["shell", t("and.tabShell")], ["log", t("and.tabLog")], ["info", t("and.tabInfo")], ["req", t("and.tabReq")]]} value={tab} onChange={setTab} />
    <div className="card">{tab === "screen" && <Screen oid={oid} d={sel} canControl={canControl} />}{tab === "apps" && <Apps oid={oid} d={sel} />}{tab === "shell" && <Shell oid={oid} d={sel} />}{tab === "log" && <Logcat oid={oid} d={sel} />}{tab === "info" && <Info oid={oid} d={sel} />}{tab === "req" && <Requests oid={oid} />}</div></div>;
  return <><Err m={err || l.err} />{bad.map(b => <div key={b.host} className="banner">{b.host_name}: {t("and.error", { e: b.error ?? "" })}</div>)}
    <div className="card"><DataTable rows={l.data ? good : null} loading={l.loading} empty={t("and.none")} onRow={d => { if (d.state === "device") { setSel(d); setTab("screen"); } }} cols={[{ head: t("and.model"), cell: d => <b>{d.name ?? d.model ?? d.serial}</b>, text: d => `${d.name} ${d.model}` }, { head: t("and.host"), cell: d => d.host_name, text: d => d.host_name }, { head: t("and.serial"), cell: d => <span className="ltr">{d.serial}</span>, text: d => d.serial },
      { head: t("and.state"), cell: d => <span className={"pill " + (d.state === "device" ? "ok" : "warn")}>{STATE(d.state)}</span> }, { head: "", cell: d => d.state === "device" && <button className="btn sm" onClick={() => { setSel(d); setTab("screen"); }}>{t("and.open")}</button> }]} /></div>
    {me?.role === "owner" && !hostFilter && <div className="card mt"><div className="row"><h3 style={{ flex: 1, margin: 0 }}>{t("and.unassigned")}</h3><button className="btn ghost sm" onClick={() => go(async () => setLoose(await api<any[]>("/android/unassigned")))}>{t("and.scan")}</button></div>
      {(loose ?? []).map(p => <div key={p.serial} className="row mt"><b className="ltr">{p.model ?? p.serial}</b><span className="tag ltr">{p.serial}</span><span className="tag">{STATE(p.state)}</span><button className="btn sm" onClick={() => go(async () => { await api(`/orgs/${oid}/android/assign`, "PUT", { serial: p.serial, name: p.model ?? p.serial }); setLoose((loose ?? []).filter(x => x.serial !== p.serial)); l.reload(); })}>{t("and.assign")}</button></div>)}</div>}</>;
}

function AndroidPage() {
  useLang(); const oid = useOrgId(); return <><OrgBar />{oid ? <AndroidDevices oid={oid} /> : <NoOrg />}</>;
}
registerSection({ key: "android", icon: "android", order: 40, label: "nav.android", render: () => <AndroidPage /> });
registerWorkspaceTab({ key: "android", label: "Android", node: ({ wid, oid }) => <AndroidDevices oid={oid} hostFilter={wid} /> });
void when; void useCallback;
