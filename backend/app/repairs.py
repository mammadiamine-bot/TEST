"""Reviewed repair workflows. The agent can only act through this library: typed templates with validated parameters, never free-form shell.

cls:
  safe        reversible or harmless, allowlisted -> may run by itself at autonomy level 2+ (and only when the owner enabled it)
  approval    changes the computer in a way people should sign off -> always needs the existing approval flow (four-eyes)
  disruptive  restarts or interrupts the user -> needs the typed "I APPROVE" confirmation, never automatic
A workflow's code is fixed in this file. Nothing the model writes or any imported document can add a workflow or widen a class.
"""
from __future__ import annotations

from dataclasses import dataclass, field

SAFE_SERVICES = {"Spooler", "Dnscache", "W32Time", "Audiosrv", "Themes", "Winmgmt", "LanmanWorkstation"}


@dataclass(frozen=True)
class Workflow:
    id: str
    title: str
    title_ar: str
    signature: tuple[str, ...]          # finding codes this repairs
    cls: str                             # safe | approval | disruptive
    steps: tuple[tuple[str, str], ...]   # (template or "internal:<name>", param-key taken from the finding or "")
    verify_kind: str | None              # evidence kind re-collected after the repair
    verify_metric: str | None            # metric that must improve (lower is better unless 'higher' is set)
    expected: str
    expected_ar: str
    rollback: str | None = None
    rollback_ar: str | None = None
    timeout: int = 120
    precondition: str = ""               # human-readable
    higher_is_better: bool = False
    targets: tuple[str, ...] = ("worker", "main")
    os: str = "Windows 10/11"
    notes: dict = field(default_factory=dict)


LIBRARY: dict[str, Workflow] = {w.id: w for w in [
    Workflow("temp_cleanup", "Remove old temporary files", "حذف الملفات المؤقتة القديمة", ("temp_large", "disk_full"), "safe", (("cleanup.temp", ""),), "disk.usage", "temp_mb",
             "Temporary files older than 3 days are removed; TEMP shrinks.", "تُحذف الملفات المؤقتة الأقدم من 3 أيام ويصغر مجلد TEMP.", None, None, 180,
             "Only files (not folders) older than 3 days in the service account's TEMP folder. Deleted temp files cannot be restored; they are caches by design."),
    Workflow("dns_flush", "Flush the DNS cache", "مسح ذاكرة DNS", ("dns_fail", "dns_slow"), "safe", (("dns.flush", ""),), "net.deep", "dns_ms",
             "DNS lookups resolve again / become faster.", "تعود استعلامات DNS للعمل أو تصبح أسرع.", "None needed: the cache refills by itself.", "لا حاجة: تمتلئ الذاكرة تلقائياً.", 60, "Windows 10/11 with the DNS Client service running."),
    Workflow("service_start_safe", "Start a stopped Windows service", "تشغيل خدمة ويندوز متوقفة", ("svc_stopped",), "safe", (("service.start", "name"),), "services.list", "stopped_services",
             "The service reports Running.", "تصبح حالة الخدمة «تعمل».", "Stop the service again (service.stop).", "أوقف الخدمة مجدداً.", 60, "Only for services in the allowlist (Print Spooler, DNS Client, Windows Time, Audio, Themes, WMI, Workstation)."),
    Workflow("service_start_approved", "Start a stopped Windows service (needs approval)", "تشغيل خدمة متوقفة (يحتاج موافقة)", ("svc_stopped",), "approval", (("service.start", "name"),), "services.list", "stopped_services",
             "The service reports Running.", "تصبح حالة الخدمة «تعمل».", "Stop the service again (service.stop).", "أوقف الخدمة مجدداً.", 60, "Services outside the safe allowlist."),
    Workflow("app_close", "Close the application that is using too many resources", "إغلاق التطبيق المستهلك للموارد", ("mem_hog", "cpu_hog", "mem_pressure"), "approval", (("process.stop", "name"),), "perf.snapshot", "mem_used_pct",
             "The application is closed and memory/CPU drops.", "يُغلق التطبيق وينخفض استهلاك الذاكرة/المعالج.", "Start the application again; unsaved work in it is lost.", "شغّل التطبيق مجدداً؛ العمل غير المحفوظ فيه يضيع.", 90,
             "Asks the application to close first (so it can save), then ends it. System, security and CIT processes are protected and never stopped."),
    Workflow("wu_services", "Restart the Windows Update services", "إعادة تشغيل خدمات تحديث ويندوز", ("wu_errors",), "approval", (("service.restart", "=bits"), ("service.restart", "=wuauserv")), "health.windows", "update_errors",
             "Update services are running again; stuck updates can retry.", "تعمل خدمات التحديث من جديد وتُعيد التحديثات العالقة المحاولة.", "Services restart on their own; nothing to undo.", "تعيد الخدمات التشغيل بنفسها.", 120,
             "Does not reset the SoftwareDistribution folder. Whether the update then installs is only known after the next update attempt."),
    Workflow("reboot", "Restart the computer", "إعادة تشغيل الحاسوب", ("reboot_pending", "long_uptime"), "disruptive", (("system.reboot", ""),), None, None,
             "The computer restarts after a 60 second warning.", "يعاد تشغيل الحاسوب بعد تحذير 60 ثانية.", "A scheduled restart can be aborted with: shutdown /a within 60 seconds.", "يمكن إلغاؤه خلال 60 ثانية.", 90,
             "Interrupts the user. Disconnects the Worker until it boots."),
    Workflow("worker_reconnect", "Reconnect the CIT Worker", "إعادة اتصال وكيل CIT", ("worker_unreachable", "worker_degraded"), "safe", (("internal:worker.reconnect", ""),), None, None,
             "The controller probes the Worker immediately and resets its back-off.", "يفحص المتحكم الوكيل فوراً ويعيد ضبط فترة الانتظار.", None, None, 30, "Controller-side only; changes nothing on the computer.", targets=("worker",)),
    Workflow("adb_restart", "Restart the ADB server", "إعادة تشغيل خادم ADB", ("adb_no_devices", "adb_offline"), "safe", (("internal:adb.restart", ""),), None, None,
             "adb kill-server then start-server; the phone is detected again.", "يعاد تشغيل خادم ADB ويظهر الهاتف مجدداً.", "None needed.", "لا حاجة.", 40, "ADB must be installed on that computer. Does not touch the phone.", targets=("main", "android")),
]}
BY_SIGNATURE: dict[str, list[str]] = {}
for _w in LIBRARY.values():
    for _c in _w.signature:
        BY_SIGNATURE.setdefault(_c, []).append(_w.id)


def candidates(finding: dict, target_kind: str) -> list[Workflow]:
    """Workflows that repair this finding on this kind of target, safest first. Service workflows are picked by service name."""
    code, p = finding.get("code"), finding.get("p", {})
    out = []
    for wid in BY_SIGNATURE.get(code, []):
        w = LIBRARY[wid]
        if target_kind not in w.targets:
            continue
        if code == "svc_stopped":
            is_safe = p.get("name") in SAFE_SERVICES
            if (w.id == "service_start_safe") != is_safe:
                continue
        if code == "mem_pressure" and w.id == "app_close" and not p.get("name"):
            continue
        if code in ("mem_pressure", "mem_hog", "cpu_hog") and not p.get("name"):
            continue
        out.append(w)
    return sorted(out, key=lambda w: ("safe", "approval", "disruptive").index(w.cls))


def step_params(w: Workflow, finding: dict) -> list[tuple[str, dict]] | None:
    """Concrete (template, params) list for a workflow and finding, or None when a needed parameter is missing.
    Parameters come from the validated evidence or fixed literals ("=bits"), never from model text."""
    steps = []
    for tpl, key in w.steps:
        if not key:
            steps.append((tpl, {}))
        elif key.startswith("="):
            steps.append((tpl, {"name": key[1:]}))
        else:
            v = finding.get("p", {}).get(key)
            if not v:
                return None
            steps.append((tpl, {"name": str(v)}))
    return steps


def evaluate(w: Workflow, before: dict | None, after: dict | None) -> tuple[str, str]:
    """('pass'|'fail'|'inconclusive', explanation) from the metric the workflow is expected to move."""
    if not w.verify_metric:
        return "inconclusive", "this workflow has no automatic outcome check"
    if after is None:
        return "inconclusive", "the check after the repair did not complete"
    a, b = after.get(w.verify_metric), (before or {}).get(w.verify_metric)
    if a is None:
        return "inconclusive", f"{w.verify_metric} could not be measured after the repair"
    if w.verify_metric == "stopped_services":
        return ("pass", "no critical service is stopped any more") if a == 0 else ("fail", f"{a} critical service(s) still stopped")
    if b is None:
        return "inconclusive", "no measurement from before the repair to compare with"
    better = a > b if w.higher_is_better else a < b
    same = a == b
    if better:
        return "pass", f"{w.verify_metric}: {b} -> {a}"
    return ("inconclusive", f"{w.verify_metric} unchanged ({a})") if same else ("fail", f"{w.verify_metric} got worse: {b} -> {a}")
