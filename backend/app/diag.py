"""Deterministic diagnosis for the AI agent: intent -> playbook, evidence -> findings with weights, root-cause ranking, metrics for before/after.

The rules here are the trusted part of CIT's technician. A local model may explain the result or help classify an unclear request,
but it never decides what the evidence says. Each finding carries a 'weight' (how much it can explain a symptom, 0-100) and a 'cause'
group so the engine can rank likely root causes and say honestly when nothing explains the symptom.
"""
from __future__ import annotations

import re

from .aitech import F, analyse as base_analyse, localize as base_localize

ARABIC = re.compile(r"[؀-ۿ]")


def detect_lang(text: str, hint: str = "en") -> str:
    """Arabic when the request is mostly Arabic letters; otherwise the UI language hint."""
    letters = [c for c in text if c.isalpha()]
    if letters and sum(1 for c in letters if ARABIC.match(c)) / len(letters) > 0.4:
        return "ar"
    return "ar" if hint == "ar" and ARABIC.search(text) else ("ar" if hint == "ar" and not re.search(r"[A-Za-z]{4,}", text) else "en")


# ---- intent -> playbook ------------------------------------------------------------------------------------------------------------
INTENTS = [  # first match wins
    ("android", r"android|\badb\b|logcat|tablet|phone not|هاتف|اندرويد|أندرويد|جوال"),
    ("cit_worker", r"\bworker\b|\bcit\b|reconnect|وركر|العامل"),
    ("update", r"windows update|update error|updates? (fail|stuck)|wuauserv|تحديث|تحديثات"),
    ("network", r"internet|network|wi-?fi|\bdns\b|disconnect|ping|latency|الانترنت|الإنترنت|الشبكة|واي ?فاي|الاتصال|ينقطع"),
    ("crash", r"crash|keeps (closing|stopping)|not responding|انهيار|يتعطل|تعطل|يتوقف عن"),
    ("memory", r"\bram\b|memory|consuming|using too much|الذاكرة|رام|يستهلك"),
    ("disk", r"disk|storage|space|drive|القرص|مساحة|التخزين"),
    ("logs", r"\blogs?\b|event ?log|analy[sz]e|سجل|السجلات"),
    ("services", r"service|spooler|خدمة|الخدمات"),
    ("slow", r"slow|lag|freez|hang|performance|sluggish|بطيء|بطئ|بطيئ|تهنيج|يعلق|ثقيل|أداء"),
]
PLAYBOOKS: dict[str, dict] = {   # stages run in order; steps are (template, params). 'required' must all succeed before "healthy" may be claimed.
    "slow": {"stages": [[("perf.snapshot", {}), ("inventory.system", {}), ("startup.apps", {})], [("health.windows", {}), ("disk.usage", {}), ("eventlog.recent", {"log": "System", "hours": "24"})]], "required": ["perf.snapshot", "inventory.system"], "metrics": ["cpu_pct", "mem_used_pct", "disk_ms", "temp_mb", "free_gb_c", "startup_count"]},
    "memory": {"stages": [[("perf.snapshot", {}), ("startup.apps", {})], [("processes.top", {})]], "required": ["perf.snapshot"], "metrics": ["mem_used_pct", "mem_free_mb", "top_mem_mb", "cpu_pct"]},
    "network": {"stages": [[("net.deep", {}), ("network.diag", {})], [("health.windows", {})]], "required": ["net.deep"], "metrics": ["gw_loss_pct", "net_loss_pct", "dns_ms", "wifi_signal", "net_ms"]},
    "update": {"stages": [[("health.windows", {}), ("windows.update.status", {})], [("services.list", {}), ("eventlog.recent", {"log": "System", "hours": "72"})]], "required": ["health.windows", "windows.update.status"], "metrics": ["update_errors", "reboot_pending"]},
    "crash": {"stages": [[("health.windows", {}), ("eventlog.recent", {"log": "Application", "hours": "72"})], [("perf.snapshot", {})]], "required": ["health.windows"], "metrics": ["crash_count", "mem_used_pct"]},
    "disk": {"stages": [[("inventory.system", {}), ("disk.usage", {})], [("perf.snapshot", {})]], "required": ["inventory.system", "disk.usage"], "metrics": ["free_gb_c", "temp_mb", "disk_ms"]},
    "logs": {"stages": [[("eventlog.recent", {"log": "System", "hours": "48"}), ("eventlog.recent", {"log": "Application", "hours": "48"})], [("health.windows", {})]], "required": ["eventlog.recent"], "metrics": ["crash_count"]},
    "services": {"stages": [[("services.list", {}), ("eventlog.recent", {"log": "System", "hours": "24"})]], "required": ["services.list"], "metrics": ["stopped_services"]},
    "general": {"stages": [[("perf.snapshot", {}), ("inventory.system", {}), ("health.windows", {})], [("services.list", {}), ("net.deep", {})]], "required": ["perf.snapshot", "inventory.system"], "metrics": ["cpu_pct", "mem_used_pct", "free_gb_c", "stopped_services"]},
    "android": {"stages": [], "required": [], "metrics": []},
    "cit_worker": {"stages": [], "required": [], "metrics": []},
}


def classify(text: str) -> str:
    t = text.lower()
    for name, rx in INTENTS:
        if re.search(rx, t):
            return name
    return "general"


# ---- evidence -> findings -------------------------------------------------------------------------------------------------------------
SEV_W = {"critical": 80, "warning": 50, "info": 20}


def _pct(a, b):
    return round(100 * a / b) if b else 0


def mem_used_pct(d: dict) -> int:
    return _pct((d.get("mem_total_mb") or 0) - (d.get("mem_free_mb") or 0), d.get("mem_total_mb") or 0)


def W(f: dict, weight: int, cause: str, **extra) -> dict:
    return {**f, "weight": weight, "cause": cause, **extra}


def analyse_all(kind: str, data) -> list[dict]:
    """Findings for one piece of evidence. Base rules (from aitech) are kept; agent rules add performance, health and network depth."""
    out: list[dict] = []
    for f in base_analyse(kind, data):
        if f["code"] in ("temp_large", "many_events"):
            continue        # replaced below: a lower TEMP threshold, and event counts that ignore well-known harmless noise
        cause = {"disk_full": "disk_space", "svc_stopped": "services", "no_internet": "network", "dns_fail": "dns", "many_events": "errors", "sleep": "power"}.get(f["code"], "other")
        out.append(W(f, {"disk_full": 65, "no_internet": 90, "dns_fail": 75, "svc_stopped": 55}.get(f["code"], SEV_W.get(f["severity"], 20)), cause))
    try:
        if kind == "perf.snapshot" and isinstance(data, dict):
            used, free, tot = mem_used_pct(data), data.get("mem_free_mb") or 0, data.get("mem_total_mb") or 0
            top = (data.get("top_mem") or [{}])[0]
            if tot and used >= 85:
                out.append(W(F("mem_pressure", "critical" if used >= 92 else "warning", "Memory is almost exhausted", f"{free} MB free of {tot} MB ({used}% used); largest: {top.get('ProcessName')} {top.get('mem_mb')} MB", "Close the largest application or add RAM.",
                               used=used, free=free, total=tot, name=top.get("ProcessName"), mb=top.get("mem_mb")), 90 if used >= 92 else 70, "memory"))
            for p in (data.get("top_mem") or [])[:2]:
                if tot and p.get("mem_mb", 0) / tot > 0.25 and (p.get("ProcessName") or "").lower() not in ("system", "memcompression", "registry"):
                    out.append(W(F("mem_hog", "warning", f"{p['ProcessName']} uses {round(100 * p['mem_mb'] / tot)}% of the memory", f"{p['mem_mb']} MB (pid {p['Id']})", "Save your work and close it, or restart it.", name=p["ProcessName"], mb=p["mem_mb"], pid=p["Id"], pct=round(100 * p["mem_mb"] / tot)), 60, "memory",
                                 proposal_hint="app_close"))
            cpu = data.get("cpu_pct") or 0
            hogs = [p for p in (data.get("top_cpu") or []) if (p.get("ProcessName") or "").lower() not in ("powershell", "pwsh", "idle", "system", "conhost")]
            if cpu >= 75:
                h = hogs[0] if hogs else {}
                out.append(W(F("cpu_high", "critical" if cpu >= 90 else "warning", "The processor is overloaded", f"CPU {cpu}% (3 samples); busiest: {h.get('ProcessName')} {h.get('cpu_pct')}%", "Find out why the busiest application is working so hard.", cpu=cpu, name=h.get("ProcessName"), pct=h.get("cpu_pct")), 85 if cpu >= 90 else 60, "cpu"))
            if hogs and hogs[0].get("cpu_pct", 0) >= 35:
                out.append(W(F("cpu_hog", "warning", f"{hogs[0]['ProcessName']} is using {hogs[0]['cpu_pct']}% of the processor", f"pid {hogs[0]['Id']}", "Check whether it is stuck; closing it may help.", name=hogs[0]["ProcessName"], pct=hogs[0]["cpu_pct"], pid=hogs[0]["Id"]), 55, "cpu", proposal_hint="app_close"))
            if (data.get("disk_ms") or 0) >= 30 or (data.get("disk_queue") or 0) >= 2:
                out.append(W(F("disk_slow", "warning", "The disk is slow to respond", f"average {data.get('disk_ms')} ms per request, queue {data.get('disk_queue')}", "Check for a failing disk or a heavy background task.", ms=data.get("disk_ms"), queue=data.get("disk_queue")), 55, "disk_speed"))
            if (data.get("uptime_hours") or 0) > 24 * 14:
                out.append(W(F("long_uptime", "info", f"The computer has not restarted for {round(data['uptime_hours'] / 24)} days", "long uptime accumulates memory leaks", "A restart at a convenient time helps.", days=round(data["uptime_hours"] / 24)), 25, "uptime", proposal_hint="reboot"))
        elif kind == "eventlog.recent" and isinstance(data, list):
            groups: dict = {}
            for ev in data:
                key = (ev.get("ProviderName"), ev.get("Id"))
                if key not in BENIGN_EVENTS:
                    groups[key] = groups.get(key, 0) + 1
            if groups:
                (prov, eid), n = max(groups.items(), key=lambda kv: kv[1])
                if n >= 8:
                    out.append(W(F("event_storm", "warning", f"{prov} reported error {eid} {n} times", f"{n} of {len(data)} recent events; Windows keeps logging this", "Search this provider/ID; a failing driver or service often repeats like this.", provider=prov, eid=eid, n=n), 45, "errors"))
        elif kind == "startup.apps" and isinstance(data, dict) and (data.get("count") or 0) > 15:
            names = ", ".join(i.get("Name", "?") for i in (data.get("items") or [])[:6])
            out.append(W(F("startup_many", "info", f"{data['count']} programs start with Windows", names, "Disable the ones you do not need in Task Manager > Startup (CIT does not change startup items).", n=data["count"], names=names), 30, "startup"))
        elif kind == "disk.usage" and isinstance(data, dict) and (data.get("temp_mb") or 0) >= 500:
            out.append(W(F("temp_large", "info", "Large temporary-files folder", f"{data['temp_mb']} MB in TEMP", "Files older than 3 days can be removed safely.", mb=data["temp_mb"]), 30 if data["temp_mb"] < 2000 else 45, "disk_space", proposal_hint="temp_cleanup"))
        elif kind == "health.windows" and isinstance(data, dict):
            if data.get("reboot_pending"):
                out.append(W(F("reboot_pending", "warning", "A restart is pending", "Windows reports pending changes that need a restart", "Restart at a convenient time (needs approval).", ), 35, "reboot", proposal_hint="reboot"))
            df = data.get("defender") or {}
            if df.get("present") and (df.get("realtime") is False or df.get("service") is False):
                out.append(W(F("defender_off", "warning", "Windows Defender real-time protection is off", f"service={df.get('service')} realtime={df.get('realtime')}", "Turn protection back on in Windows Security. CIT never changes security settings."), 45, "security"))
            if df.get("present") and (df.get("signature_age_days") or 0) > 7:
                out.append(W(F("defender_old", "info", "Defender definitions are out of date", f"{df.get('signature_age_days')} days old", "Run Windows Update."), 25, "security"))
            for d in data.get("bad_devices") or []:
                nm = d.get("Name") or d.get("Description") or d.get("PNPClass") or "unnamed device"
                out.append(W(F("driver_problem", "warning", f"Device problem: {nm}", f"error code {d.get('ConfigManagerErrorCode')}", "Reinstall or update this driver.", name=nm, code_=d.get("ConfigManagerErrorCode")), 40, "drivers"))
            for d in data.get("disks") or []:
                if str(d.get("HealthStatus")) not in ("Healthy", "0", "None", ""):
                    out.append(W(F("disk_unhealthy", "critical", f"Disk health is {d.get('HealthStatus')}", f"{d.get('FriendlyName')}", "Back up data now; the drive may be failing.", name=d.get("FriendlyName"), status=d.get("HealthStatus")), 85, "disk_health"))
            for fw in data.get("firewall") or []:
                if fw.get("Enabled") in (0, False, "False"):
                    out.append(W(F("firewall_off", "warning", f"Firewall is off for the {fw.get('Name')} profile", "Enabled=False", "Turn it on in Windows Security. CIT never changes firewall settings.", profile=fw.get("Name")), 40, "security"))
            by: dict[str, int] = {}
            for c in data.get("crashes") or []:
                by[c.get("app") or "?"] = by.get(c.get("app") or "?", 0) + 1
            for app_, n in sorted(by.items(), key=lambda x: -x[1])[:3]:
                if n >= 2:
                    out.append(W(F("app_crash", "warning", f"{app_} crashed or hung {n} times in 72 hours", "Application Error / Application Hang events", "Update or reinstall the application.", app=app_, n=n), 45, "crashes"))
            if len(data.get("update_errors") or []) >= 1:
                n = len(data["update_errors"])
                out.append(W(F("wu_errors", "warning", f"Windows Update reported {n} error(s) this week", (data["update_errors"][0].get("msg") or "")[:160], "Restarting the update services often clears stuck updates.", n=n), 40, "updates", proposal_hint="wu_services"))
        elif kind == "net.deep" and isinstance(data, dict):
            gl = round(100 * (1 - (data.get("gw_recv") or 0) / (data.get("gw_sent") or 4))) if data.get("gateway") else None
            nl = round(100 * (1 - (data.get("net_recv") or 0) / 4))
            if data.get("gateway") is None:
                out.append(W(F("no_gateway", "critical", "No default gateway", "the computer has no route to the router", "Check the Wi-Fi or cable connection."), 90, "network"))
            elif gl and gl > 0:
                out.append(W(F("gw_loss", "critical" if gl >= 50 else "warning", f"{gl}% packet loss to the router", f"{data.get('gw_recv')}/{data.get('gw_sent')} replies from {data.get('gateway')}", "The local link is unstable: weak Wi-Fi, cable or router.", loss=gl, gw=data.get("gateway")), 80 if gl >= 50 else 60, "network_local"))
            elif nl > 0:
                out.append(W(F("inet_loss", "warning", f"{nl}% packet loss to the internet", f"{data.get('net_recv')}/4 replies from 1.1.1.1 (the router answers)", "The problem is beyond the router: the internet provider or the router uplink.", loss=nl), 65, "network_isp"))
            if (data.get("net_ms") or 0) > 150 or (data.get("gw_ms") or 0) > 100:
                out.append(W(F("latency_high", "warning", "Network latency is high", f"router {data.get('gw_ms')} ms, internet {data.get('net_ms')} ms", "Wi-Fi congestion or a busy line.", gw_ms=data.get("gw_ms"), net_ms=data.get("net_ms")), 45, "network_local"))
            if data.get("dns_resolves") is False:
                out.append(W(F("dns_fail", "critical", "DNS resolution fails", "example.com did not resolve", "Flush the DNS cache; if it persists check the DNS servers.", ), 80, "dns", proposal_hint="dns_flush"))
            elif (data.get("dns_ms") or 0) > 800:
                out.append(W(F("dns_slow", "warning", "DNS lookups are slow", f"{data['dns_ms']} ms for example.com", "Flushing the cache can help; otherwise try other DNS servers.", ms=data["dns_ms"]), 50, "dns", proposal_hint="dns_flush"))
            if data.get("wifi_signal") is not None and data["wifi_signal"] < 40:
                out.append(W(F("wifi_weak", "warning", f"Wi-Fi signal is weak ({data['wifi_signal']}%)", "signal below 40%", "Move closer to the router or use a cable.", signal=data["wifi_signal"]), 55, "network_local"))
            if data.get("proxy_enabled"):
                out.append(W(F("proxy_on", "info", "A proxy server is configured", str(data.get("proxy")), "Make sure the proxy is intended.", proxy=str(data.get("proxy"))), 20, "network_config"))
            if len(data.get("events") or []) >= 5:
                out.append(W(F("net_events", "info", f"{len(data['events'])} network errors in the last 24 hours", (data["events"][0] or {}).get("provider", ""), "Review adapter and DNS-client events.", n=len(data["events"])), 25, "network_local"))
    except (KeyError, TypeError, ZeroDivisionError, ValueError):
        out.append(W(F("unreadable", "info", f"Could not interpret {kind}", "unexpected data shape"), 5, "other"))
    return out


def metrics(kind: str, data) -> dict:
    """Flat numbers for the before/after comparison. Only keys the evidence really provides are returned."""
    m: dict = {}
    try:
        if kind == "perf.snapshot" and isinstance(data, dict):
            m = {"cpu_pct": data.get("cpu_pct"), "mem_used_pct": mem_used_pct(data), "mem_free_mb": data.get("mem_free_mb"), "disk_ms": data.get("disk_ms"), "top_mem_mb": ((data.get("top_mem") or [{}])[0]).get("mem_mb"), "top_mem_name": ((data.get("top_mem") or [{}])[0]).get("ProcessName")}
        elif kind == "inventory.system" and isinstance(data, dict):
            c = next((d for d in data.get("disks") or [] if str(d.get("DeviceID")).upper().startswith("C")), None)
            if c:
                m["free_gb_c"] = c.get("free_gb")
        elif kind == "disk.usage" and isinstance(data, dict):
            m["temp_mb"] = data.get("temp_mb")
        elif kind == "startup.apps" and isinstance(data, dict):
            m["startup_count"] = data.get("count")
        elif kind == "net.deep" and isinstance(data, dict):
            m = {"gw_loss_pct": round(100 * (1 - (data.get("gw_recv") or 0) / (data.get("gw_sent") or 4))) if data.get("gateway") else None,
                 "net_loss_pct": round(100 * (1 - (data.get("net_recv") or 0) / 4)), "dns_ms": data.get("dns_ms"), "wifi_signal": data.get("wifi_signal"), "net_ms": data.get("net_ms")}
        elif kind == "health.windows" and isinstance(data, dict):
            m = {"crash_count": len(data.get("crashes") or []), "update_errors": len(data.get("update_errors") or []), "reboot_pending": int(bool(data.get("reboot_pending")))}
        elif kind == "services.list" and isinstance(data, list):
            from .aitech import CRITICAL_SERVICES
            m["stopped_services"] = sum(1 for s in data if s.get("Name") in CRITICAL_SERVICES and str(s.get("StartType")) in ("2", "Automatic") and str(s.get("Status")) in ("1", "Stopped"))
    except (KeyError, TypeError):
        pass
    return {k: v for k, v in m.items() if v is not None}


# Windows logs these constantly and they do not hurt anything (DCOM permission noise)
BENIGN_EVENTS = {("Microsoft-Windows-DistributedCOM", 10016), ("Microsoft-Windows-DistributedCOM", 10010), ("Microsoft-Windows-Kernel-PnP", 219)}
SYMPTOM_INTENTS = {"slow", "network", "crash", "memory"}
MIN_CAUSE_WEIGHT = 45     # a symptom is only "explained" by something that can actually produce it; weaker findings stay as observations


# the symptom a playbook is trying to explain: which cause groups count as a real explanation
EXPLAINS = {
    "slow": {"memory", "cpu", "disk_speed", "disk_space", "disk_health", "startup", "uptime", "errors", "crashes", "services", "reboot"},
    "memory": {"memory", "startup", "uptime"},
    "network": {"network", "network_local", "network_isp", "dns", "network_config", "power"},
    "update": {"updates", "reboot", "services", "disk_space"},
    "crash": {"crashes", "memory", "drivers", "errors", "disk_health"},
    "disk": {"disk_space", "disk_health", "disk_speed"},
    "logs": {"errors", "crashes", "updates", "drivers", "services"},
    "services": {"services", "errors"},
    "general": None,
}


def rank(findings: list[dict], intent: str) -> list[dict]:
    """Likely root causes: strongest first. Advice-only findings (weight<=30) are listed but are not called a cause."""
    ex = EXPLAINS.get(intent)
    floor = MIN_CAUSE_WEIGHT if intent in SYMPTOM_INTENTS else 30
    cands = [f for f in findings if f.get("weight", 0) >= floor and (ex is None or f.get("cause") in ex)]
    cands.sort(key=lambda f: (-f["weight"], f["code"]))
    seen, out = set(), []
    for f in cands:
        k = (f["code"], f.get("p", {}).get("name") or f.get("p", {}).get("drive"))
        if k not in seen:
            seen.add(k); out.append({**f, "confidence": round(min(0.95, f["weight"] / 100 * 1.05), 2)})
    return out


# ---- Arabic ---------------------------------------------------------------------------------------------------------------------------
AR2 = {
    "mem_pressure": ("الذاكرة على وشك النفاد", "{free} ميجابايت متاحة من {total} ({used}% مستخدمة)؛ الأكبر: {name} {mb} ميجابايت", "أغلق التطبيق الأكبر أو زد الذاكرة."),
    "mem_hog": ("{name} يستخدم {pct}% من الذاكرة", "{mb} ميجابايت (المعرّف {pid})", "احفظ عملك ثم أغلقه أو أعد تشغيله."),
    "cpu_high": ("المعالج مثقل بالعمل", "المعالج {cpu}%؛ الأكثر نشاطاً: {name} {pct}%", "اعرف سبب انشغال هذا التطبيق."),
    "cpu_hog": ("{name} يستهلك {pct}% من المعالج", "المعرّف {pid}", "تحقق إن كان عالقاً؛ قد يفيد إغلاقه."),
    "disk_slow": ("القرص بطيء الاستجابة", "المتوسط {ms} مللي ثانية لكل طلب، الطابور {queue}", "افحص وجود قرص معطوب أو مهمة خلفية ثقيلة."),
    "long_uptime": ("لم يُعاد تشغيل الحاسوب منذ {days} يوماً", "التشغيل الطويل يراكم تسرّب الذاكرة", "أعد التشغيل في وقت مناسب."),
    "startup_many": ("{n} برنامجاً يبدأ مع ويندوز", "{names}", "عطّل غير الضروري من مدير المهام ← بدء التشغيل (لا يغيّر CIT عناصر بدء التشغيل)."),
    "event_storm": ("{provider} سجّل الخطأ {eid} عدد {n} مرة", "{evidence_msg}", "ابحث عن هذا المصدر؛ غالباً يتكرر هكذا بسبب برنامج تشغيل أو خدمة معطوبة."),
    "temp_large": ("مجلد الملفات المؤقتة كبير", "{mb} ميجابايت في TEMP", "يمكن حذف الملفات الأقدم من 3 أيام بأمان."),
    "reboot_pending": ("إعادة التشغيل معلّقة", "يبلغ ويندوز عن تغييرات تحتاج إلى إعادة تشغيل", "أعد التشغيل في وقت مناسب (يحتاج موافقة)."),
    "defender_off": ("الحماية الفورية في Windows Defender متوقفة", "الخدمة={service}", "فعّلها من أمن ويندوز. لا يغيّر CIT إعدادات الأمان."),
    "defender_old": ("تعريفات Defender قديمة", "{days} يوماً", "شغّل تحديث ويندوز."),
    "driver_problem": ("مشكلة في جهاز: {name}", "رمز الخطأ {code_}", "أعد تثبيت أو حدّث برنامج التشغيل."),
    "disk_unhealthy": ("حالة القرص: {status}", "{name}", "انسخ بياناتك احتياطياً الآن؛ قد يكون القرص يتعطل."),
    "firewall_off": ("جدار الحماية متوقف للملف {profile}", "Enabled=False", "فعّله من أمن ويندوز. لا يغيّر CIT إعدادات الجدار الناري."),
    "app_crash": ("تعطل {app} أو توقف {n} مرات خلال 72 ساعة", "أحداث Application Error / Application Hang", "حدّث التطبيق أو أعد تثبيته."),
    "wu_errors": ("أبلغ تحديث ويندوز عن {n} خطأ هذا الأسبوع", "{evidence_msg}", "إعادة تشغيل خدمات التحديث تحل غالباً التحديث العالق."),
    "no_gateway": ("لا توجد بوابة افتراضية", "لا يوجد مسار إلى جهاز التوجيه", "افحص الواي فاي أو الكابل."),
    "gw_loss": ("فقدان {loss}% من الحزم إلى جهاز التوجيه", "الاتصال المحلي غير مستقر", "إشارة واي فاي ضعيفة أو كابل أو جهاز توجيه."),
    "inet_loss": ("فقدان {loss}% من الحزم إلى الإنترنت", "جهاز التوجيه يرد، لكن الإنترنت لا يرد", "المشكلة بعد جهاز التوجيه: مزود الخدمة."),
    "latency_high": ("زمن الاستجابة للشبكة مرتفع", "جهاز التوجيه {gw_ms} مللي ثانية، الإنترنت {net_ms} مللي ثانية", "ازدحام الواي فاي أو خط مشغول."),
    "dns_fail": ("فشل تحليل أسماء DNS", "لم يتم تحليل example.com", "امسح ذاكرة DNS؛ وإن استمر فافحص خوادم DNS."),
    "dns_slow": ("استعلامات DNS بطيئة", "{ms} مللي ثانية", "قد يفيد مسح الذاكرة المؤقتة؛ أو جرّب خوادم DNS أخرى."),
    "wifi_weak": ("إشارة الواي فاي ضعيفة ({signal}%)", "أقل من 40%", "اقترب من جهاز التوجيه أو استخدم كابلاً."),
    "proxy_on": ("هناك خادم وسيط (Proxy) مضبوط", "{proxy}", "تأكد أنه مقصود."),
    "net_events": ("{n} أخطاء شبكة خلال 24 ساعة", "", "راجع أحداث المحوّل وDNS."),
}
CAUSE_AR = {"memory": "ضغط الذاكرة", "cpu": "حمل المعالج", "disk_speed": "بطء القرص", "disk_space": "امتلاء القرص", "disk_health": "صحة القرص", "startup": "كثرة برامج بدء التشغيل", "uptime": "التشغيل الطويل", "errors": "أخطاء النظام",
            "crashes": "تعطل التطبيقات", "services": "خدمة متوقفة", "reboot": "إعادة تشغيل معلّقة", "network": "الشبكة", "network_local": "الاتصال المحلي (واي فاي/كابل/جهاز التوجيه)", "network_isp": "مزود الإنترنت", "dns": "DNS",
            "updates": "تحديثات ويندوز", "drivers": "برامج التشغيل", "security": "الأمان", "power": "إعدادات الطاقة", "other": "أخرى"}


def localize(f: dict, lang: str) -> dict:
    if lang != "ar":
        return f
    if f.get("code") in AR2:
        t, e, n = AR2[f["code"]]
        p = {**f.get("p", {}), "evidence_msg": f.get("evidence", ""), "days": f.get("p", {}).get("days", ""), "service": f.get("evidence", "")}
        try:
            return {**f, "title": t.format(**p), "evidence": e.format(**p) if e else f["evidence"], **({"next": n.format(**p)} if n else {})}
        except (KeyError, IndexError):
            return f
    return base_localize(f, lang)
