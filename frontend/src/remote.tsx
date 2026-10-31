import { useEffect, useState } from "react";
import { api } from "./api";
import { DataTable, Empty, Err, NoOrg, OrgBar, Pill, useAct, useData, useMe, useOrgId, when } from "./kit";
import { reg, t, useLang } from "./i18n";
import { registerSection } from "./registry";
import { registerWorkspaceTab } from "./views1";

reg({
  "rc.title": { en: "Live Remote Control", ar: "التحكم المباشر عن بُعد" }, "rc.pick": { en: "Computer", ar: "الحاسوب" }, "rc.none": { en: "No computers yet. Connect one in “Clients & Sites”.", ar: "لا توجد حواسيب بعد. اربط واحداً من «العملاء والمواقع»." },
  "rc.how": { en: "This opens the live desktop of the selected computer in the RustDesk viewer on this PC (a separate window, not inside the browser). You can use the mouse and keyboard, open programs, browse and use File Explorer there. The person at that computer sees RustDesk's own prompt and indicator.", ar: "يفتح هذا سطح المكتب المباشر للحاسوب المحدد في عارض RustDesk على هذا الجهاز (نافذة منفصلة وليس داخل المتصفح). يمكنك استخدام الفأرة ولوحة المفاتيح وتشغيل البرامج وتصفح الإنترنت واستخدام مستكشف الملفات. يرى الشخص عند ذلك الحاسوب نافذة التأكيد والمؤشر الخاصين بـ RustDesk." },
  "rc.id": { en: "RustDesk ID of this computer", ar: "معرّف RustDesk لهذا الحاسوب" }, "rc.idHint": { en: "Shown in the RustDesk window on that computer.", ar: "يظهر في نافذة RustDesk على ذلك الحاسوب." }, "rc.saveId": { en: "Save ID", ar: "حفظ المعرّف" }, "rc.saved": { en: "Saved.", ar: "تم الحفظ." },
  "rc.minutes": { en: "Session length (minutes)", ar: "مدة الجلسة (بالدقائق)" }, "rc.start": { en: "Start live session", ar: "بدء جلسة مباشرة" }, "rc.starting": { en: "Starting…", ar: "جارٍ البدء…" }, "rc.end": { en: "End session", ar: "إنهاء الجلسة" },
  "rc.active": { en: "Active session", ar: "جلسة نشطة" }, "rc.operator": { en: "Operator", ar: "المشغّل" }, "rc.started": { en: "Started", ar: "بدأت" }, "rc.expires": { en: "Ends automatically at", ar: "تنتهي تلقائياً في" }, "rc.status": { en: "Status", ar: "الحالة" },
  "rc.notControllable": { en: "CIT could not find the viewer window to close it automatically. Close the RustDesk window yourself to disconnect.", ar: "لم يستطع CIT العثور على نافذة العارض لإغلاقها تلقائياً. أغلق نافذة RustDesk بنفسك لقطع الاتصال." },
  "rc.history": { en: "Session history", ar: "سجل الجلسات" }, "rc.noHistory": { en: "No sessions yet.", ar: "لا توجد جلسات بعد." },
  "rc.security": { en: "Security: the other computer must accept the connection. Unattended access works only if its owner set a permanent password in RustDesk; CIT never sets or sends one. Windows sign-in, the lock screen and the UAC prompt are not bypassed. “Emergency stop” closes every live session.", ar: "الأمان: يجب أن يقبل الحاسوب الآخر الاتصال. الوصول بلا حضور يعمل فقط إذا وضع مالكه كلمة مرور دائمة في RustDesk؛ لا يضبط CIT واحدة ولا يرسلها. لا يتم تجاوز تسجيل دخول ويندوز ولا شاشة القفل ولا نافذة UAC. «الإيقاف الطارئ» يغلق كل الجلسات المباشرة." },
  "rc.notInstalled": { en: "RustDesk is not installed on this PC. Install it from rustdesk.com.", ar: "RustDesk غير مثبّت على هذا الجهاز. ثبّته من rustdesk.com." },
  "st.expired_session": { en: "Expired", ar: "انتهت المدة" }, "st.ended": { en: "Ended", ar: "انتهت" }, "st.emergency_stop": { en: "Emergency stop", ar: "إيقاف طارئ" },
});

export function RemoteControl({ wid, oid }: { wid: string; oid: string }) {
  useLang(); const me = useMe(); const w = useData<any>(`/workers/${wid}`); const ss = useData<any[]>(`/workers/${wid}/remote/sessions`, 5000); const st = useData<any>("/remote/status");
  const { err, busy, ok, go } = useAct(); const [id, setId] = useState(""); const [minutes, setMinutes] = useState(30); const [, tick] = useState(0);
  useEffect(() => { if (w.data) setId(w.data.rustdesk_id ?? ""); }, [w.data?.rustdesk_id]);
  useEffect(() => { const i = setInterval(() => tick(x => x + 1), 1000); return () => clearInterval(i); }, []);
  const active = (ss.data ?? []).find(s => s.status === "active"); const left = active ? Math.max(0, Math.round(active.expires_at - Date.now() / 1000)) : 0; const canControl = me?.role !== "viewer" && me?.role !== "customer";
  return <><Err m={err || w.err} />{st.data && !st.data.installed && <div className="banner">{t("rc.notInstalled")}</div>}
    <div className="card"><div className="tag">{t("rc.how")}</div>
      <div className="row mt"><div style={{ minWidth: 220 }}><label>{t("rc.id")}</label><input className="ltr" value={id} inputMode="numeric" onChange={e => setId(e.target.value.trim())} placeholder="123456789" /></div>
        <button className="btn ghost" disabled={busy || !id || id === (w.data?.rustdesk_id ?? "")} onClick={() => go(async () => { await api(`/workers/${wid}`, "PATCH", { rustdesk_id: id }); w.reload(); }, t("rc.saved"))}>{t("rc.saveId")}</button>
        <div style={{ width: 150 }}><label>{t("rc.minutes")}</label><input type="number" min={1} max={240} value={minutes} onChange={e => setMinutes(+e.target.value)} /></div>
        <button className="btn" disabled={busy || !!active || !w.data?.rustdesk_id || !canControl || !oid} onClick={() => go(async () => { await api(`/workers/${wid}/remote/sessions`, "POST", { minutes }); ss.reload(); })}>{busy ? t("rc.starting") : t("rc.start")}</button>{ok && <span className="tag">{ok}</span>}</div>
      <div className="tag mt">{t("rc.idHint")}</div><div className="banner mt">{t("rc.security")}</div></div>
    {active && <div className="card mt fade"><div className="row"><h3 style={{ flex: 1, margin: 0 }}><span className="dot bad" />{t("rc.active")}</h3><b className="ltr">{Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")}</b>
      <button className="btn danger" onClick={() => go(async () => { await api(`/workers/${wid}/remote/sessions/${active.id}/end`, "POST"); ss.reload(); })}>{t("rc.end")}</button></div>
      <div className="grid mt"><div><div className="tag">{t("rc.operator")}</div><b className="ltr">{active.operator}</b></div><div><div className="tag">{t("rc.started")}</div><b>{when(active.started_at)}</b></div><div><div className="tag">{t("rc.expires")}</div><b>{when(active.expires_at)}</b></div></div>
      {!active.controllable && <div className="banner mt">{t("rc.notControllable")}</div>}</div>}
    <div className="card mt"><h3>{t("rc.history")}</h3><DataTable rows={ss.data} loading={ss.loading} error={ss.err} pageSize={6} empty={t("rc.noHistory")} cols={[{ head: t("rc.started"), cell: s => when(s.started_at) }, { head: t("rc.operator"), cell: s => <span className="ltr">{s.operator}</span>, text: s => s.operator }, { head: t("rc.status"), cell: s => <Pill v={s.status === "expired" ? "expired" : s.status} />, text: s => s.status }]} /></div></>;
}

function RemotePage() {
  useLang(); const oid = useOrgId(); const fleet = useData<{ sites: any[] }>(oid ? `/orgs/${oid}/fleet` : null, 10000); const [wid, setWid] = useState("");
  const ws: any[] = (fleet.data?.sites ?? []).flatMap(s => s.workers); useEffect(() => { if (!wid && ws[0]) setWid(ws[0].id); }, [ws.length]);
  return <><OrgBar />{!oid ? <NoOrg /> : !ws.length ? <div className="card"><Empty title={t("rc.none")} /></div> : <>
    <div className="row" style={{ marginBottom: 12 }}><label style={{ margin: 0 }}>{t("rc.pick")}</label><select aria-label={t("rc.pick")} style={{ width: 280 }} value={wid} onChange={e => setWid(e.target.value)}>{ws.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</select></div>
    {wid && <RemoteControl wid={wid} oid={oid} />}</>}</>;
}

registerSection({ key: "remote", icon: "remote", order: 30, label: "nav.remote", render: () => <RemotePage /> });
registerWorkspaceTab({ key: "live", label: "Live Screen", node: ({ wid, oid }) => <RemoteControl wid={wid} oid={oid} /> });
