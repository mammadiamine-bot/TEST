import { useSyncExternalStore } from "react";
export type Lang = "en" | "ar";
const D: Record<string, { en: string; ar: string }> = {
  app: { en: "Claude IT Technician", ar: "فني الدعم Claude" },
  overview: { en: "Overview", ar: "نظرة عامة" }, discovery: { en: "Discovery & pairing", ar: "الاكتشاف والإقران" },
  inventory: { en: "Inventory", ar: "الأجهزة" }, metrics: { en: "Live metrics", ar: "المقاييس الحية" },
  diagnostics: { en: "Diagnostics", ar: "التشخيص" }, chat: { en: "AI Technician", ar: "الفني الذكي" },
  repairs: { en: "Repair plans", ar: "خطط الإصلاح" }, remote: { en: "Support sessions", ar: "جلسات الدعم" },
  console: { en: "PowerShell (restricted)", ar: "PowerShell (مقيّد)" }, software: { en: "Software", ar: "البرامج" },
  hardware: { en: "Hardware health", ar: "صحة العتاد" }, logs: { en: "Windows logs", ar: "سجلات ويندوز" },
  reports: { en: "Reports", ar: "التقارير" }, audit: { en: "Audit history", ar: "سجل التدقيق" }, settings: { en: "Settings", ar: "الإعدادات" },
  device: { en: "Device", ar: "الجهاز" }, none: { en: "No device selected", ar: "لم يتم اختيار جهاز" },
  online: { en: "Online – cloud AI available", ar: "متصل – الذكاء السحابي متاح" },
  offline: { en: "Offline – scripted diagnostics only (no cloud AI)", ar: "غير متصل – تشخيص مبرمج فقط (بدون ذكاء سحابي)" },
  run: { en: "Run full diagnostic", ar: "تشغيل تشخيص كامل" }, running: { en: "Running…", ar: "جارٍ التنفيذ…" },
  cpu: { en: "CPU", ar: "المعالج" }, ram: { en: "RAM", ar: "الذاكرة" },
  approve: { en: "Approve", ar: "موافقة" }, reject: { en: "Reject", ar: "رفض" }, execute: { en: "Execute approved steps", ar: "تنفيذ الخطوات المعتمدة" },
  approveLow: { en: "Approve all low-risk", ar: "اعتماد كل منخفض الخطورة" }, createPlan: { en: "Create repair plan", ar: "إنشاء خطة إصلاح" },
  highConfirm: { en: "I confirm this HIGH-RISK action on this device", ar: "أؤكد تنفيذ هذا الإجراء عالي الخطورة على هذا الجهاز" },
  send: { en: "Send", ar: "إرسال" }, lang: { en: "العربية", ar: "English" },
  devices: { en: "Device inventory (classic)", ar: "جرد الأجهزة (كلاسيكي)" }, quick: { en: "Quick Connect", ar: "اتصال سريع" }, workspace: { en: "AI Workspace", ar: "مساحة الذكاء الاصطناعي" },
  projects: { en: "Development Projects", ar: "مشاريع التطوير" }, rclaude: { en: "Remote Claude Sessions", ar: "جلسات Claude البعيدة" },
  servers: { en: "Server Manager", ar: "إدارة الخوادم" }, desktop: { en: "Remote Desktop", ar: "سطح المكتب البعيد" }, files: { en: "File Manager", ar: "مدير الملفات" },
  fixes: { en: "Diagnostics & Repairs", ar: "التشخيص والإصلاح" }, deployments: { en: "Deployments", ar: "عمليات النشر" }, history: { en: "Task History", ar: "سجل المهام" },
  stop: { en: "EMERGENCY STOP", ar: "إيقاف طارئ" }, stopped: { en: "All sessions revoked and targets disconnected", ar: "تم إلغاء كل الجلسات وفصل الأجهزة" },
  newTask: { en: "New task", ar: "مهمة جديدة" }, kind: { en: "Task type", ar: "نوع المهمة" }, instruction: { en: "Instruction for Claude", ar: "التعليمات إلى Claude" },
  features: { en: "Feature status", ar: "حالة الميزات" }, mode: { en: "Execution mode", ar: "وضع التنفيذ" },
  modeCentral: { en: "Central Claude (this computer)", ar: "Claude المركزي (هذا الجهاز)" }, modeRemote: { en: "Remote Claude (on the target)", ar: "Claude البعيد (على الجهاز الهدف)" },
  clients: { en: "Clients & sites", ar: "العملاء والمواقع" },
  fleet: { en: "Devices", ar: "الأجهزة" }, jobs: { en: "Jobs & automations", ar: "المهام والأتمتة" }, monitoring: { en: "Monitoring & alerts", ar: "المراقبة والتنبيهات" },
  scripts: { en: "Scripts", ar: "السكربتات" }, aitech: { en: "AI Technician", ar: "الفني الذكي" }, tickets: { en: "Tickets & SLA", ar: "التذاكر واتفاقية الخدمة" },
  team: { en: "Technicians & roles", ar: "الفنيون والصلاحيات" }, billing: { en: "Subscription", ar: "الاشتراك" }, orgaudit: { en: "Audit logs", ar: "سجلات التدقيق" }, orgsettings: { en: "Client settings", ar: "إعدادات العميل" },
  theme: { en: "Dark / light", ar: "داكن / فاتح" }, legacy: { en: "More tools", ar: "أدوات إضافية" },
  workers: { en: "AI Worker Stations", ar: "محطات العمل الذكية" },
  scripted: { en: "Scripted (rule-based, not AI)", ar: "مبرمج (قواعد ثابتة، ليس ذكاءً اصطناعياً)" },
  revoke: { en: "Revoke", ar: "إلغاء" }, download: { en: "Download PDF", ar: "تنزيل PDF" },
  mockBanner: { en: "Mock devices only — no real computer is connected yet", ar: "أجهزة تجريبية فقط — لم يتم ربط أي جهاز حقيقي بعد" },
};
let lang: Lang = ((): Lang => { try { return (localStorage.getItem("lang") as Lang) || "en"; } catch { return "en"; } })();
const subs = new Set<() => void>();
function apply() { if (typeof document === "undefined") return; document.documentElement.lang = lang; document.documentElement.dir = lang === "ar" ? "rtl" : "ltr"; }
export function setLang(l: Lang) { lang = l; try { localStorage.setItem("lang", l); } catch {} apply(); subs.forEach(f => f()); }
apply();
export const t = (k: string) => D[k]?.[lang] ?? k;
export function useLang() { return useSyncExternalStore(f => { subs.add(f); return () => subs.delete(f); }, () => lang); }
export const _dict = D;
