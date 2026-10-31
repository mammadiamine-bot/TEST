import { ReactNode, useEffect, useRef, useState } from "react";
import { api } from "./api";
import { DataTable, Empty, Err, isSenior, useAct, useData, useMe, when } from "./kit";
import { getLang, reg, t, useLang } from "./i18n";
import { Markdown, aiTabs } from "./chat";

reg({
  "ag.tabTech": { en: "AI Technician", ar: "الفني الذكي" }, "ag.tabKb": { en: "Knowledge", ar: "المعرفة" }, "ag.tabModels": { en: "Models", ar: "النماذج" }, "ag.tabPolicy": { en: "Autonomy & safety", ar: "الاستقلالية والأمان" },
  "ag.target": { en: "Device", ar: "الجهاز" }, "ag.host": { en: "AI runs on", ar: "يعمل الذكاء على" }, "ag.model": { en: "Model", ar: "النموذج" }, "ag.noModel": { en: "no model (built-in checks only)", ar: "بدون نموذج (فحوصات مدمجة فقط)" },
  "ag.ask": { en: "Describe the problem", ar: "صف المشكلة" }, "ag.placeholder": { en: "e.g. My PC is slow. Find the cause and fix what is safe to fix.", ar: "مثال: حاسوبي بطيء. اعثر على السبب وأصلح ما يمكن إصلاحه بأمان." },
  "ag.start": { en: "Start troubleshooting", ar: "ابدأ التشخيص" }, "ag.cancel": { en: "Stop", ar: "إيقاف" }, "ag.new": { en: "New", ar: "جديد" }, "ag.history": { en: "Incidents", ar: "الحوادث" },
  "ag.levelIs": { en: "Autonomy: level {n} — {name}", ar: "الاستقلالية: المستوى {n} — {name}" }, "ag.lvl.0": { en: "observe", ar: "مراقبة" }, "ag.lvl.1": { en: "diagnose", ar: "تشخيص" }, "ag.lvl.2": { en: "safe auto-repair", ar: "إصلاح آمن تلقائي" }, "ag.lvl.3": { en: "approved repair", ar: "إصلاح بعد الموافقة" }, "ag.lvl.4": { en: "maintenance workflow", ar: "مسار صيانة" },
  "ag.halted": { en: "Emergency stop is active. The AI technician cannot start work until the owner resumes it (Autonomy & safety).", ar: "الإيقاف الطارئ مفعّل. لا يستطيع الفني الذكي العمل حتى يستأنفه المالك (الاستقلالية والأمان)." },
  "ag.ex1": { en: "My computer is very slow. Find the cause and fix it.", ar: "حاسوبي بطيء جداً. اعثر على السبب وأصلحه." }, "ag.ex2": { en: "Check why the internet disconnects", ar: "افحص سبب انقطاع الإنترنت" }, "ag.ex3": { en: "Find the application consuming too much RAM", ar: "اعثر على التطبيق الذي يستهلك الكثير من الذاكرة" },
  "ag.ex4": { en: "Repair common Windows Update errors", ar: "أصلح أخطاء تحديث ويندوز الشائعة" }, "ag.ex5": { en: "Check CIT Worker and reconnect it", ar: "افحص وكيل CIT وأعد توصيله" }, "ag.ex6": { en: "Why is my Android device not detected?", ar: "لماذا لا يظهر جهاز الأندرويد؟" },
  "ag.state.healthy": { en: "Healthy", ar: "سليم" }, "ag.state.problem_detected": { en: "Problem detected", ar: "تم اكتشاف مشكلة" }, "ag.state.inconclusive": { en: "Inconclusive", ar: "غير حاسم" }, "ag.state.check_failed": { en: "Check failed", ar: "فشل الفحص" }, "ag.state.unreachable": { en: "Device unreachable", ar: "الجهاز غير متاح" }, "ag.state.permission_required": { en: "Permission required", ar: "يلزم إذن" },
  "ag.status.running": { en: "working", ar: "قيد العمل" }, "ag.status.verifying": { en: "verifying", ar: "قيد التحقق" }, "ag.status.awaiting_approval": { en: "waiting for approval", ar: "بانتظار الموافقة" }, "ag.status.completed": { en: "finished", ar: "انتهى" }, "ag.status.cancelled": { en: "stopped", ar: "أُوقف" }, "ag.status.failed": { en: "error", ar: "خطأ" },
  "ag.res.resolved": { en: "Resolved and verified", ar: "حُلّت مع التحقق" }, "ag.res.partially_resolved": { en: "Partly resolved", ar: "حُلّت جزئياً" }, "ag.res.unresolved": { en: "Not resolved", ar: "لم تُحل" }, "ag.res.none": { en: "No repair was run", ar: "لم يُنفَّذ إصلاح" },
  "ag.now": { en: "Now: {x}", ar: "الآن: {x}" }, "ag.timeline": { en: "Live activity", ar: "النشاط المباشر" }, "ag.causes": { en: "Likely causes", ar: "الأسباب المحتملة" }, "ag.noCause": { en: "No cause strong enough to explain the problem was found.", ar: "لم يُعثر على سبب قوي بما يكفي لتفسير المشكلة." },
  "ag.conf": { en: "confidence {n}%", ar: "الثقة {n}%" }, "ag.seen": { en: "worked {n}× before here", ar: "نجح {n} مرة سابقاً هنا" }, "ag.findings": { en: "Findings and evidence", ar: "النتائج والأدلة" }, "ag.checks": { en: "Checks that ran", ar: "الفحوصات التي أُجريت" },
  "ag.tool": { en: "Check", ar: "الفحص" }, "ag.result": { en: "Result", ar: "النتيجة" }, "ag.ms": { en: "Time", ar: "الزمن" }, "ag.required": { en: "needed for a verdict", ar: "لازم للحكم" },
  "ag.repairs": { en: "Repair options", ar: "خيارات الإصلاح" }, "ag.noRepairs": { en: "No reviewed repair applies to these findings.", ar: "لا يوجد إصلاح معتمد ينطبق على هذه النتائج." }, "ag.cls.safe": { en: "safe", ar: "آمن" }, "ag.cls.approval": { en: "needs approval", ar: "يحتاج موافقة" }, "ag.cls.disruptive": { en: "disruptive", ar: "مُعطِّل" },
  "ag.dec.auto": { en: "ran automatically", ar: "نُفِّذ تلقائياً" }, "ag.dec.request": { en: "approval requested", ar: "طُلبت الموافقة" }, "ag.dec.propose": { en: "proposed", ar: "مقترح" }, "ag.dec.observe": { en: "not allowed (observe-only)", ar: "غير مسموح (مراقبة فقط)" }, "ag.dec.blocked": { en: "blocked", ar: "محظور" },
  "ag.expected": { en: "Expected", ar: "المتوقع" }, "ag.rollback": { en: "Undo", ar: "التراجع" }, "ag.requestRepair": { en: "Request this repair", ar: "اطلب هذا الإصلاح" }, "ag.history2": { en: "history here: {ok} worked, {bad} failed", ar: "السجل هنا: نجح {ok} وفشل {bad}" },
  "ag.actions": { en: "Repairs", ar: "الإصلاحات" }, "ag.approve": { en: "Approve", ar: "موافقة" }, "ag.reject": { en: "Reject", ar: "رفض" }, "ag.typeApprove": { en: "Type I APPROVE to confirm", ar: "اكتب I APPROVE للتأكيد" }, "ag.fourEyes": { en: "A second person must approve (four-eyes rule).", ar: "يجب أن يوافق شخص آخر (قاعدة العينين الأربع)." },
  "ag.verify": { en: "Verification", ar: "التحقق" }, "ag.v.pass": { en: "confirmed", ar: "تأكد" }, "ag.v.fail": { en: "not confirmed", ar: "لم يتأكد" }, "ag.v.inconclusive": { en: "cannot tell", ar: "غير محدد" }, "ag.auto": { en: "automatic", ar: "تلقائي" }, "ag.rolledBack": { en: "rolled back", ar: "تم التراجع" },
  "ag.ba": { en: "Before / after", ar: "قبل / بعد" }, "ag.metric": { en: "Measure", ar: "المقياس" }, "ag.before": { en: "Before", ar: "قبل" }, "ag.after": { en: "After", ar: "بعد" }, "ag.verdict.improved": { en: "improved", ar: "تحسّن" }, "ag.verdict.unchanged": { en: "unchanged", ar: "دون تغيير" }, "ag.verdict.worse": { en: "worse", ar: "أسوأ" },
  "ag.report": { en: "Report", ar: "التقرير" }, "ag.memory": { en: "Similar past cases", ar: "حالات سابقة مشابهة" }, "ag.small": { en: "The selected model is small ({m}). It only helps with the written explanation; the diagnosis and the repairs come from built-in checks and reviewed workflows.", ar: "النموذج المحدد صغير ({m}). يساعد في الشرح المكتوب فقط؛ أما التشخيص والإصلاحات فمن فحوصات مدمجة ومسارات معتمدة." },
  "ag.viewerNote": { en: "Your role can read results but not start repairs.", ar: "دورك يسمح بقراءة النتائج فقط وليس بطلب إصلاحات." }, "ag.empty": { en: "Pick a device, describe the problem and press Start. CIT collects real measurements first, explains what it found, and only then offers repairs.", ar: "اختر جهازاً وصف المشكلة ثم اضغط ابدأ. يجمع CIT قياسات حقيقية أولاً ثم يشرح ما وجده وبعدها فقط يعرض الإصلاحات." },
  "ag.ev.understand": { en: "Understood", ar: "فهم الطلب" }, "ag.ev.plan": { en: "Plan", ar: "الخطة" }, "ag.ev.tool_start": { en: "Running", ar: "تشغيل" }, "ag.ev.tool_end": { en: "Finished", ar: "انتهى" }, "ag.ev.analysis": { en: "Analysis", ar: "التحليل" }, "ag.ev.repair_plan": { en: "Repair options", ar: "خيارات الإصلاح" }, "ag.ev.action": { en: "Repair created", ar: "إنشاء إصلاح" },
  "ag.ev.action_result": { en: "Repair result", ar: "نتيجة الإصلاح" }, "ag.ev.approved": { en: "Approved", ar: "تمت الموافقة" }, "ag.ev.rejected": { en: "Rejected", ar: "رُفض" }, "ag.ev.verify_start": { en: "Re-measuring", ar: "إعادة القياس" }, "ag.ev.verify": { en: "Verification", ar: "التحقق" }, "ag.ev.rollback": { en: "Rolled back", ar: "تراجع" }, "ag.ev.memory": { en: "Memory", ar: "الذاكرة" },
  "ag.ev.awaiting_approval": { en: "Waiting for approval", ar: "بانتظار الموافقة" }, "ag.ev.reconnect": { en: "Reconnecting", ar: "إعادة الاتصال" }, "ag.ev.done": { en: "Done", ar: "انتهى" }, "ag.ev.cancelled": { en: "Stopped", ar: "أُوقف" }, "ag.ev.error": { en: "Error", ar: "خطأ" }, "ag.ev.limit": { en: "Limit", ar: "حد" }, "ag.ev.note": { en: "Note", ar: "ملاحظة" },
  // policy
  "ag.policy": { en: "Autonomy level", ar: "مستوى الاستقلالية" }, "ag.policyHint": { en: "Default is level 1. Only the owner changes it. No level lets the AI run arbitrary commands, skip approvals for risky changes, or change power, security, firewall or credentials.", ar: "الافتراضي هو المستوى 1. المالك وحده يغيّره. لا يسمح أي مستوى للذكاء بتنفيذ أوامر عشوائية أو تجاوز الموافقات على التغييرات الخطرة أو تغيير الطاقة أو الأمان أو الجدار الناري أو بيانات الدخول." },
  "ag.l.0": { en: "Level 0 — Observe: read-only checks, no repairs offered to run.", ar: "المستوى 0 — مراقبة: فحوصات للقراءة فقط دون عرض إصلاحات للتنفيذ." }, "ag.l.1": { en: "Level 1 — Diagnose: collect evidence and propose repairs. Nothing changes unless a person requests it.", ar: "المستوى 1 — تشخيص: جمع الأدلة واقتراح الإصلاحات. لا يتغير شيء ما لم يطلبه شخص." },
  "ag.l.2": { en: "Level 2 — Safe auto-repair: runs only allowlisted, reversible, low-risk repairs by itself (temporary files, DNS cache, safe services, reconnect).", ar: "المستوى 2 — إصلاح آمن تلقائي: ينفذ وحده الإصلاحات المسموحة القابلة للتراجع والمنخفضة الخطورة فقط (الملفات المؤقتة، ذاكرة DNS، خدمات آمنة، إعادة الاتصال)." },
  "ag.l.3": { en: "Level 3 — Approved repair: also requests approval automatically for bigger repairs; they run only after a second person approves.", ar: "المستوى 3 — إصلاح بعد الموافقة: يطلب الموافقة تلقائياً للإصلاحات الأكبر ولا تُنفَّذ إلا بعد موافقة شخص ثانٍ." },
  "ag.l.4": { en: "Level 4 — Maintenance workflow: inside an approved maintenance plan (devices, repairs, time limit) approval-class repairs may run. Restarts never run automatically.", ar: "المستوى 4 — مسار صيانة: ضمن خطة صيانة معتمدة (الأجهزة والإصلاحات والمدة) قد تُنفَّذ إصلاحات تحتاج موافقة. إعادة التشغيل لا تُنفَّذ تلقائياً أبداً." },
  "ag.perHour": { en: "Automatic repairs per hour (limit)", ar: "الإصلاحات التلقائية في الساعة (الحد)" }, "ag.save": { en: "Save", ar: "حفظ" }, "ag.saved": { en: "Saved", ar: "تم الحفظ" }, "ag.ownerOnly": { en: "Only the owner can change this.", ar: "المالك وحده يستطيع تغيير هذا." },
  "ag.stop": { en: "Emergency stop", ar: "إيقاف طارئ" }, "ag.stopHint": { en: "Cancels every running AI session and pending repair and blocks new work until the owner resumes.", ar: "يلغي كل جلسات الذكاء الجارية والإصلاحات المعلّقة ويمنع أي عمل جديد حتى يستأنف المالك." }, "ag.stopConfirm": { en: "Stop everything now?", ar: "إيقاف كل شيء الآن؟" }, "ag.resume": { en: "Resume the AI technician", ar: "استئناف الفني الذكي" },
  "ag.monitor": { en: "Proactive monitoring", ar: "المراقبة الاستباقية" }, "ag.monitorHint": { en: "Every 10 minutes CIT takes one light measurement of each online computer. If memory or CPU is critically high it opens an incident (a normal AI session, so your autonomy level and approvals still apply). At most one incident per computer every 6 hours. Off by default.", ar: "كل 10 دقائق يأخذ CIT قياساً خفيفاً لكل حاسوب متصل. إن كانت الذاكرة أو المعالج مرتفعين بشكل حرج يفتح حادثة (جلسة ذكاء عادية، فتسري عليها مستوى الاستقلالية والموافقات). حادثة واحدة كحد أقصى لكل حاسوب كل 6 ساعات. متوقفة افتراضياً." }, "ag.monitorOn": { en: "Monitor this client's computers", ar: "راقب حواسيب هذا العميل" },
  "ag.plans": { en: "Maintenance plans (level 4)", ar: "خطط الصيانة (المستوى 4)" }, "ag.planNew": { en: "New plan", ar: "خطة جديدة" }, "ag.planTitle": { en: "Plan name", ar: "اسم الخطة" }, "ag.planHours": { en: "Valid for (hours, max 72)", ar: "صالحة لمدة (ساعات، حد أقصى 72)" }, "ag.planDevices": { en: "Devices", ar: "الأجهزة" }, "ag.planRepairs": { en: "Repairs allowed", ar: "الإصلاحات المسموحة" },
  "ag.planHint": { en: "A second administrator (or the owner) must approve the plan. Restarts cannot be part of a plan.", ar: "يجب أن يوافق مسؤول آخر (أو المالك) على الخطة. لا يمكن أن تشمل الخطة إعادة التشغيل." }, "ag.expires": { en: "expires", ar: "تنتهي" },
  // knowledge
  "ag.cases": { en: "Cases", ar: "الحالات" }, "ag.procedures": { en: "Procedures", ar: "الإجراءات" }, "ag.search": { en: "Search similar cases…", ar: "ابحث عن حالات مشابهة…" }, "ag.caseStatus.verified": { en: "verified", ar: "موثّقة" }, "ag.caseStatus.unverified": { en: "unverified", ar: "غير موثّقة" }, "ag.caseStatus.rejected": { en: "rejected", ar: "مرفوضة" },
  "ag.pStatus.approved": { en: "approved", ar: "معتمد" }, "ag.pStatus.proposed": { en: "proposed", ar: "مقترح" }, "ag.pStatus.retired": { en: "retired", ar: "متقاعد" }, "ag.pStatus.rejected": { en: "rejected", ar: "مرفوض" },
  "ag.kbHint": { en: "Only repairs whose outcome check passed are stored as verified and used to rank advice. Imported or proposed procedures stay inactive until an administrator approves them, and they can only point to repairs that already exist in CIT's reviewed library.", ar: "تُحفظ كموثّقة فقط الإصلاحات التي نجح فحص نتيجتها وتُستخدم لترتيب النصائح. الإجراءات المستوردة أو المقترحة تبقى غير فعّالة حتى يعتمدها مسؤول، ولا يمكنها إلا الإشارة إلى إصلاحات موجودة أصلاً في مكتبة CIT المعتمدة." },
  "ag.good": { en: "Helpful", ar: "مفيدة" }, "ag.bad": { en: "Wrong", ar: "خاطئة" }, "ag.export": { en: "Export", ar: "تصدير" }, "ag.import": { en: "Import", ar: "استيراد" }, "ag.importHint": { en: "Choose a CIT knowledge file (.json). It is validated first and lands as proposed/unverified.", ar: "اختر ملف معرفة CIT (‎.json). يُتحقق منه أولاً ويدخل كمقترح/غير موثّق." }, "ag.restore": { en: "Restore from backup (keeps approved status; owner only)", ar: "استعادة من نسخة احتياطية (يحافظ على حالة الاعتماد؛ للمالك فقط)" },
  "ag.imported": { en: "Imported: {p} procedures, {c} cases.", ar: "تم الاستيراد: {p} إجراء و{c} حالة." }, "ag.propose": { en: "Propose a procedure", ar: "اقتراح إجراء" }, "ag.pKey": { en: "Key", ar: "المعرّف" }, "ag.pTitle": { en: "Title", ar: "العنوان" }, "ag.pSig": { en: "Problem codes (space separated)", ar: "رموز المشكلة (مفصولة بمسافة)" }, "ag.pWf": { en: "Repair from the library", ar: "إصلاح من المكتبة" }, "ag.pBody": { en: "Runbook text (documentation only; never executed)", ar: "نص الدليل (توثيق فقط؛ لا يُنفَّذ أبداً)" }, "ag.pNone": { en: "none (advice only)", ar: "لا شيء (نصيحة فقط)" },
  "ag.retire": { en: "Retire", ar: "إيقاف" }, "ag.when": { en: "When", ar: "متى" }, "ag.req": { en: "Request", ar: "الطلب" }, "ag.cause": { en: "Root cause", ar: "السبب الجذري" }, "ag.wf": { en: "Repair", ar: "الإصلاح" }, "ag.outcome": { en: "Outcome", ar: "النتيجة" }, "ag.status": { en: "Status", ar: "الحالة" }, "ag.version": { en: "v{n}", ar: "الإصدار {n}" }, "ag.source": { en: "Source", ar: "المصدر" },
  // models
  "ag.mHost": { en: "Computer", ar: "الحاسوب" }, "ag.mSize": { en: "Size", ar: "الحجم" }, "ag.mCtx": { en: "Context", ar: "السياق" }, "ag.mTool": { en: "Tool choice", ar: "اختيار الأدوات" }, "ag.mJson": { en: "Structured output", ar: "المخرجات المنظّمة" }, "ag.mAr": { en: "Arabic", ar: "العربية" }, "ag.mReason": { en: "Reasoning", ar: "الاستدلال" }, "ag.mFit": { en: "Hardware", ar: "العتاد" }, "ag.mBench": { en: "Measured", ar: "المقاس" }, "ag.runBench": { en: "Measure", ar: "قياس" }, "ag.benching": { en: "Measuring… (up to a minute)", ar: "جارٍ القياس… (حتى دقيقة)" },
  "ag.q.poor": { en: "poor", ar: "ضعيف" }, "ag.q.fair": { en: "fair", ar: "مقبول" }, "ag.q.good": { en: "good", ar: "جيد" }, "ag.q.very good": { en: "very good", ar: "ممتاز" }, "ag.fit.fits": { en: "fits", ar: "مناسب" }, "ag.fit.tight": { en: "tight", ar: "ضيق" }, "ag.fit.too large": { en: "too large", ar: "كبير جداً" }, "ag.fit.unknown": { en: "unknown", ar: "غير معروف" },
  "ag.benchRes": { en: "JSON {j} · Arabic {a} · {ms} ms", ar: "JSON {j} · العربية {a} · {ms} مللي ثانية" }, "ag.yes": { en: "ok", ar: "سليم" }, "ag.no": { en: "failed", ar: "فشل" }, "ag.noBench": { en: "not measured", ar: "لم يُقس" },
  "ag.modelsHint": { en: "A 1.5B model is fine for short explanations but cannot be trusted to choose tools or write good Arabic. CIT's diagnosis does not depend on it. For better Arabic and reasoning use a 7B+ Qwen when the computer has enough memory. CIT never downloads a model by itself.", ar: "نموذج 1.5B مناسب لشروحات قصيرة لكن لا يُعتمد عليه في اختيار الأدوات ولا في عربية جيدة. تشخيص CIT لا يعتمد عليه. للحصول على عربية واستدلال أفضل استخدم Qwen بحجم 7B فأكثر إن كانت ذاكرة الحاسوب تكفي. لا ينزّل CIT أي نموذج من تلقاء نفسه." },
  "ag.offlineHost": { en: "offline", ar: "غير متصل" },
});

const STATE_CLS: Record<string, string> = { healthy: "ok", problem_detected: "bad", inconclusive: "warn", check_failed: "bad", unreachable: "bad", permission_required: "warn" };
const SEV_CLS: Record<string, string> = { critical: "bad", warning: "warn", info: "" };
const ACTIVE = ["running", "verifying"];
const lvlName = (n: number) => t("ag.lvl." + n);
const q = (k: string) => (k && k.startsWith("ag.") ? k : k);
void q;

function Pillx({ cls, children }: { cls?: string; children: ReactNode }) { return <span className={"pill " + (cls ?? "")}>{children}</span>; }

function evText(e: any): string {
  const d = e.data ?? {};
  if (e.kind === "tool_end") return `${d.status}${d.ms != null ? ` · ${d.ms} ms` : ""}${d.findings?.length ? ` · ${d.findings.join(", ")}` : ""}${d.error ? ` · ${d.error}` : ""}`;
  if (e.kind === "tool_start") return d.title ?? "";
  if (e.kind === "verify") return `${d.result ?? ""} — ${d.why ?? ""}`;
  if (e.kind === "action") return `${d.cls ?? ""} · ${d.status ?? ""}`;
  if (e.kind === "action_result") return `${d.status ?? ""}${d.error ? ` · ${d.error}` : ""}`;
  if (e.kind === "understand") return `${d.intent} · ${d.target_kind}`;
  if (e.kind === "plan") return (d.stages ?? []).map((s: string[]) => s.join(" + ")).join("  →  ");
  return "";
}

function Session({ oid, s, onChange }: { oid: string; s: any; onChange: () => void }) {
  useLang(); const me = useMe(); const { err, busy, go } = useAct(); const [typed, setTyped] = useState<Record<string, string>>({});
  const rep = s.report; const senior = isSenior(me); const viewer = me?.role === "viewer"; const live = ACTIVE.includes(s.status);
  const last = [...s.events].reverse().find((e: any) => e.kind === "tool_start");
  const running = live && last && !s.events.some((e: any) => e.kind === "tool_end" && e.id > last.id) ? last.title : "";
  const decide = (a: any, yes: boolean) => go(async () => { await api(`/orgs/${oid}/agent/actions/${a.id}/${yes ? "approve" : "reject"}`, "POST", yes ? { confirm: typed[a.id] ?? null } : {}); onChange(); });
  const request = (p: any) => go(async () => { await api(`/orgs/${oid}/agent/sessions/${s.id}/repairs`, "POST", { workflow: p.workflow, finding: p.finding }); onChange(); });
  return <div className="fade">
    <div className="card"><div className="row">
      <h3 style={{ margin: 0, flex: 1 }} dir="auto">{s.request}</h3>
      {s.outcome && <Pillx cls={STATE_CLS[s.outcome]}>{t("ag.state." + s.outcome)}</Pillx>}
      <Pillx cls={s.status === "completed" ? "ok" : s.status === "failed" || s.status === "cancelled" ? "bad" : "warn"}>{t("ag.status." + s.status)}</Pillx>
      {s.resolution && s.status === "completed" && <Pillx cls={s.resolution === "resolved" ? "ok" : s.resolution === "none" ? "" : "warn"}>{t("ag.res." + s.resolution)}</Pillx>}
      {live || s.status === "awaiting_approval" ? <button className="btn danger sm" onClick={() => go(async () => { await api(`/orgs/${oid}/agent/sessions/${s.id}/cancel`, "POST"); onChange(); })}>{t("ag.cancel")}</button> : null}</div>
      <div className="row mt"><span className="tag">{s.target_kind}: <span className="ltr">{s.target}</span></span><span className="tag">{t("ag.levelIs", { n: s.level, name: lvlName(s.level) })}</span>{s.model && <span className="tag ltr">{s.model} @ {s.host}</span>}<span className="tag">{when(s.created_at)}</span></div>
      {running && <div className="banner mt" role="status">⏳ {t("ag.now", { x: running })}</div>}<Err m={err} /></div>

    <div className="grid mt" style={{ gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)", alignItems: "start" }}>
      <div className="card"><h3>{t("ag.timeline")}</h3>
        <div style={{ maxHeight: 360, overflow: "auto" }}>{s.events.filter((e: any) => e.kind !== "tool_start" || !s.events.some((x: any) => x.kind === "tool_end" && x.title === e.title && x.id > e.id)).map((e: any) => <div key={e.id} className="row" style={{ alignItems: "baseline", gap: 8, marginBottom: 3 }}>
          <span className="tag ltr" style={{ minWidth: 54 }}>{((e.ts - s.created_at)).toFixed(1)}s</span><b style={{ minWidth: 110 }}>{t("ag.ev." + e.kind)}</b><span className="ltr" dir="ltr">{e.kind === "tool_end" || e.kind === "tool_start" ? e.title : ""}</span><span className="tag grow" dir="auto">{e.kind === "tool_end" || e.kind === "tool_start" ? evText(e) : e.kind === "understand" || e.kind === "plan" ? evText(e) : e.title}</span></div>)}</div></div>

      <div className="card"><h3>{t("ag.causes")}</h3>
        {!rep ? <div className="tag">…</div> : rep.causes.length ? rep.causes.map((c: any, i: number) => <div key={i} style={{ marginBottom: 10 }}><div className="row"><b dir="auto" style={{ flex: 1 }}>{c.title}</b><Pillx cls={SEV_CLS[c.severity]}>{t("ag.conf", { n: Math.round((c.confidence ?? 0) * 100) })}</Pillx>{c.seen_before ? <span className="tag">{t("ag.seen", { n: c.seen_before })}</span> : null}</div>
          <div className="tag" dir="auto">{c.evidence}</div><div style={{ height: 6, background: "var(--panel2)", borderRadius: 4, marginTop: 4 }}><div style={{ width: `${Math.round((c.confidence ?? 0) * 100)}%`, height: 6, borderRadius: 4, background: "var(--acc)" }} /></div></div>) : <Empty title={t("ag.noCause")} />}</div>
    </div>

    {rep && <div className="card mt"><h3>{t("ag.findings")}</h3>
      {rep.findings.length ? rep.findings.map((f: any, i: number) => <div key={i} className="row" style={{ alignItems: "baseline", gap: 8, marginBottom: 4 }}><Pillx cls={SEV_CLS[f.severity]}>{f.severity}</Pillx><b dir="auto">{f.title}</b><span className="tag grow" dir="auto">{f.evidence}{f.next ? ` — ${f.next}` : ""}</span></div>) : <div className="tag">—</div>}
      <details className="mt"><summary>{t("ag.checks")} ({rep.checks.filter((c: any) => c.status === "ok").length}/{rep.checks.length})</summary>
        <DataTable rows={rep.checks.map((c: any, i: number) => ({ ...c, id: String(i) }))} pageSize={20} cols={[{ head: t("ag.tool"), cell: (c: any) => <span className="ltr">{c.tool}{c.required ? " *" : ""}</span>, text: (c: any) => c.tool }, { head: t("ag.result"), cell: (c: any) => <Pillx cls={c.status === "ok" ? "ok" : "bad"}>{c.status}</Pillx>, text: (c: any) => c.status }, { head: t("ag.ms"), num: true, cell: (c: any) => c.ms, text: (c: any) => String(c.ms) }]} />
        <div className="tag">* {t("ag.required")}</div></details></div>}

    {rep && <div className="card mt"><h3>{t("ag.repairs")}</h3>
      {viewer && <div className="tag">{t("ag.viewerNote")}</div>}
      {!rep.repair_plan.length ? <Empty title={t("ag.noRepairs")} /> : rep.repair_plan.map((p: any, i: number) => {
        const act = s.actions.find((a: any) => a.workflow === p.workflow);
        return <div key={i} className="card mt"><div className="row"><b dir="auto" style={{ flex: 1 }}>{p.title}</b><Pillx cls={p.cls === "safe" ? "ok" : p.cls === "disruptive" ? "bad" : "warn"}>{t("ag.cls." + p.cls)}</Pillx><Pillx cls={p.decision === "auto" ? "ok" : p.decision === "blocked" ? "bad" : ""}>{t("ag.dec." + p.decision)}</Pillx></div>
          <div className="tag" dir="auto">{p.finding_title}</div><div className="tag" dir="auto">{p.reason}</div>
          <div className="tag" dir="auto">{t("ag.expected")}: {p.expected}</div>{p.rollback && <div className="tag" dir="auto">{t("ag.rollback")}: {p.rollback}</div>}
          {p.history && (p.history.ok || p.history.bad) ? <div className="tag">{t("ag.history2", { ok: p.history.ok, bad: p.history.bad })}</div> : null}
          {!act && (p.decision === "propose" || p.decision === "request") && !viewer && s.level > 0 && <button className="btn sm mt" disabled={busy} onClick={() => void request(p)}>{t("ag.requestRepair")}</button>}</div>; })}</div>}

    {s.actions.length > 0 && <div className="card mt"><h3>{t("ag.actions")}</h3>{s.actions.map((a: any) => <div key={a.id} className="card mt"><div className="row"><b dir="auto" style={{ flex: 1 }}>{a.title}</b>{a.auto ? <span className="tag">{t("ag.auto")}</span> : null}<Pillx cls={a.status === "ok" ? "ok" : a.status === "failed" || a.status === "rejected" || a.status === "cancelled" ? "bad" : "warn"}>{a.status}</Pillx></div>
      {a.verify && <div className="row mt"><b>{t("ag.verify")}:</b><Pillx cls={a.verify.result === "pass" ? "ok" : a.verify.result === "fail" ? "bad" : "warn"}>{t("ag.v." + a.verify.result)}</Pillx><span className="tag" dir="auto">{a.verify.why}</span>{a.verify.rollback?.done && <Pillx cls="warn">{t("ag.rolledBack")}</Pillx>}</div>}
      {a.status === "pending_approval" && senior && <div className="row mt">{a.cls === "disruptive" && <input style={{ maxWidth: 200 }} aria-label={t("ag.typeApprove")} placeholder={t("ag.typeApprove")} value={typed[a.id] ?? ""} onChange={e => setTyped({ ...typed, [a.id]: e.target.value })} />}
        <button className="btn sm" disabled={busy} onClick={() => void decide(a, true)}>{t("ag.approve")}</button><button className="btn ghost sm" disabled={busy} onClick={() => void decide(a, false)}>{t("ag.reject")}</button><span className="tag">{t("ag.fourEyes")}</span></div>}
      {a.status === "pending_approval" && !senior && <div className="tag mt">{t("ag.fourEyes")}</div>}</div>)}</div>}

    {rep?.before_after?.length > 0 && <div className="card mt"><h3>{t("ag.ba")}</h3><DataTable rows={rep.before_after.map((b: any) => ({ ...b, id: b.metric }))} pageSize={12} cols={[{ head: t("ag.metric"), cell: (b: any) => <span className="ltr">{b.metric}</span>, text: (b: any) => b.metric }, { head: t("ag.before"), num: true, cell: (b: any) => b.before }, { head: t("ag.after"), num: true, cell: (b: any) => b.after }, { head: t("ag.result"), cell: (b: any) => <Pillx cls={b.verdict === "improved" ? "ok" : b.verdict === "worse" ? "bad" : ""}>{t("ag.verdict." + b.verdict)}</Pillx> }]} /></div>}

    {s.answer && <div className="card mt"><h3>{t("ag.report")}</h3><Markdown text={s.answer.split("\n").map((l: string) => l.replace(/^(\s+)(-|\*)\s/, "$1- ")).join("\n")} /></div>}
  </div>;
}

function TechnicianTab({ oid, presetWorker }: { oid: string; presetWorker?: string }) {
  useLang(); const me = useMe(); const targets = useData<any[]>(`/orgs/${oid}/agent/targets`, 20000); const models = useData<any[]>(`/orgs/${oid}/agent/models`, 60000); const policy = useData<any>(`/orgs/${oid}/agent/policy`, 15000);
  const list = useData<any[]>(`/orgs/${oid}/agent/sessions`, 6000); const { err, busy, go } = useAct();
  const [target, setTarget] = useState(presetWorker ? `worker|${presetWorker}` : ""); const [host, setHost] = useState("main"); const [model, setModel] = useState(""); const [text, setText] = useState("");
  const [sid, setSid] = useState(""); const [s, setS] = useState<any>(null); const [ev, setEv] = useState<any[]>([]); const [nonce, setNonce] = useState(0); const [pe, setPe] = useState("");
  const timer = useRef<any>(null);
  useEffect(() => { if (!target && targets.data?.length) { const first = targets.data.find(x => x.kind === "worker") ?? targets.data[0]; setTarget(`${first.kind}|${first.id}`); } }, [targets.data]);
  const hostModels = (models.data ?? []).filter(m => m.host === host && m.model);
  useEffect(() => { if (!hostModels.find(m => m.model === model)) setModel(hostModels.find(m => m.model.includes("qwen") && m.params_b && m.params_b >= 6)?.model ?? hostModels.find(m => m.model.includes("qwen"))?.model ?? hostModels[0]?.model ?? ""); }, [host, models.data]);
  useEffect(() => {
    if (!sid) { setS(null); setEv([]); return; }
    let stop = false; let after = 0; let acc: any[] = []; setEv([]); setPe("");
    const tick = async () => {
      try {
        const j = await api<any>(`/orgs/${oid}/agent/sessions/${sid}?after=${after}`); if (stop) return;
        if (j.events.length) { after = j.events[j.events.length - 1].id; acc = [...acc, ...j.events]; }
        setEv(acc); setS({ ...j, events: acc });
        if (ACTIVE.includes(j.status)) timer.current = setTimeout(tick, 1000); else if (j.status === "awaiting_approval") timer.current = setTimeout(tick, 3000); else list.reload();
      } catch (e: any) { if (!stop) setPe(e.message); }
    };
    void tick(); return () => { stop = true; clearTimeout(timer.current); };
  }, [sid, oid, nonce]);
  void ev;
  const [tk, tid] = target.split("|"); const start = () => go(async () => {
    const r = await api<any>(`/orgs/${oid}/agent/sessions`, "POST", { request: text, target_kind: tk, target: tid, host, model: model || null, lang: getLang() }); setSid(r.id); setNonce(n => n + 1); list.reload();
  });
  const mdl = models.data?.find(m => m.host === host && m.model === model); const small = mdl && (mdl.params_b ?? 0) < 3;
  const examples = ["ag.ex1", "ag.ex2", "ag.ex3", "ag.ex4", "ag.ex5", "ag.ex6"];
  return <div className="grid" style={{ gridTemplateColumns: "minmax(200px,250px) minmax(0,1fr)", alignItems: "start" }}>
    <div className="card"><div className="row"><h3 style={{ flex: 1, margin: 0 }}>{t("ag.history")}</h3><button className="btn sm" onClick={() => { setSid(""); }}>{t("ag.new")}</button></div>
      <div className="mt" style={{ maxHeight: 620, overflow: "auto" }}>{(list.data ?? []).map(r => <button key={r.id} className={"btn sm " + (sid === r.id ? "" : "ghost")} style={{ display: "block", width: "100%", textAlign: "start", marginBottom: 4 }} onClick={() => { setSid(r.id); setNonce(n => n + 1); }}>
        <div dir="auto" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.request}</div><span className="tag">{r.outcome ? t("ag.state." + r.outcome) : t("ag.status." + r.status)} · {when(r.created_at)}</span></button>)}
        {!list.data?.length && <div className="tag">—</div>}</div></div>
    <div>
      <div className="card"><div className="row" style={{ alignItems: "end" }}>
        <div style={{ minWidth: 200 }}><label>{t("ag.target")}</label><select value={target} onChange={e => setTarget(e.target.value)} aria-label={t("ag.target")}>{(targets.data ?? []).map(x => <option key={x.kind + x.id} value={`${x.kind}|${x.id}`}>{x.name}{x.state === "online" || x.state === "local" || x.state === "assigned" ? "" : ` (${x.state})`}</option>)}</select></div>
        <div style={{ minWidth: 170 }}><label>{t("ag.host")}</label><select value={host} onChange={e => setHost(e.target.value)} aria-label={t("ag.host")}>{[...new Set((models.data ?? []).map(m => m.host))].map(h => { const m = (models.data ?? []).find(x => x.host === h); return <option key={h} value={h} disabled={!m.online}>{m.host_name}{m.online ? "" : ` (${t("ag.offlineHost")})`}</option>; })}</select></div>
        <div style={{ minWidth: 180 }}><label>{t("ag.model")}</label><select className="ltr" value={model} onChange={e => setModel(e.target.value)} aria-label={t("ag.model")}><option value="">{t("ag.noModel")}</option>{hostModels.map(m => <option key={m.model}>{m.model}</option>)}</select></div>
        {policy.data && <span className={"pill " + (policy.data.halted ? "bad" : "")} title={policy.data.name}>{t("ag.levelIs", { n: policy.data.level, name: lvlName(policy.data.level) })}</span>}</div>
        {policy.data?.halted && <div className="banner bad mt" role="alert">{t("ag.halted")}</div>}
        {small && <div className="banner mt">{t("ag.small", { m: model })}</div>}
        <div className="mt"><label>{t("ag.ask")}</label><textarea rows={3} dir="auto" aria-label={t("ag.ask")} placeholder={t("ag.placeholder")} value={text} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && text.trim().length >= 3 && tid) void start(); }} /></div>
        <div className="row mt">{examples.map(k => <button key={k} className="btn ghost sm" onClick={() => setText(t(k))}>{t(k)}</button>)}</div>
        <div className="row mt"><button className="btn" disabled={busy || text.trim().length < 3 || !tid || me?.role === "viewer" || policy.data?.halted} onClick={() => void start()}>{t("ag.start")}</button></div><Err m={err || pe} /></div>
      <div className="mt">{s ? <Session oid={oid} s={s} onChange={() => setNonce(n => n + 1)} /> : <div className="card empty mt"><b>{t("ag.empty")}</b></div>}</div></div></div>;
}

function PolicyTab({ oid }: { oid: string }) {
  useLang(); const me = useMe(); const owner = me?.role === "owner"; const pol = useData<any>(`/orgs/${oid}/agent/policy`, 10000); const { err, busy, ok, go } = useAct();
  const targets = useData<any[]>(`/orgs/${oid}/agent/targets`); const procs = useData<any[]>(`/orgs/${oid}/agent/kb/procedures`); const plans = useData<any[]>(`/orgs/${oid}/agent/maintenance`, 15000); const mon = useData<{ enabled: boolean }>(`/orgs/${oid}/agent/monitor`);
  const [lvl, setLvl] = useState<number | null>(null); const [cap, setCap] = useState<number | null>(null);
  const [title, setTitle] = useState(""); const [hours, setHours] = useState(2); const [tg, setTg] = useState<string[]>([]); const [wf, setWf] = useState<string[]>([]);
  const cur = lvl ?? pol.data?.level ?? 1; const senior = isSenior(me);
  const flip = (a: string[], v: string, set: (x: string[]) => void) => set(a.includes(v) ? a.filter(x => x !== v) : [...a, v]);
  const wfs = (procs.data ?? []).filter(p => p.source === "builtin" && p.cls !== "disruptive");
  return <div className="grid" style={{ gridTemplateColumns: "minmax(0,1fr)" }}>
    <div className="card"><h3>{t("ag.policy")}</h3><div className="tag">{t("ag.policyHint")}</div><Err m={err} />
      <div className="mt">{[0, 1, 2, 3, 4].map(n => <label key={n} className="row" style={{ alignItems: "flex-start", gap: 8, marginBottom: 6 }}><input type="radio" name="lvl" disabled={!owner} checked={cur === n} onChange={() => setLvl(n)} /><span dir="auto">{t("ag.l." + n)}</span></label>)}</div>
      <div className="row mt" style={{ alignItems: "end" }}><div><label>{t("ag.perHour")}</label><input type="number" min={0} max={50} disabled={!owner} value={cap ?? pol.data?.max_auto_per_hour ?? 6} onChange={e => setCap(+e.target.value)} style={{ maxWidth: 120 }} /></div>
        <button className="btn" disabled={!owner || busy} onClick={() => go(async () => { await api(`/orgs/${oid}/agent/policy`, "PUT", { level: cur, max_auto_per_hour: cap ?? pol.data?.max_auto_per_hour ?? 6 }); pol.reload(); }, t("ag.saved"))}>{t("ag.save")}</button>{!owner && <span className="tag">{t("ag.ownerOnly")}</span>}{ok && <span className="tag">{ok}</span>}</div></div>
    <div className="card mt"><h3>{t("ag.stop")}</h3><div className="tag">{t("ag.stopHint")}</div>{pol.data?.halted && <div className="banner bad mt">{t("ag.halted")}</div>}
      <div className="row mt">{senior && <button className="btn danger" disabled={busy} onClick={() => confirm(t("ag.stopConfirm")) && void go(async () => { await api(`/emergency-stop`, "POST"); pol.reload(); })}>{t("ag.stop")}</button>}
        {owner && pol.data?.halted && <button className="btn" disabled={busy} onClick={() => go(async () => { await api(`/agent/resume`, "POST"); pol.reload(); })}>{t("ag.resume")}</button>}</div></div>
    <div className="card mt"><h3>{t("ag.monitor")}</h3><div className="tag">{t("ag.monitorHint")}</div>
      <label className="row mt"><input type="checkbox" disabled={!owner || busy} checked={!!mon.data?.enabled} onChange={e => go(async () => { await api(`/orgs/${oid}/agent/monitor`, "PUT", { enabled: e.target.checked }); mon.reload(); })} />{t("ag.monitorOn")}</label>{!owner && <div className="tag">{t("ag.ownerOnly")}</div>}</div>
    <div className="card mt"><h3>{t("ag.plans")}</h3><div className="tag">{t("ag.planHint")}</div>
      <DataTable rows={plans.data} loading={plans.loading} error={plans.err} pageSize={6} cols={[{ head: t("ag.planTitle"), cell: (p: any) => p.title, text: (p: any) => p.title }, { head: t("ag.planRepairs"), cell: (p: any) => <span className="ltr">{p.workflows.join(", ")}</span> }, { head: t("ag.expires"), cell: (p: any) => when(p.expires_at) }, { head: t("ag.status"), cell: (p: any) => <Pillx cls={p.status === "active" ? "ok" : p.status === "pending" ? "warn" : "bad"}>{p.status}</Pillx> },
        { head: "", cell: (p: any) => p.status === "pending" && senior ? <span className="row"><button className="btn sm" onClick={() => go(async () => { await api(`/orgs/${oid}/agent/maintenance/${p.id}/approve`, "POST"); plans.reload(); })}>{t("ag.approve")}</button><button className="btn ghost sm" onClick={() => go(async () => { await api(`/orgs/${oid}/agent/maintenance/${p.id}/reject`, "POST"); plans.reload(); })}>{t("ag.reject")}</button></span> : null }]} />
      {senior && <div className="card mt"><b>{t("ag.planNew")}</b>
        <div className="row mt"><div className="grow"><label>{t("ag.planTitle")}</label><input value={title} onChange={e => setTitle(e.target.value)} /></div><div><label>{t("ag.planHours")}</label><input type="number" min={1} max={72} value={hours} onChange={e => setHours(+e.target.value)} style={{ maxWidth: 110 }} /></div></div>
        <div className="mt"><label>{t("ag.planDevices")}</label><div className="row">{(targets.data ?? []).filter(x => x.kind === "worker").map(x => <label key={x.id} className="row"><input type="checkbox" checked={tg.includes(x.id)} onChange={() => flip(tg, x.id, setTg)} />{x.name}</label>)}</div></div>
        <div className="mt"><label>{t("ag.planRepairs")}</label><div className="row">{wfs.map(p => <label key={p.key} className="row"><input type="checkbox" checked={wf.includes(p.key)} onChange={() => flip(wf, p.key, setWf)} /><span dir="auto">{p.title}</span></label>)}</div></div>
        <button className="btn mt" disabled={busy || title.length < 3 || !tg.length || !wf.length} onClick={() => go(async () => { await api(`/orgs/${oid}/agent/maintenance`, "POST", { title, targets: tg, workflows: wf, hours }); setTitle(""); plans.reload(); })}>{t("ag.planNew")}</button></div>}</div></div>;
}

function KnowledgeTab({ oid }: { oid: string }) {
  useLang(); const me = useMe(); const senior = isSenior(me); const owner = me?.role === "owner"; const { err, busy, ok, go } = useAct();
  const [sq, setSq] = useState(""); const cases = useData<any[]>(`/orgs/${oid}/agent/kb/cases${sq.length > 2 ? `?q=${encodeURIComponent(sq)}` : ""}`); const procs = useData<any[]>(`/orgs/${oid}/agent/kb/procedures`);
  const [restore, setRestore] = useState(false); const [f, setF] = useState({ key: "", title: "", signature: "", workflow: "", body: "" });
  const exportKb = () => go(async () => { const b = await api<any>(`/orgs/${oid}/agent/kb/export`); const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([JSON.stringify(b, null, 2)], { type: "application/json" })); a.download = "cit-knowledge.json"; a.click(); });
  const importKb = (file: File) => go(async () => { const bundle = JSON.parse(await file.text()); const r = await api<any>(`/orgs/${oid}/agent/kb/import`, "POST", { bundle, restore }); procs.reload(); cases.reload(); return r; }).then(r => r && void 0);
  const builtin = (procs.data ?? []).filter(p => p.source === "builtin");
  const act = (p: any, a: string) => go(async () => { await api(`/orgs/${oid}/agent/kb/procedures/${p.id}/${a}`, "POST"); procs.reload(); });
  return <div className="grid" style={{ gridTemplateColumns: "minmax(0,1fr)" }}>
    <div className="banner">{t("ag.kbHint")}</div><Err m={err} />{ok && <div className="tag">{ok}</div>}
    <div className="card"><div className="row"><h3 style={{ flex: 1, margin: 0 }}>{t("ag.cases")}</h3><input aria-label={t("ag.search")} placeholder={t("ag.search")} value={sq} onChange={e => setSq(e.target.value)} style={{ maxWidth: 260 }} /></div>
      <DataTable rows={cases.data} loading={cases.loading} error={cases.err} pageSize={8} cols={[{ head: t("ag.when"), cell: (c: any) => when(c.created_at) }, { head: t("ag.req"), cell: (c: any) => <span dir="auto">{c.request}</span>, text: (c: any) => c.request }, { head: t("ag.cause"), cell: (c: any) => <span dir="auto">{c.root_cause ?? "—"}</span>, text: (c: any) => c.root_cause ?? "" }, { head: t("ag.wf"), cell: (c: any) => <span className="ltr">{c.workflow ?? "—"}</span> },
        { head: t("ag.outcome"), cell: (c: any) => c.outcome, text: (c: any) => c.outcome }, { head: t("ag.status"), cell: (c: any) => <Pillx cls={c.status === "verified" ? "ok" : c.status === "rejected" ? "bad" : "warn"}>{t("ag.caseStatus." + c.status)}</Pillx> },
        { head: "", cell: (c: any) => c.id && c.status ? <span className="row"><button className="btn ghost sm" disabled={busy} onClick={() => go(async () => { await api(`/orgs/${oid}/agent/kb/cases/${c.id}/feedback`, "POST", { delta: 1 }); cases.reload(); })}>{t("ag.good")}</button><button className="btn ghost sm" disabled={busy} onClick={() => go(async () => { await api(`/orgs/${oid}/agent/kb/cases/${c.id}/feedback`, "POST", { delta: -1 }); cases.reload(); })}>{t("ag.bad")}</button></span> : null }]} /></div>
    <div className="card mt"><h3>{t("ag.procedures")}</h3>
      <DataTable rows={(procs.data ?? []).map(p => ({ ...p, id: p.id }))} loading={procs.loading} error={procs.err} pageSize={8} cols={[{ head: t("ag.pKey"), cell: (p: any) => <span className="ltr">{p.key} · {t("ag.version", { n: p.version })}</span>, text: (p: any) => p.key }, { head: t("ag.pTitle"), cell: (p: any) => <span dir="auto">{p.title}</span>, text: (p: any) => p.title }, { head: t("ag.pSig"), cell: (p: any) => <span className="ltr">{(p.signature ?? []).join(" ")}</span> },
        { head: t("ag.wf"), cell: (p: any) => <span className="ltr">{p.workflow ?? "—"}</span> }, { head: t("ag.source"), cell: (p: any) => p.source }, { head: t("ag.status"), cell: (p: any) => <Pillx cls={p.status === "approved" ? "ok" : p.status === "proposed" ? "warn" : "bad"}>{t("ag.pStatus." + p.status)}</Pillx> },
        { head: "", cell: (p: any) => p.source !== "builtin" && senior ? <span className="row">{p.status === "proposed" && <><button className="btn sm" disabled={busy} onClick={() => void act(p, "approve")}>{t("ag.approve")}</button><button className="btn ghost sm" disabled={busy} onClick={() => void act(p, "reject")}>{t("ag.reject")}</button></>}{p.status === "approved" && <button className="btn ghost sm" disabled={busy} onClick={() => void act(p, "retire")}>{t("ag.retire")}</button>}</span> : null }]} /></div>
    <div className="card mt"><h3>{t("ag.propose")}</h3>
      <div className="row"><div><label>{t("ag.pKey")}</label><input className="ltr" value={f.key} onChange={e => setF({ ...f, key: e.target.value })} /></div><div className="grow"><label>{t("ag.pTitle")}</label><input dir="auto" value={f.title} onChange={e => setF({ ...f, title: e.target.value })} /></div></div>
      <div className="row mt"><div className="grow"><label>{t("ag.pSig")}</label><input className="ltr" value={f.signature} onChange={e => setF({ ...f, signature: e.target.value })} /></div><div><label>{t("ag.pWf")}</label><select value={f.workflow} onChange={e => setF({ ...f, workflow: e.target.value })}><option value="">{t("ag.pNone")}</option>{builtin.map(p => <option key={p.key} value={p.key}>{p.title}</option>)}</select></div></div>
      <div className="mt"><label>{t("ag.pBody")}</label><textarea rows={3} dir="auto" value={f.body} onChange={e => setF({ ...f, body: e.target.value })} /></div>
      <button className="btn mt" disabled={busy || f.key.length < 2 || f.title.length < 3 || !f.signature.trim()} onClick={() => go(async () => { await api(`/orgs/${oid}/agent/kb/procedures`, "POST", { key: f.key, title: f.title, signature: f.signature.trim().split(/\s+/), workflow: f.workflow || null, body: f.body }); setF({ key: "", title: "", signature: "", workflow: "", body: "" }); procs.reload(); })}>{t("ag.propose")}</button></div>
    <div className="card mt"><div className="row"><button className="btn" disabled={busy} onClick={() => void exportKb()}>{t("ag.export")}</button>
      {senior && <><label className="btn ghost" style={{ cursor: "pointer" }}>{t("ag.import")}<input type="file" accept="application/json,.json" style={{ display: "none" }} onChange={e => { const fl = e.target.files?.[0]; if (fl) void importKb(fl); e.target.value = ""; }} /></label>{owner && <label className="row"><input type="checkbox" checked={restore} onChange={e => setRestore(e.target.checked)} />{t("ag.restore")}</label>}</>}</div><div className="tag mt">{t("ag.importHint")}</div></div></div>;
}

function ModelsTab({ oid }: { oid: string }) {
  useLang(); const models = useData<any[]>(`/orgs/${oid}/agent/models`, 60000); const { err, busy, go } = useAct(); const [res, setRes] = useState<Record<string, any>>({});
  const bench = (m: any) => go(async () => { const r = await api<any>(`/orgs/${oid}/agent/models/benchmark`, "POST", { host: m.host, model: m.model }); setRes({ ...res, [m.host + m.model]: r }); models.reload(); });
  const Q = (v: string) => <Pillx cls={v === "poor" ? "bad" : v === "fair" ? "warn" : "ok"}>{t("ag.q." + v)}</Pillx>;
  return <div><div className="banner">{t("ag.modelsHint")}</div><Err m={err} />
    <div className="card mt"><DataTable rows={(models.data ?? []).filter(m => m.model).map(m => ({ ...m, id: m.host + m.model }))} loading={models.loading} error={models.err} pageSize={10} cols={[{ head: t("ag.mHost"), cell: (m: any) => m.host_name, text: (m: any) => m.host_name }, { head: t("ag.model"), cell: (m: any) => <span className="ltr">{m.model}</span>, text: (m: any) => m.model }, { head: t("ag.mSize"), cell: (m: any) => <span className="ltr">{m.params_b ? m.params_b + "B · " : ""}{m.size_gb} GB</span> },
      { head: t("ag.mCtx"), cell: (m: any) => <span className="ltr">{m.context ?? "—"}</span> }, { head: t("ag.mTool"), cell: (m: any) => Q(m.tool_calling) }, { head: t("ag.mJson"), cell: (m: any) => Q(m.structured_json) }, { head: t("ag.mAr"), cell: (m: any) => Q(m.arabic) }, { head: t("ag.mReason"), cell: (m: any) => Q(m.reasoning) },
      { head: t("ag.mFit"), cell: (m: any) => t("ag.fit." + m.hardware_fit) }, { head: t("ag.mBench"), cell: (m: any) => m.benchmark ? <span className="tag">{t("ag.benchRes", { j: m.benchmark.json_ok ? t("ag.yes") : t("ag.no"), a: m.benchmark.arabic_ok ? t("ag.yes") : t("ag.no"), ms: m.benchmark.ms })}</span> : <span className="tag">{t("ag.noBench")}</span> },
      { head: "", cell: (m: any) => <button className="btn ghost sm" disabled={busy} onClick={() => void bench(m)}>{busy ? t("ag.benching") : t("ag.runBench")}</button> }]} /></div></div>;
}

aiTabs.push({ key: "tech", order: 0, label: "ag.tabTech", render: ({ oid, presetWorker }) => <TechnicianTab oid={oid} presetWorker={presetWorker} /> });
aiTabs.push({ key: "kb", order: 20, label: "ag.tabKb", render: ({ oid }) => <KnowledgeTab oid={oid} /> });
aiTabs.push({ key: "models", order: 30, label: "ag.tabModels", render: ({ oid }) => <ModelsTab oid={oid} /> });
aiTabs.push({ key: "policy", order: 40, label: "ag.tabPolicy", render: ({ oid }) => <PolicyTab oid={oid} /> });
