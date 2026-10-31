import type { ReactNode } from "react";

const P: Record<string, ReactNode> = {
  dashboard: <><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></>,
  devices: <><rect x="3" y="4" width="18" height="12" rx="2" /><path d="M8 20h8M12 16v4" /></>,
  remote: <><rect x="3" y="4" width="18" height="12" rx="2" /><path d="M9 9l5 3-2.2.7L11 15z" /><path d="M8 20h8M12 16v4" /></>,
  android: <><rect x="7" y="2" width="10" height="20" rx="2.5" /><path d="M11 18.5h2" /></>,
  ai: <><path d="M12 3l1.8 4.7L18.5 9l-4.7 1.8L12 15.5l-1.8-4.7L5.5 9l4.7-1.3z" /><path d="M18 15l.8 2.2L21 18l-2.2.8L18 21l-.8-2.2L15 18l2.2-.8z" /></>,
  jobs: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
  monitoring: <><path d="M3 12h4l3-8 4 16 3-8h4" /></>,
  clients: <><path d="M3 21V8l9-5 9 5v13" /><path d="M9 21v-6h6v6" /></>,
  reports: <><path d="M6 3h9l4 4v14H6z" /><path d="M14 3v5h5M9 13h6M9 17h6" /></>,
  admin: <><circle cx="12" cy="12" r="3" /><path d="M19 12a7 7 0 00-.1-1.2l2-1.5-2-3.4-2.3.9a7 7 0 00-2-1.2L14 3h-4l-.5 2.6a7 7 0 00-2 1.2l-2.3-.9-2 3.4 2 1.5a7 7 0 000 2.4l-2 1.5 2 3.4 2.3-.9a7 7 0 002 1.2L10 21h4l.5-2.6a7 7 0 002-1.2l2.3.9 2-3.4-2-1.5c.1-.4.2-.8.2-1.2z" /></>,
  menu: <><path d="M4 6h16M4 12h16M4 18h16" /></>,
  moon: <><path d="M20 14.5A8 8 0 019.5 4 8 8 0 1020 14.5z" /></>,
  globe: <><circle cx="12" cy="12" r="9" /><path d="M3 12h18M12 3a14 14 0 010 18M12 3a14 14 0 000 18" /></>,
  out: <><path d="M9 4H5v16h4M16 8l4 4-4 4M20 12H9" className="flip" /></>,
  stop: <><circle cx="12" cy="12" r="9" /><path d="M9 9h6v6H9z" /></>,
};

export function Icon({ name, size }: { name: string; size?: number }) {
  return <svg className="ico" viewBox="0 0 24 24" aria-hidden="true" style={size ? { width: size, height: size } : undefined}>{P[name] ?? null}</svg>;
}
