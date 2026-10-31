import { useEffect, useState } from "react";
import { api } from "./api";
import { DataTable, Err, Help, NoOrg, OrgBar, Pill, Tabs, isSenior, stl, useAct, useData, useMe, useOrgId, useOrgs, setOrg, when } from "./kit";
import { reg, t, useLang } from "./i18n";

reg({
  "cl.intro": { en: "A client is a company or person you support. Everything belongs to one client, and the technicians you create here can only ever see their own clients.", ar: "العميل هو شركة أو شخص تقدّم له الدعم. كل شيء يتبع عميلاً واحداً، والفنيون الذين تنشئهم هنا لا يرون إلا عملاءهم." },
  "cl.none": { en: "No clients yet. Add your first client below.", ar: "لا يوجد عملاء بعد. أضف أول عميل أدناه." }, "cl.newName": { en: "New client name", ar: "اسم العميل الجديد" }, "cl.add": { en: "Add client", ar: "إضافة عميل" },
  "cl.sites": { en: "Sites", ar: "المواقع" }, "cl.newSite": { en: "New site (office, branch, server room…)", ar: "موقع جديد (مكتب، فرع، غرفة خوادم…)" }, "cl.addSite": { en: "Add site", ar: "إضافة موقع" },
  "cl.computer": { en: "Computer", ar: "الحاسوب" }, "cl.kind": { en: "Type", ar: "النوع" }, "cl.state": { en: "State", ar: "الحالة" }, "cl.via": { en: "Connection", ar: "الاتصال" }, "cl.worker": { en: "Worker", ar: "عامل" }, "cl.nothing": { en: "Nothing here yet.", ar: "لا شيء هنا بعد." }, "cl.nosite": { en: "(no site)", ar: "(بدون موقع)" },
  "cl.loose": { en: "Not yet assigned to any client", ar: "غير مرتبط بأي عميل بعد" }, "cl.looseHint": { en: "Unassigned computers are visible to the owner only. Put each one under this client and a site.", ar: "الحواسيب غير المرتبطة يراها المالك فقط. اربط كل حاسوب بهذا العميل وبموقع." }, "cl.assign": { en: "Assign to this client", ar: "ربط بهذا العميل" }, "cl.device": { en: "Device", ar: "جهاز" },
  "cl.connect": { en: "Connect the next computer", ar: "ربط الحاسوب التالي" }, "cl.stepsWorker": { en: "Show the steps", ar: "عرض الخطوات" }, "cl.addWorker": { en: "Open “Add computer”", ar: "فتح «إضافة حاسوب»" },
  "onb.1": { en: "Get the customer's written permission to manage this computer (who agreed, and when).", ar: "احصل على إذن كتابي من العميل لإدارة هذا الحاسوب (من وافق ومتى)." },
  "onb.2": { en: "Copy cit-worker.exe to the computer and compare its SHA-256 with the value you recorded.", ar: "انسخ cit-worker.exe إلى الحاسوب وقارن قيمة SHA-256 الخاصة به بالقيمة التي سجلتها." },
  "onb.3": { en: "Make sure both computers reach each other privately: the same local network, or the same Tailscale/WireGuard network. Never open a port to the public Internet.", ar: "تأكد أن الحاسوبين يصل أحدهما للآخر بشكل خاص: نفس الشبكة المحلية أو نفس شبكة Tailscale/WireGuard. لا تفتح أي منفذ على الإنترنت العام." },
  "onb.4": { en: "On that computer run: cit-worker.exe run --bind <its private address>. It prints a fingerprint. Then run authorize and enroll to get a one-time 8-digit code.", ar: "على ذلك الحاسوب شغّل: cit-worker.exe run --bind <عنوانه الخاص>. سيطبع بصمة. ثم شغّل authorize ثم enroll للحصول على رمز من 8 أرقام لمرة واحدة." },
  "onb.5": { en: "In the dashboard open “Add computer”, type its address, compare the fingerprint group by group with its screen, then enter the code.", ar: "في لوحة التحكم افتح «إضافة حاسوب» واكتب عنوانه وقارن البصمة مجموعة مجموعة مع شاشته ثم أدخل الرمز." },
  "onb.6": { en: "Assign the new computer to a site. It is then visible only to technicians of this client.", ar: "اربط الحاسوب الجديد بموقع. عندها يراه فقط فنيو هذا العميل." },
  "ac.title": { en: "Add a computer", ar: "إضافة حاسوب" }, "ac.name": { en: "Name", ar: "الاسم" }, "ac.address": { en: "Address (host:8777)", ar: "العنوان (المضيف:8777)" }, "ac.fp": { en: "Fingerprint shown on the computer", ar: "البصمة المعروضة على الحاسوب" },
  "ac.register": { en: "Register", ar: "تسجيل" }, "ac.code": { en: "Enrollment code (8 digits)", ar: "رمز التسجيل (8 أرقام)" }, "ac.enroll": { en: "Enroll and connect", ar: "تسجيل واتصال" }, "ac.done": { en: "Connected. Assign it to a client above.", ar: "تم الاتصال. اربطه بعميل أعلاه." },
  "ac.hint": { en: "Public addresses are refused. Compare the fingerprint with the one printed on the computer before entering the code.", ar: "العناوين العامة مرفوضة. قارن البصمة بالمطبوعة على الحاسوب قبل إدخال الرمز." },
  "ac.persist": { en: "This computer cannot keep the credential in Windows-protected storage; it stays in memory only and you must enroll again after a restart.", ar: "لا يستطيع هذا الحاسوب حفظ بيانات الاعتماد في تخزين ويندوز المحمي؛ ستبقى في الذاكرة فقط وعليك التسجيل مجدداً بعد إعادة التشغيل." },
  "tm.invite": { en: "Invite a person", ar: "دعوة شخص" }, "tm.role": { en: "Role", ar: "الدور" }, "tm.note": { en: "Who is this for? (note)", ar: "لمن هذه الدعوة؟ (ملاحظة)" }, "tm.create": { en: "Create invitation", ar: "إنشاء دعوة" },
  "tm.roles": { en: "Administrator: manages this client's people, rules and approvals. Technician: runs jobs, cannot approve. Viewer: read-only. Customer: sees their own summary, tickets and bills.", ar: "المسؤول: يدير أشخاص العميل وقواعده وموافقاته. الفني: ينفّذ المهام ولا يوافق عليها. المشاهد: للقراءة فقط. العميل: يرى ملخصه وتذاكره وفواتيره." },
  "tm.secret": { en: "Give this to the person now — shown once, single use:", ar: "سلّم هذا للشخص الآن — يظهر مرة واحدة ويُستخدم مرة واحدة:" }, "tm.done": { en: "Done", ar: "تم" },
  "tm.people": { en: "People with access to this client", ar: "الأشخاص الذين لديهم وصول لهذا العميل" }, "tm.name": { en: "Name", ar: "الاسم" }, "tm.lastUsed": { en: "Last used", ar: "آخر استخدام" }, "tm.rotate": { en: "Rotate token", ar: "تدوير الرمز" }, "tm.revoke": { en: "Revoke", ar: "إلغاء الوصول" },
  "tm.invitations": { en: "Invitations", ar: "الدعوات" }, "tm.created": { en: "Created", ar: "تاريخ الإنشاء" }, "tm.used": { en: "used", ar: "مستخدمة" }, "tm.expired": { en: "expired or revoked", ar: "منتهية أو ملغاة" }, "tm.openInv": { en: "open", ar: "مفتوحة" }, "tm.onlyAdmin": { en: "Only owners and administrators manage people.", ar: "المالكون والمسؤولون فقط يديرون الأشخاص." },
  "role.administrator": { en: "Administrator", ar: "مسؤول" }, "role.technician": { en: "Technician", ar: "فني" }, "role.viewer": { en: "Viewer", ar: "مشاهد" }, "role.customer": { en: "Customer", ar: "عميل" }, "role.owner": { en: "Owner", ar: "مالك" },
  "bill.plan": { en: "Plan", ar: "الباقة" }, "bill.until": { en: "until {when}", ar: "حتى {when}" }, "bill.machines": { en: "Computers", ar: "الحواسيب" }, "bill.peak": { en: "peak this month {n}", ar: "الذروة هذا الشهر {n}" }, "bill.usage": { en: "Usage ({month})", ar: "الاستخدام ({month})" },
  "bill.usageLine": { en: "{jobs} jobs · {tokens} AI tokens", ar: "{jobs} مهمة · {tokens} رمز ذكاء اصطناعي" }, "bill.features": { en: "Includes: {list}", ar: "تشمل: {list}" }, "bill.owner": { en: "Owner actions", ar: "إجراءات المالك" }, "bill.change": { en: "Change plan (30 days)", ar: "تغيير الباقة (30 يوماً)" },
  "bill.issue": { en: "Issue license (365 days)", ar: "إصدار ترخيص (365 يوماً)" }, "bill.draft": { en: "Draft this month's invoice", ar: "إعداد فاتورة هذا الشهر" }, "bill.license": { en: "License (bound to this client, single use):", ar: "الترخيص (مرتبط بهذا العميل ويُستخدم مرة واحدة):" },
  "bill.paste": { en: "Paste a license to activate", ar: "الصق ترخيصاً لتفعيله" }, "bill.activate": { en: "Activate", ar: "تفعيل" }, "bill.noPay": { en: "No payment provider is connected: nothing is charged here. Invoices are drafts from list prices.", ar: "لا يوجد مزوّد دفع متصل: لا يتم خصم أي مبلغ هنا. الفواتير مسودات محسوبة من الأسعار المعلنة." },
  "bill.ownerOnly": { en: "Plan changes and invoices are handled by the service owner.", ar: "تغيير الباقات والفواتير يتولاها مالك الخدمة." }, "bill.invoices": { en: "Invoices (drafts)", ar: "الفواتير (مسودات)" }, "bill.period": { en: "Period", ar: "الفترة" }, "bill.total": { en: "Total", ar: "الإجمالي" }, "bill.status": { en: "Status", ar: "الحالة" },
  "bill.history": { en: "Subscription history", ar: "سجل الاشتراك" }, "bill.changed": { en: "Plan changed.", ar: "تم تغيير الباقة." }, "bill.drafted": { en: "Draft invoice saved.", ar: "تم حفظ مسودة الفاتورة." }, "bill.activated": { en: "License activated.", ar: "تم تفعيل الترخيص." }, "bill.who": { en: "By", ar: "بواسطة" }, "bill.what": { en: "Change", ar: "التغيير" }, "bill.when": { en: "When", ar: "الوقت" },
  "au.title": { en: "Audit trail for this client", ar: "سجل التدقيق لهذا العميل" }, "au.hint": { en: "A tamper-evident record limited to this client. It never contains secrets.", ar: "سجل يكشف أي تلاعب ومحدود بهذا العميل. لا يحتوي على أسرار أبداً." }, "au.when": { en: "When", ar: "الوقت" }, "au.who": { en: "Who", ar: "من" }, "au.action": { en: "Action", ar: "الإجراء" }, "au.detail": { en: "Detail", ar: "التفاصيل" },
  "set.title": { en: "Client settings", ar: "إعدادات العميل" }, "set.ai": { en: "AI assistant enabled for this client", ar: "تفعيل المساعد الذكي لهذا العميل" }, "set.cloud": { en: "Cloud AI is not available; only local models are used.", ar: "الذكاء الاصطناعي السحابي غير متاح؛ تُستخدم النماذج المحلية فقط." },
  "set.offboard": { en: "Offboard this client", ar: "إنهاء التعامل مع هذا العميل" }, "set.offboardHint": { en: "Revokes all access and Worker credentials, keeps the data for the retention period, then the owner can purge it. Type the exact client name.", ar: "يلغي كل الوصول وبيانات اعتماد الأجهزة ويحتفظ بالبيانات طوال فترة الاحتفاظ ثم يمكن للمالك حذفها نهائياً. اكتب اسم العميل بالضبط." },
  "set.exactName": { en: "Exact client name", ar: "اسم العميل بالضبط" }, "set.retention": { en: "Retention days", ar: "أيام الاحتفاظ" }, "set.offboardBtn": { en: "Offboard", ar: "إنهاء التعامل" }, "set.offboarded": { en: "Client offboarded. Computers that were offline must also run “cit-worker revoke-all”.", ar: "تم إنهاء التعامل مع العميل. الحواسيب التي كانت غير متصلة يجب أن تشغّل أيضاً «cit-worker revoke-all»." },
  "adm.team": { en: "People & roles", ar: "الأشخاص والصلاحيات" }, "adm.billing": { en: "Subscription", ar: "الاشتراك" }, "adm.audit": { en: "Audit log", ar: "سجل التدقيق" }, "adm.settings": { en: "Client settings", ar: "إعدادات العميل" }, "adm.addComputer": { en: "Add computer", ar: "إضافة حاسوب" },
  "adm.system": { en: "System", ar: "النظام" }, "adm.advanced": { en: "Advanced tools", ar: "أدوات متقدمة" }, "adm.advancedNote": { en: "Older technical tools (English only).", ar: "أدوات تقنية قديمة (بالإنجليزية فقط)." },
});

const ROLE = (r: string) => t("role." + r);

// ---------------------------------------------------------------- Clients & Sites
export function Clients({ goto }: { goto: (p: string) => void }) {
  useLang(); const { orgs, id: oid, reload } = useOrgs(); const fleet = useData<{ sites: any[] }>(oid ? `/orgs/${oid}/fleet` : null, 10000); const me = useMe(); const { err, go } = useAct();
  const [name, setName] = useState(""); const [site, setSite] = useState(""); const [steps, setSteps] = useState(false); const [loose, setLoose] = useState<{ devices: any[]; workers: any[] }>({ devices: [], workers: [] });
  const load = async () => { fleet.reload(); if (me?.role === "owner") { try { const [d, w] = await Promise.all([api<any[]>("/devices"), api<any[]>("/workers")]); setLoose({ devices: d.filter(x => !x.org_id && x.transport !== "local"), workers: w.filter(x => !x.org_id) }); } catch { /* owner only */ } } };
  useEffect(() => { void load(); }, [me?.role, oid]);
  const siteOpts = (fleet.data?.sites ?? []).filter(s => s.id); const senior = isSenior(me);
  return <><Err m={err} />
    <div className="card"><div className="tag">{t("cl.intro")}</div>
      <div className="row mt">{orgs.map(o => <button key={o.id} className={"btn " + (oid === o.id ? "" : "ghost")} onClick={() => setOrg(o.id)}>{o.name}</button>)}{!orgs.length && <span className="tag">{t("cl.none")}</span>}</div>
      {me?.role === "owner" && <div className="row mt"><input aria-label={t("cl.newName")} placeholder={t("cl.newName")} value={name} onChange={e => setName(e.target.value)} style={{ maxWidth: 320 }} /><button className="btn" disabled={!name} onClick={() => go(async () => { const o = await api<any>("/orgs", "POST", { name }); setName(""); setOrg(o.id); reload(); })}>{t("cl.add")}</button></div>}</div>
    {oid && <>
      {senior && <div className="card mt"><h3>{t("cl.sites")}</h3><div className="row"><input aria-label={t("cl.newSite")} placeholder={t("cl.newSite")} value={site} onChange={e => setSite(e.target.value)} style={{ maxWidth: 380 }} /><button className="btn" disabled={!site} onClick={() => go(async () => { await api(`/orgs/${oid}/sites`, "POST", { name: site }); setSite(""); fleet.reload(); })}>{t("cl.addSite")}</button></div></div>}
      {fleet.data?.sites.map(s => <div className="card mt" key={s.id ?? "none"}><h3>{s.id ? s.name : t("cl.nosite")}</h3><div className="tbl-wrap"><table><thead><tr><th>{t("cl.computer")}</th><th>{t("cl.kind")}</th><th>{t("cl.state")}</th><th>{t("cl.via")}</th></tr></thead><tbody>
        {s.workers.map((w: any) => <tr key={w.id}><td><b>{w.name}</b><div className="tag ltr">{w.address}</div></td><td>{t("cl.worker")}</td><td><Pill v={w.state ?? w.status} /></td><td>{stl(w.path)}</td></tr>)}
        {s.devices.map((d: any) => <tr key={d.id}><td><b>{d.name}</b><div className="tag ltr">{d.address}</div></td><td>{t("cl.device")}</td><td><Pill v={d.status} /></td><td>{stl(d.path)}</td></tr>)}
        {!s.workers.length && !s.devices.length && <tr><td colSpan={4} className="tag">{t("cl.nothing")}</td></tr>}</tbody></table></div></div>)}
      {(loose.devices.length > 0 || loose.workers.length > 0) && <div className="card mt"><h3>{t("cl.loose")}</h3><div className="tag">{t("cl.looseHint")}</div>
        {[...loose.workers.map(x => ({ ...x, k: "workers" as const })), ...loose.devices.map(x => ({ ...x, k: "devices" as const }))].map(x => <AssignRow key={x.id} x={x} sites={siteOpts} onAssign={sid => go(async () => { await api(`/${x.k}/${x.id}/assign`, "PUT", { org_id: oid, site_id: sid || null }); await load(); })} />)}</div>}
      <div className="card mt"><h3>{t("cl.connect")}</h3><div className="row"><button className="btn ghost" onClick={() => setSteps(!steps)}>{t("cl.stepsWorker")}</button><button className="btn" onClick={() => goto("add-computer")}>{t("cl.addWorker")}</button></div>
        {steps && <ol className="mt">{[1, 2, 3, 4, 5, 6].map(i => <li key={i}>{t("onb." + i)}</li>)}</ol>}</div></>}</>;
}
function AssignRow({ x, sites, onAssign }: { x: { name: string; address: string; k: string }; sites: any[]; onAssign: (site: string) => void }) {
  useLang(); const [s, setS] = useState("");
  return <div className="row mt"><span>{x.k === "workers" ? t("cl.worker") : t("cl.device")}: <b>{x.name}</b> <span className="tag ltr">{x.address}</span></span><select aria-label={t("cl.sites")} value={s} onChange={e => setS(e.target.value)} style={{ width: 200 }}><option value="">{t("cl.nosite")}</option>{sites.map(o => <option key={o.id} value={o.id}>{o.name}</option>)}</select><button className="btn" onClick={() => onAssign(s)}>{t("cl.assign")}</button></div>;
}

export function AddComputer() {
  useLang(); const { err, busy, go } = useAct(); const [f, setF] = useState({ name: "", address: "", fingerprint: "" }); const [code, setCode] = useState(""); const [id, setId] = useState(""); const [done, setDone] = useState(false); const [warn, setWarn] = useState("");
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  return <div className="card"><h3>{t("ac.title")}</h3><Err m={err} />{warn && <div className="banner">{warn}</div>}<ol>{[3, 4, 5].map(i => <li key={i}>{t("onb." + i)}</li>)}</ol><div className="tag">{t("ac.hint")}</div>
    {done ? <div className="banner mt">{t("ac.done")}</div> : !id ? <><div className="grid mt"><div><label>{t("ac.name")}</label><input value={f.name} onChange={set("name")} /></div><div><label>{t("ac.address")}</label><input className="ltr" value={f.address} onChange={set("address")} placeholder="192.168.1.50:8777" /></div></div>
      <label>{t("ac.fp")}</label><input className="ltr" value={f.fingerprint} onChange={set("fingerprint")} /><button className="btn mt" disabled={busy || !f.name || !f.address || !f.fingerprint} onClick={() => go(async () => { const w = await api<any>("/workers", "POST", f); setId(w.id); })}>{t("ac.register")}</button></>
      : <><div className="grid mt"><div><label>{t("ac.code")}</label><input className="ltr" value={code} maxLength={8} onChange={e => setCode(e.target.value)} /></div></div><button className="btn mt" disabled={busy || code.length !== 8} onClick={() => go(async () => { const r = await api<any>(`/workers/${id}/enroll`, "POST", { code }); if (!r.token_persisted) setWarn(t("ac.persist")); setDone(true); })}>{t("ac.enroll")}</button></>}</div>;
}

// ---------------------------------------------------------------- Administration
export function Team() {
  useLang(); const oid = useOrgId(); const p = useData<any[]>("/principals"); const inv = useData<any[]>(oid ? `/orgs/${oid}/invitations` : null); const me = useMe(); const { err, busy, go } = useAct();
  const [f, setF] = useState({ role: "technician", note: "" }); const [secret, setSecret] = useState(""); const mine = (p.data ?? []).filter(x => x.org_ids.includes(oid));
  if (!oid) return <NoOrg />; if (!isSenior(me)) return <div className="card empty">{t("tm.onlyAdmin")}</div>;
  return <><div className="card"><h3>{t("tm.invite")}</h3><Err m={err} /><div className="row"><select aria-label={t("tm.role")} style={{ width: 200 }} value={f.role} onChange={e => setF({ ...f, role: e.target.value })}>{["administrator", "technician", "viewer", "customer"].filter(r => r !== "administrator" || me?.role === "owner").map(r => <option key={r} value={r}>{ROLE(r)}</option>)}</select><input aria-label={t("tm.note")} placeholder={t("tm.note")} value={f.note} onChange={e => setF({ ...f, note: e.target.value })} style={{ maxWidth: 320 }} />
      <button className="btn" disabled={busy} onClick={() => go(async () => { const r = await api<any>(`/orgs/${oid}/invitations`, "POST", f); setSecret(r.token); inv.reload(); })}>{t("tm.create")}</button></div><div className="tag mt">{t("tm.roles")}</div>
    {secret && <div className="banner mt">{t("tm.secret")} <code>{secret}</code> <button className="btn ghost sm" onClick={() => setSecret("")}>{t("tm.done")}</button></div>}</div>
    <div className="card mt"><h3>{t("tm.people")}</h3><DataTable rows={p.data ? mine : null} loading={p.loading} error={p.err} pageSize={8} cols={[{ head: t("tm.name"), cell: x => x.name, text: x => x.name }, { head: t("tm.role"), cell: x => ROLE(x.role), text: x => ROLE(x.role) }, { head: t("tm.lastUsed"), cell: x => when(x.last_used) }, { head: t("cl.state"), cell: x => <Pill v={x.revoked ? "expired" : "active"} /> },
      { head: "", cell: x => !x.revoked && <div className="row"><button className="btn ghost sm" onClick={() => go(async () => { const r = await api<any>(`/principals/${x.id}/rotate`, "POST"); setSecret(r.token); })}>{t("tm.rotate")}</button><button className="btn danger sm" onClick={() => go(async () => { await api(`/principals/${x.id}`, "DELETE"); p.reload(); })}>{t("tm.revoke")}</button></div> }]} /></div>
    <div className="card mt"><h3>{t("tm.invitations")}</h3><DataTable rows={inv.data} loading={inv.loading} error={inv.err} pageSize={6} cols={[{ head: t("tm.role"), cell: x => ROLE(x.role) }, { head: t("tm.created"), cell: x => when(x.created_at) }, { head: t("cl.state"), cell: x => (x.used_at ? t("tm.used") : x.expires_at < Date.now() / 1000 ? t("tm.expired") : t("tm.openInv")) }, { head: "", cell: x => !x.used_at && <button className="btn ghost sm" onClick={() => go(async () => { await api(`/orgs/${oid}/invitations/${x.id}`, "DELETE"); inv.reload(); })}>{t("tm.revoke")}</button> }]} /></div></>;
}

export function Billing() {
  useLang(); const oid = useOrgId(); const s = useData<any>(oid ? `/orgs/${oid}/subscription` : null); const plans = useData<any>("/plans"); const h = useData<any[]>(oid ? `/orgs/${oid}/subscription/history` : null); const inv = useData<any[]>(oid ? `/orgs/${oid}/invoices` : null);
  const me = useMe(); const { err, busy, ok, go } = useAct(); const owner = me?.role === "owner"; const [plan, setPlan] = useState("professional"); const [lic, setLic] = useState(""); const u = s.data?.usage; const e = s.data?.entitlements;
  if (!oid) return <NoOrg />;
  return <><div className="grid"><div className="card"><h3>{t("bill.plan")}</h3><div className="big">{s.data?.plan ?? "—"}</div><Pill v={s.data?.status ?? ""} /><div className="tag">{t("bill.until", { when: when(s.data?.period_end) })}</div></div>
    <div className="card"><h3>{t("bill.machines")}</h3><div className="big ltr">{u?.devices_now ?? "—"} / {e?.max_devices ?? "—"}</div><div className="tag">{t("bill.peak", { n: u?.devices_peak ?? "—" })}</div></div>
    <div className="card"><h3>{t("bill.usage", { month: u?.month ?? "" })}</h3><div>{t("bill.usageLine", { jobs: u?.jobs ?? 0, tokens: u?.ai_tokens_month ?? 0 })}</div><div className="tag ltr">{t("bill.features", { list: (e?.features ?? []).join(", ") })}</div></div></div>
    <Err m={err || s.err} />{ok && <div className="tag">{ok}</div>}
    {owner ? <div className="card mt"><h3>{t("bill.owner")}</h3><div className="row"><select aria-label={t("bill.plan")} style={{ width: 180 }} value={plan} onChange={e => setPlan(e.target.value)}>{Object.keys(plans.data ?? {}).filter(x => x !== "trial").map(x => <option key={x}>{x}</option>)}</select>
      <button className="btn" disabled={busy} onClick={() => go(async () => { await api(`/orgs/${oid}/subscription`, "PUT", { plan }); s.reload(); h.reload(); }, t("bill.changed"))}>{t("bill.change")}</button>
      <button className="btn ghost" disabled={busy} onClick={() => go(async () => { const r = await api<any>(`/orgs/${oid}/subscription/license`, "POST", { plan, days: 365 }); setLic(r.license); })}>{t("bill.issue")}</button>
      <button className="btn ghost" disabled={busy} onClick={() => go(async () => { await api(`/orgs/${oid}/invoices`, "POST", {}); inv.reload(); }, t("bill.drafted"))}>{t("bill.draft")}</button></div>
      <div className="row mt"><input aria-label={t("bill.paste")} className="ltr" placeholder={t("bill.paste")} value={lic} onChange={e => setLic(e.target.value)} /><button className="btn" disabled={busy || lic.length < 20} onClick={() => go(async () => { await api(`/orgs/${oid}/subscription`, "POST", { license: lic }); s.reload(); h.reload(); }, t("bill.activated"))}>{t("bill.activate")}</button></div>
      <div className="tag mt">{t("bill.noPay")}</div></div> : <div className="card mt tag">{t("bill.ownerOnly")}</div>}
    <div className="grid mt"><div className="card"><h3>{t("bill.invoices")}</h3><DataTable rows={inv.data} loading={inv.loading} error={inv.err} pageSize={5} cols={[{ head: t("bill.period"), cell: x => x.period }, { head: t("bill.total"), num: true, cell: x => `${x.total} ${x.currency}` }, { head: t("bill.status"), cell: x => x.status }]} /></div>
      <div className="card"><h3>{t("bill.history")}</h3><DataTable rows={(h.data ?? []).map((x, i) => ({ id: String(i), ...x }))} loading={h.loading} error={h.err} pageSize={5} cols={[{ head: t("bill.when"), cell: x => when(x.ts) }, { head: t("bill.who"), cell: x => <span className="ltr">{x.actor}</span> }, { head: t("bill.what"), cell: x => <span className="ltr">{x.change}</span> }]} /></div></div></>;
}

export function Audit() {
  useLang(); const oid = useOrgId(); const a = useData<any[]>(oid ? `/orgs/${oid}/audit?limit=500` : null, 15000);
  if (!oid) return <NoOrg />;
  return <div className="card"><h3>{t("au.title")}</h3><div className="tag">{t("au.hint")}</div><DataTable rows={a.data ? a.data.map(x => ({ id: String(x.seq), ...x })) : null} loading={a.loading} error={a.err}
    cols={[{ head: t("au.when"), cell: x => when(x.ts) }, { head: t("au.who"), cell: x => <span className="ltr">{x.actor}</span>, text: x => x.actor }, { head: t("au.action"), cell: x => <b className="ltr">{x.action}</b>, text: x => x.action }, { head: t("au.detail"), cell: x => <code className="tag">{x.detail}</code>, text: x => x.detail }]} /></div>;
}

export function OrgSettings() {
  useLang(); const oid = useOrgId(); const s = useData<any>(oid ? `/orgs/${oid}/settings` : null); const me = useMe(); const { err, busy, ok, go } = useAct(); const [name, setName] = useState(""); const [days, setDays] = useState(30); const owner = me?.role === "owner";
  if (!oid) return <NoOrg />;
  return <><div className="card"><h3>{t("set.title")}</h3><Err m={err || s.err} />{ok && <div className="tag">{ok}</div>}
    <label className="row"><input type="checkbox" disabled={!isSenior(me)} checked={!!s.data?.ai_enabled} onChange={e => go(async () => { await api(`/orgs/${oid}/settings`, "PUT", { ...s.data, ai_enabled: e.target.checked, ai_cloud: false }); s.reload(); })} />{t("set.ai")}</label><div className="tag">{t("set.cloud")}</div></div>
    {owner && <div className="card mt"><h3>{t("set.offboard")}</h3><div className="tag">{t("set.offboardHint")}</div><div className="row mt"><input aria-label={t("set.exactName")} placeholder={t("set.exactName")} value={name} onChange={e => setName(e.target.value)} style={{ maxWidth: 300 }} /><input type="number" aria-label={t("set.retention")} title={t("set.retention")} style={{ width: 100 }} value={days} onChange={e => setDays(+e.target.value)} />
      <button className="btn danger" disabled={busy || !name} onClick={() => go(async () => { await api(`/orgs/${oid}/offboard`, "POST", { confirm_name: name, retention_days: days }); }, t("set.offboarded"))}>{t("set.offboardBtn")}</button></div></div>}</>;
}

void Help; void OrgBar; void Tabs;
