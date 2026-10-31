import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { api, apiStream } from "./api";
import { DataTable, Empty, Err, NoOrg, OrgBar, Tabs, isSenior, useAct, useData, useMe, useOrgId, when } from "./kit";
import { getLang, reg, t, useLang } from "./i18n";
import { registerSection } from "./registry";
import { registerWorkspaceTab } from "./views1";

reg({
  "ai.tabChat": { en: "Chat", ar: "المحادثة" }, "ai.tabDiag": { en: "Diagnose a computer", ar: "تشخيص حاسوب" },
  "ai.new": { en: "New chat", ar: "محادثة جديدة" }, "ai.history": { en: "Conversations", ar: "المحادثات" }, "ai.delete": { en: "Delete", ar: "حذف" }, "ai.confirmDelete": { en: "Delete this conversation?", ar: "حذف هذه المحادثة؟" },
  "ai.host": { en: "AI host", ar: "مضيف الذكاء الاصطناعي" }, "ai.model": { en: "Model", ar: "النموذج" }, "ai.context": { en: "Device context (optional)", ar: "سياق الجهاز (اختياري)" }, "ai.noContext": { en: "None", ar: "بدون" },
  "ai.online": { en: "online", ar: "متصل" }, "ai.offline": { en: "unavailable", ar: "غير متاح" }, "ai.noModels": { en: "No Qwen/Ollama model found on this host. Install one there with Ollama.", ar: "لم يُعثر على نموذج Qwen/Ollama على هذا المضيف. ثبّت واحداً هناك عبر Ollama." },
  "ai.placeholder": { en: "Ask in Arabic or English…", ar: "اسأل بالعربية أو الإنجليزية…" }, "ai.send": { en: "Send", ar: "إرسال" }, "ai.stop": { en: "Stop", ar: "إيقاف" }, "ai.thinking": { en: "Thinking…", ar: "جارٍ التفكير…" },
  "ai.copy": { en: "Copy", ar: "نسخ" }, "ai.copied": { en: "Copied", ar: "تم النسخ" }, "ai.stats": { en: "{s} s · {n} tokens", ar: "{s} ث · {n} رمز" }, "ai.stopped": { en: "(stopped)", ar: "(أُوقف)" },
  "ai.empty": { en: "Ask anything about Windows, Android, PowerShell, scripts or CIT. This chat only talks; to inspect a computer use “Diagnose a computer”.", ar: "اسأل عن ويندوز أو أندرويد أو PowerShell أو السكربتات أو CIT. هذه المحادثة للكلام فقط؛ لفحص حاسوب استخدم «تشخيص حاسوب»." },
  "ai.err": { en: "The AI host did not answer: {e}", ar: "لم يستجب مضيف الذكاء الاصطناعي: {e}" },
  "ai.dPick": { en: "Computer", ar: "الحاسوب" }, "ai.dAsk": { en: "What is the problem?", ar: "ما المشكلة؟" }, "ai.dPlaceholder": { en: "e.g. The computer is slow since this morning", ar: "مثال: الحاسوب بطيء منذ هذا الصباح" }, "ai.dRun": { en: "Diagnose", ar: "تشخيص" }, "ai.dRunning": { en: "Running read-only checks…", ar: "جارٍ تنفيذ فحوصات للقراءة فقط…" },
  "ai.dHow": { en: "Order of work: built-in checks first, then the local model. The assistant only reads. Anything it suggests becomes a request that a person must approve.", ar: "ترتيب العمل: الفحوصات المدمجة أولاً ثم النموذج المحلي. المساعد يقرأ فقط. أي اقتراح يتحول إلى طلب يجب أن يوافق عليه شخص." },
  "ai.dResult": { en: "Result", ar: "النتيجة" }, "ai.dEngine": { en: "engine", ar: "المحرك" }, "ai.dProposal": { en: "Proposed", ar: "مقترح" }, "ai.dNotRun": { en: "Not executed. Submitting creates a job that waits for approval.", ar: "لم يُنفَّذ. الإرسال ينشئ مهمة تنتظر الموافقة." },
  "ai.dSubmit": { en: "Submit for approval", ar: "إرسال للموافقة" }, "ai.dSubmitted": { en: "Submitted — approve it in “Jobs & Automation”.", ar: "تم الإرسال — وافق عليه من «المهام والأتمتة»." },
  "ai.dHist": { en: "Diagnosis history", ar: "سجل التشخيص" }, "ai.eng.deterministic": { en: "built-in checks", ar: "فحوصات مدمجة" }, "ai.eng.local-model": { en: "local model", ar: "نموذج محلي" }, "ai.tokensN": { en: "{n} tokens", ar: "{n} رمز" },
  "ai.dTaskHint": { en: "You can also ask for a task, for example “find report.docx and put it on the Desktop”. The assistant drafts a script for you to review; it runs only after another person approves it.", ar: "يمكنك أيضاً طلب مهمة، مثل «ابحث عن report.docx وضعه على سطح المكتب». يصيغ المساعد سكربتاً لتراجعه، ولا يعمل إلا بعد موافقة شخص آخر." }, "ai.dDraft": { en: "Drafted script", ar: "السكربت المقترح" }, "ai.dWhen": { en: "When", ar: "الوقت" }, "ai.dReq": { en: "Request", ar: "الطلب" }, "ai.dBy": { en: "By", ar: "بواسطة" }, "ai.dTokens": { en: "Tokens", ar: "الرموز" },
  "ws.ai": { en: "AI Assistant", ar: "المساعد الذكي" }, "ai.tabDraft": { en: "Draft a task script", ar: "صياغة سكربت لمهمة" },
});

// ---- tiny Markdown renderer (headings, lists, bold/italic, inline code, links, fenced code with copy + light highlighting). No HTML is ever injected.
const KW = /\b(function|return|const|let|var|if|else|for|foreach|while|import|from|def|class|try|catch|except|param|Get-[A-Za-z]+|Set-[A-Za-z]+|Start-[A-Za-z]+|Stop-[A-Za-z]+|Write-[A-Za-z]+|New-[A-Za-z]+|Remove-[A-Za-z]+|echo|dir|ipconfig|netsh|sfc|dism)\b/g;
function highlight(code: string): ReactNode[] {
  return code.split("\n").map((line, i) => { const parts: ReactNode[] = []; let last = 0; const m = [...line.matchAll(new RegExp(`(#.*$|//.*$)|("[^"]*"|'[^']*')|${KW.source}`, "g"))];
    for (const x of m) { if (x.index! > last) parts.push(line.slice(last, x.index)); const c = x[1] ? "var(--mut)" : x[2] ? "var(--ok)" : "var(--acc)"; parts.push(<span key={x.index} style={{ color: c, fontWeight: x[1] || x[2] ? 400 : 600 }}>{x[0]}</span>); last = x.index! + x[0].length; }
    parts.push(line.slice(last)); return <div key={i}>{parts.length ? parts : " "}</div>; });
}
function Inline({ s }: { s: string }) {
  const out: ReactNode[] = []; const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\n]+\*)|(\[[^\]]+\]\(https?:\/\/[^)\s]+\))/g; let last = 0, k = 0;
  for (const m of s.matchAll(re)) { if (m.index! > last) out.push(s.slice(last, m.index)); const x = m[0];
    if (m[1]) out.push(<code key={k++}>{x.slice(1, -1)}</code>); else if (m[2]) out.push(<b key={k++}>{x.slice(2, -2)}</b>); else if (m[3]) out.push(<i key={k++}>{x.slice(1, -1)}</i>);
    else { const mm = /\[([^\]]+)\]\((https?:[^)]+)\)/.exec(x)!; out.push(<a key={k++} href={mm[2]} target="_blank" rel="noreferrer noopener">{mm[1]}</a>); } last = m.index! + x.length; }
  out.push(s.slice(last)); return <>{out}</>;
}
function Code({ code }: { code: string }) {
  useLang(); const [ok, setOk] = useState(false);
  return <div style={{ position: "relative" }}><button className="btn ghost sm" style={{ position: "absolute", top: 6, insetInlineEnd: 6 }} onClick={() => { navigator.clipboard?.writeText(code); setOk(true); setTimeout(() => setOk(false), 1200); }}>{ok ? t("ai.copied") : t("ai.copy")}</button><pre style={{ paddingInlineEnd: 70 }}>{highlight(code)}</pre></div>;
}
export function Markdown({ text }: { text: string }) {
  const blocks: ReactNode[] = []; const lines = text.split("\n"); let i = 0, k = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (l.startsWith("```")) { const buf: string[] = []; i++; while (i < lines.length && !lines[i].startsWith("```")) buf.push(lines[i++]); i++; blocks.push(<Code key={k++} code={buf.join("\n")} />); continue; }
    const h = /^(#{1,4})\s+(.*)/.exec(l); if (h) { blocks.push(<div key={k++} style={{ fontWeight: 700, fontSize: 16 - h[1].length, margin: "8px 0 2px" }}><Inline s={h[2]} /></div>); i++; continue; }
    if (/^\s*([-*]|\d+\.)\s+/.test(l)) { const items: string[] = []; while (i < lines.length && /^\s*([-*]|\d+\.)\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*([-*]|\d+\.)\s+/, "")); blocks.push(<ul key={k++} style={{ margin: "4px 0", paddingInlineStart: 22 }}>{items.map((x, j) => <li key={j}><Inline s={x} /></li>)}</ul>); continue; }
    if (!l.trim()) { i++; continue; }
    blocks.push(<p key={k++} style={{ margin: "4px 0" }}><Inline s={l} /></p>); i++;
  }
  return <div dir="auto">{blocks}</div>;
}

/** Extra tabs of the AI page (the technician workspace, knowledge, models, autonomy) register themselves here from agent.tsx. */
export const aiTabs: { key: string; order: number; label: string; render: (p: { oid: string; presetWorker?: string }) => ReactNode }[] = [];

type Host = { host: string; name: string; models: string[]; online: boolean; error?: string };
type Msg = { role: string; content: string; ms?: number; tokens_in?: number; tokens_out?: number; stopped?: boolean };

function ChatTab({ oid, presetWorker }: { oid: string; presetWorker?: string }) {
  useLang(); const hosts = useData<Host[]>(`/orgs/${oid}/chat/hosts`, 30000); const convs = useData<any[]>(`/orgs/${oid}/chat/conversations`); const fleet = useData<{ sites: any[] }>(`/orgs/${oid}/fleet`);
  const [cid, setCid] = useState(""); const [msgs, setMsgs] = useState<Msg[]>([]); const [host, setHost] = useState("main"); const [model, setModel] = useState(""); const [ctx, setCtx] = useState(presetWorker ?? ""); const [text, setText] = useState(""); const [busy, setBusy] = useState(false); const [err, setErr] = useState("");
  const ac = useRef<AbortController | null>(null); const end = useRef<HTMLDivElement>(null); const workers: any[] = (fleet.data?.sites ?? []).flatMap(s => s.workers);
  const h = hosts.data?.find(x => x.host === host);
  useEffect(() => { if (h && !h.models.includes(model)) setModel(h.models.find(m => m.startsWith("qwen3")) ?? h.models.find(m => m.includes("qwen")) ?? h.models[0] ?? ""); }, [host, hosts.data]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs]);
  const open = async (id: string) => { setCid(id); setErr(""); const r = await api<any>(`/orgs/${oid}/chat/conversations/${id}`); setMsgs(r.messages); setHost(r.conversation.host); setModel(r.conversation.model); setCtx(r.conversation.worker_id ?? ""); };
  const reset = () => { setCid(""); setMsgs([]); setErr(""); };
  const send = useCallback(async () => {
    const content = text.trim(); if (!content || busy || !model) return; setText(""); setBusy(true); setErr("");
    try {
      let id = cid; if (!id) { const c = await api<any>(`/orgs/${oid}/chat/conversations`, "POST", { host, model, worker_id: ctx || null }); id = c.id; setCid(id); convs.reload(); }
      setMsgs(m => [...m, { role: "user", content }, { role: "assistant", content: "" }]);
      ac.current = new AbortController();
      const r = await apiStream(`/orgs/${oid}/chat/conversations/${id}/messages`, { content, model, lang: getLang() }, ac.current.signal);
      const rd = r.body!.getReader(); const dec = new TextDecoder(); let buf = "";
      for (;;) { const { value, done } = await rd.read(); if (done) break; buf += dec.decode(value, { stream: true }); let ix; while ((ix = buf.indexOf("\n\n")) >= 0) { const line = buf.slice(0, ix).replace(/^data: /, ""); buf = buf.slice(ix + 2); if (!line) continue; const ev = JSON.parse(line);
        if (ev.error) throw new Error(ev.error); if (ev.delta) setMsgs(m => { const c = m.slice(); c[c.length - 1] = { ...c[c.length - 1], content: c[c.length - 1].content + ev.delta }; return c; }); if (ev.done) setMsgs(m => { const c = m.slice(); c[c.length - 1] = { ...c[c.length - 1], ms: ev.ms, tokens_in: ev.tokens_in, tokens_out: ev.tokens_out }; return c; }); } }
    } catch (e: any) { if (e.name === "AbortError") setMsgs(m => { const c = m.slice(); if (c.length) c[c.length - 1] = { ...c[c.length - 1], stopped: true }; return c; }); else { setErr(t("ai.err", { e: e.message })); setMsgs(m => (m.length && m[m.length - 1].role === "assistant" && !m[m.length - 1].content ? m.slice(0, -1) : m)); } }
    finally { setBusy(false); ac.current = null; convs.reload(); }
  }, [text, busy, model, cid, host, ctx, oid]);
  return <div className="grid" style={{ gridTemplateColumns: "minmax(200px,260px) minmax(0,1fr)", alignItems: "start" }}>
    <div className="card"><div className="row"><h3 style={{ flex: 1, margin: 0 }}>{t("ai.history")}</h3><button className="btn sm" onClick={reset}>{t("ai.new")}</button></div>
      <div style={{ maxHeight: 460, overflow: "auto" }} className="mt">{(convs.data ?? []).map(c => <div key={c.id} className="row" style={{ marginBottom: 4 }}><button className={"btn sm grow " + (cid === c.id ? "" : "ghost")} style={{ justifyContent: "flex-start", overflow: "hidden", textOverflow: "ellipsis" }} onClick={() => open(c.id)} title={c.title}>{c.title}</button>
        <button className="btn ghost sm" aria-label={t("ai.delete")} title={t("ai.delete")} onClick={async () => { if (confirm(t("ai.confirmDelete"))) { await api(`/orgs/${oid}/chat/conversations/${c.id}`, "DELETE"); if (cid === c.id) reset(); convs.reload(); } }}>×</button></div>)}</div></div>
    <div className="card" style={{ display: "flex", flexDirection: "column", minHeight: 480 }}>
      <div className="row"><div style={{ minWidth: 180 }}><label>{t("ai.host")}</label><select value={host} disabled={!!cid} onChange={e => setHost(e.target.value)} aria-label={t("ai.host")}>{(hosts.data ?? []).map(x => <option key={x.host} value={x.host}>{x.name} · {x.online ? t("ai.online") : t("ai.offline")}</option>)}</select></div>
        <div style={{ minWidth: 200 }}><label>{t("ai.model")}</label><select className="ltr" value={model} onChange={e => setModel(e.target.value)} aria-label={t("ai.model")}>{(h?.models ?? []).map(m => <option key={m}>{m}</option>)}</select></div>
        <div style={{ minWidth: 200 }}><label>{t("ai.context")}</label><select value={ctx} disabled={!!cid} onChange={e => setCtx(e.target.value)} aria-label={t("ai.context")}><option value="">{t("ai.noContext")}</option>{workers.map(w => <option key={w.id} value={w.id}>{w.name}</option>)}</select></div></div>
      {h && !h.models.length && <div className="banner mt">{t("ai.noModels")}</div>}<Err m={err} />
      <div style={{ flex: 1, overflow: "auto", maxHeight: 460, margin: "10px 0" }}>{!msgs.length && <Empty title={t("ai.empty")} />}
        {msgs.map((m, i) => <div key={i} className={"msg " + (m.role === "user" ? "me" : "bot")} dir="auto">{m.role === "user" ? m.content : m.content ? <Markdown text={m.content} /> : <span className="tag">{t("ai.thinking")}</span>}
          {m.role === "assistant" && (m.ms || m.stopped) ? <div className="tag" style={{ marginTop: 4 }}>{m.ms ? t("ai.stats", { s: (m.ms / 1000).toFixed(1), n: (m.tokens_in ?? 0) + (m.tokens_out ?? 0) }) : ""} {m.stopped ? t("ai.stopped") : ""}</div> : null}</div>)}<div ref={end} /></div>
      <div className="row"><textarea className="grow" rows={2} dir="auto" aria-label={t("ai.placeholder")} placeholder={t("ai.placeholder")} value={text} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(); } }} />
        {busy ? <button className="btn danger" onClick={() => ac.current?.abort()}>{t("ai.stop")}</button> : <button className="btn" disabled={!text.trim() || !model} onClick={() => void send()}>{t("ai.send")}</button>}</div></div></div>;
}

function DiagnoseTab({ oid, presetWorker }: { oid: string; presetWorker?: string }) {
  useLang(); const fleet = useData<{ sites: any[] }>(`/orgs/${oid}/fleet`); const runs = useData<any[]>(`/orgs/${oid}/ai/runs`); const me = useMe(); const { err, busy, ok, go } = useAct();
  const [w, setW] = useState(presetWorker ?? ""); const [q, setQ] = useState(""); const [res, setRes] = useState<any>(null); const workers: any[] = (fleet.data?.sites ?? []).flatMap(s => s.workers);
  useEffect(() => { if (!w && workers[0]) setW(workers[0].id); }, [workers.length]);
  return <><div className="card"><div className="tag">{t("ai.dHow")}</div><div className="tag mt">{t("ai.dTaskHint")}</div><Err m={err} />
    <div className="row mt"><div style={{ minWidth: 220 }}><label>{t("ai.dPick")}</label><select value={w} onChange={e => setW(e.target.value)} aria-label={t("ai.dPick")}>{workers.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</select></div>
      <div className="grow" style={{ minWidth: 260 }}><label>{t("ai.dAsk")}</label><input dir="auto" placeholder={t("ai.dPlaceholder")} value={q} onChange={e => setQ(e.target.value)} onKeyDown={e => e.key === "Enter" && w && q.length > 2 && go(async () => { setRes(await api(`/orgs/${oid}/ai/ask`, "POST", { worker_id: w, request: q, lang: getLang() })); runs.reload(); })} /></div>
      <button className="btn" style={{ alignSelf: "end" }} disabled={busy || !w || q.length < 3 || me?.role === "viewer"} onClick={() => go(async () => { setRes(await api(`/orgs/${oid}/ai/ask`, "POST", { worker_id: w, request: q, lang: getLang() })); runs.reload(); })}>{busy ? t("ai.dRunning") : t("ai.dRun")}</button></div></div>
    {res && <div className="card mt fade"><div className="row"><h3 style={{ flex: 1, margin: 0 }}>{t("ai.dResult")}</h3><span className="pill">{t("ai.dEngine")}: {t("ai.eng." + res.engine)}{res.model ? " · " : ""}<span className="ltr">{res.model ?? ""}</span></span><span className="tag">{t("ai.tokensN", { n: res.tokens_in + res.tokens_out })}</span></div><div className="mt"><Markdown text={res.answer} /></div>
      {res.plan.proposals.map((p: any) => <div key={p.n} className="card mt"><b>{t("ai.dProposal")}: {p.finding}</b><pre>{p.preview.body}</pre><div className="tag">{t("ai.dNotRun")}</div>
        <button className="btn mt" disabled={busy || me?.role === "viewer"} onClick={() => go(async () => { await api(`/orgs/${oid}/ai/runs/${res.id}/proposals/${p.n}/submit`, "POST"); }, t("ai.dSubmitted"))}>{t("ai.dSubmit")}</button></div>)}{ok && <div className="banner mt">{ok}</div>}</div>}
    <div className="card mt"><h3>{t("ai.dHist")}</h3><DataTable rows={runs.data} loading={runs.loading} error={runs.err} pageSize={8} cols={[{ head: t("ai.dWhen"), cell: r => when(r.ts) }, { head: t("ai.dReq"), cell: r => r.request, text: r => r.request }, { head: t("ai.dEngine"), cell: r => t("ai.eng." + r.engine), text: r => r.engine }, { head: t("ai.dTokens"), num: true, cell: r => r.tokens_in + r.tokens_out }, { head: t("ai.dBy"), cell: r => <span className="ltr">{r.actor}</span> }]} /></div></>;
}

function AiPage({ presetWorker, oid: given }: { presetWorker?: string; oid?: string }) {
  useLang(); const cur = useOrgId(); const oid = given ?? cur; const [tab, setTab] = useState(aiTabs.length ? "tech" : "chat"); void getLang; void isSenior;
  const all = [...aiTabs, { key: "chat", order: 10, label: "ai.tabChat", render: (p: { oid: string; presetWorker?: string }) => <ChatTab oid={p.oid} presetWorker={p.presetWorker} /> },
    { key: "draft", order: 15, label: "ai.tabDraft", render: (p: { oid: string; presetWorker?: string }) => <DiagnoseTab oid={p.oid} presetWorker={p.presetWorker} /> }].sort((a, b) => a.order - b.order);
  const cur2 = all.find(x => x.key === tab) ?? all[0];
  return <>{!given && <OrgBar />}{!oid ? <NoOrg /> : <><Tabs tabs={all.map(x => [x.key, t(x.label)] as [string, string])} value={cur2.key} onChange={setTab} />{cur2.render({ oid, presetWorker })}</>}</>;
}
registerSection({ key: "ai", icon: "ai", order: 50, label: "nav.ai", render: () => <AiPage /> });
registerWorkspaceTab({ key: "ai", label: "AI Assistant", node: ({ wid, oid }) => <AiPage oid={oid} presetWorker={wid} /> });
