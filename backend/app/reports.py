from __future__ import annotations

import time
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

SEV_COLOR = {"critical": "#b91c1c", "high": "#c2410c", "medium": "#a16207", "low": "#1d4ed8", "info": "#475569"}


def _esc(s: str) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_data(device: dict, run: dict, plan: dict | None, notes: list[dict]) -> dict:
    return {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "device": device, "run": run, "plan": plan, "notes": notes,
            "analysis_source": run.get("source", "scripted")}


def write_pdf(data: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    st = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(path), pagesize=A4, title="Claude IT Technician report")
    el = [Paragraph("Claude IT Technician — Diagnostic report", st["Title"]),
          Paragraph(f"Generated {data['generated']} · analysis source: <b>{data['analysis_source']}</b> (rule-based; not AI)", st["Normal"]), Spacer(1, 10)]
    d = data["device"]
    el += [Paragraph(f"Device: <b>{_esc(d['name'])}</b> · owner: {_esc(d['owner_name'])} · transport: {d['transport']}", st["Normal"]), Spacer(1, 10)]

    def table(findings):
        rows = [["Severity", "Finding", "Evidence"]] + [[f["severity"].upper(), Paragraph(_esc(f["title"]), st["BodyText"]), Paragraph(_esc(f["evidence"])[:300], st["BodyText"])] for f in findings] \
            or [["—", "No findings", ""]]
        t = Table(rows, colWidths=[60, 190, 240], repeatRows=1)
        style = [("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0b1f4a")),
                 ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("VALIGN", (0, 0), (-1, -1), "TOP")]
        for i, f in enumerate(findings, 1):
            style.append(("TEXTCOLOR", (0, i), (0, i), colors.HexColor(SEV_COLOR[f["severity"]])))
        t.setStyle(TableStyle(style))
        return t

    el += [Paragraph("Findings before repair", st["Heading2"]), table(data["run"]["findings"])]
    p = data["plan"]
    if p:
        el += [Spacer(1, 10), Paragraph("Repair actions", st["Heading2"])]
        rows = [["Step", "Risk", "Approved by", "Outcome"]]
        for s in p["steps"]:
            res = s["result"] or {}
            outcome = {"verified": "VERIFIED", "unverified": "RAN – NOT VERIFIED", "failed": "FAILED", "blocked": "BLOCKED"}.get(s["status"], s["status"].upper())
            rows.append([Paragraph(_esc(s["title"]), st["BodyText"]), s["risk"], s["approved_by"] or "—", Paragraph(_esc(f"{outcome}: {res.get('detail','')}"), st["BodyText"])])
        t = Table(rows, colWidths=[150, 60, 80, 200], repeatRows=1)
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0b1f4a")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white)]))
        el.append(t)
        if p.get("after"):
            el += [Spacer(1, 10), Paragraph("Findings after repair (re-scanned)", st["Heading2"]), table(p["after"]["findings"])]
    if data["notes"]:
        el += [Spacer(1, 10), Paragraph("Technician notes", st["Heading2"])] + [Paragraph(f"{_esc(n['author'])}: {_esc(n['body'])}", st["Normal"]) for n in data["notes"]]
    doc.build(el)
    return path
