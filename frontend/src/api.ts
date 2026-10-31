export type Json = any;
let base = (import.meta as any).env?.VITE_API_URL ?? "http://127.0.0.1:8765";
// one-click launcher: a sign-in token in the URL fragment (#token=...) is taken once, kept for this tab, and removed from the address bar
try { const m = /[#&]token=([^&]+)/.exec(location.hash); if (m) { sessionStorage.setItem("cit_token", decodeURIComponent(m[1])); history.replaceState(null, "", location.pathname + location.search); } } catch {}
const stored = (): string => { try { return sessionStorage.getItem("cit_token") ?? ""; } catch { return ""; } };
let token: string = (import.meta as any).env?.VITE_API_TOKEN ?? stored();  // dev/Tauri inject one; the web build asks the person to sign in (per tab, never persisted)
export const hasToken = () => !!token;
export function signIn(t: string) { token = t; try { sessionStorage.setItem("cit_token", t); } catch {} }
export function signOut() { token = ""; try { sessionStorage.removeItem("cit_token"); } catch {} location.reload(); }

/** In the Tauri shell the Rust side owns the per-launch token and hands it over via an IPC command. */
export async function initConfig() {
  try {
    const { invoke } = await import("@tauri-apps/api/core");
    const c: { url: string; token: string } = await invoke("backend_config");
    base = c.url; token = c.token;
  } catch { /* browser/dev mode: use VITE_* env */ }
}

export class ApiError extends Error { constructor(public status: number, msg: string) { super(msg); } }

export async function api<T = Json>(path: string, method = "GET", body?: unknown): Promise<T> {
  const r = await fetch(base + path, {
    method, headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!r.ok) {
    let d = r.statusText; try { d = (await r.json()).detail ?? d; } catch {}
    throw new ApiError(r.status, typeof d === "string" ? d : JSON.stringify(d));
  }
  return r.json();
}
export const reportUrl = (did: string, planId?: string) => `${base}/devices/${did}/report${planId ? `?plan_id=${planId}` : ""}`;
export async function downloadReport(did: string, planId?: string) {
  const r = await fetch(reportUrl(did, planId), { headers: { Authorization: `Bearer ${token}` } });
  if (!r.ok) throw new ApiError(r.status, "no report available yet — run a diagnostic first");
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a"); a.href = url; a.download = "report.pdf"; a.click(); URL.revokeObjectURL(url);
}
