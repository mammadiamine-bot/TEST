import type { ReactNode } from "react";

/** Feature modules (remote control, android, AI chat) register their top-level section here; the shell lists only what exists. */
export type Section = { key: string; icon: string; order: number; label: string; render: (p: { goto: (p: string) => void }) => ReactNode };
const extra: Section[] = [];
export const registerSection = (s: Section) => { if (!extra.find(x => x.key === s.key)) extra.push(s); };
export const extraSections = () => extra;
