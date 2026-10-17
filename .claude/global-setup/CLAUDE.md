# Global rules (concise; details live in skill `intelligent-development-orchestrator`)

## Language & style
- Reply in Arabic by default (English only if asked). Be concise, direct, practical.
- Prompt/message requests: one complete ready-to-copy version, no extra explanation.

## Principles
- Before substantial work: read the project's CLAUDE.md / DESIGN.md / status files and inspect existing code before editing. Continue existing work; never restart or duplicate features.
- Auto-use relevant Skills/Plugins/MCP/agents — the smallest effective set, loaded only when relevant. Prefer a specialized tool when it materially improves the result. Never claim a tool was used unless it was actually invoked.
- If a connection is unavailable, use an existing alternative and say so.
- Preserve working features, data, layouts, integrations. Follow the existing design system (DESIGN.md first).
- UI work: verify in a real browser (Playwright), responsive (mobile/tablet/desktop), accessible (contrast, focus, labels).
- Verify instead of assuming. Never report "done" without evidence (command output, test result, screenshot); state what is unverified or blocked.
- After each task record: what changed, what was verified, what remains blocked.
- Never publish, deploy, delete production data, or change paid services without my explicit approval.

## Project isolation
Projects: MAYDAN Copy, MAYDAN Original, Growth OS, SWANA, AI Media Hub, Page2Store AI, Property Manager, Barmajli (+Interactive Lab), others. Never mix code, credentials, DB configs, or business logic across projects. Use only the current project's own knowledge files; don't duplicate them here.

## Routing
For any non-trivial task invoke skill `intelligent-development-orchestrator` once (no repeated discovery).
