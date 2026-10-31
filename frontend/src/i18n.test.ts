import { describe, expect, it } from "vitest";
import { _dict } from "./i18n";

// importing the views registers their translations (reg(...)) as a side effect
const mods = import.meta.glob(["./**/*.tsx", "!./main.tsx"], { eager: true });
const sources = import.meta.glob("./**/*.{ts,tsx}", { eager: true, query: "?raw", import: "default" }) as Record<string, string>;
const ALLOWED_SAME = new Set(["CPU", "RAM", "ADB", "Logcat", "PowerShell", "CMD", "SHA-256", "API", "AI", "Qwen", "Ollama", "IP", "ID", "PDF", "USB", "APK", "Wi-Fi", "SLA", "RTL"]);

describe("i18n", () => {
  it("loads the views so their dictionaries register", () => { expect(Object.keys(mods).length).toBeGreaterThan(3); });
  it("every key has both languages and the Arabic is real Arabic", () => {
    for (const [k, v] of Object.entries(_dict)) {
      expect(v.en, k).toBeTruthy(); expect(v.ar, k).toBeTruthy();
      if (!ALLOWED_SAME.has(v.en) && /[A-Za-z]{4,}/.test(v.en)) expect(/[؀-ۿ]/.test(v.ar), `${k}: Arabic text missing`).toBe(true);
      expect(v.ar, `${k}: corrupted characters`).not.toMatch(/[ÃÂâ€�]/);
      expect(v.en, `${k}: corrupted characters`).not.toMatch(/[ÃÂâ€�]/);
    }
  });
  it("every t('key') used in the source exists in the dictionary", () => {
    const missing: string[] = [];
    for (const [file, src] of Object.entries(sources)) {
      if (file.includes(".test.") || file.endsWith("i18n.ts")) continue;
      for (const m of src.matchAll(/\bt\(\s*"([A-Za-z0-9_.]+)"/g)) { const k = m[1]; if (k.endsWith(".")) { if (!Object.keys(_dict).some(x => x.startsWith(k))) missing.push(`${file}: no keys with prefix ${k}`); } else if (!_dict[k]) missing.push(`${file}: ${k}`); }
    }
    expect(missing).toEqual([]);
  });
  it("placeholders match between languages", () => {
    for (const [k, v] of Object.entries(_dict)) expect((v.en.match(/\{\w+\}/g) ?? []).sort(), k).toEqual((v.ar.match(/\{\w+\}/g) ?? []).sort());
  });
});
