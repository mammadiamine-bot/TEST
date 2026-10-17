---
name: intelligent-development-orchestrator
description: Plan and coordinate any substantial development task (UI/UX, frontend, backend/DB, debugging, security, deployment). Selects the smallest effective set of installed skills, MCP tools and subagents, enforces project isolation, and verifies results with evidence. Use at the start of non-trivial coding, design, debugging or release work.
---

# Intelligent Development Orchestrator

Run this procedure ONCE per task. Do not re-run discovery or call this skill from other skills.

## 1. Identify (cheap)
- Project = git root / cwd. Read its CLAUDE.md, DESIGN.md, status/report files, `.claude/rules`. Reuse them; don't copy them elsewhere.
- Isolation: touch only this project's code, credentials, DB. Never reuse another project's config.

## 2. Select tools (smallest effective set)
Use the skill list already in context; no filesystem scan. Pick by task:
| Task | Prefer (if installed & connected) |
|---|---|
| UI/UX, brand | project `ui-ux-pro-max`, `design-system`, `ui-styling`; `21st:21st-ui`; Figma/Canva MCP only if a file/URL is given; `a11y-enforcer`; `web-perf-a11y-qa` |
| Frontend | `agent-protocols:frontend-ui-engineering`, `vercel-react-best-practices`; Playwright for browser check |
| Backend/DB | Supabase MCP (read-only inspect: list_tables, get_advisors) + `supabase-postgres-best-practices`; `security-and-hardening` |
| Debug | `agent-protocols:debugging-and-error-recovery`: reproduce → root cause → smallest fix → verify workflow |
| Security | `security-and-hardening`, `saas-security-review`, `security-engineer`; check authn/authz, API access, tenant isolation |
| Deploy | build/lint/test only; Vercel/Supabase writes need explicit user approval |
| Complex/parallel | `Agent` subagents (Explore for broad search, code-reviewer, test-engineer) only when parallelism or isolation clearly pays off |
- MCP tool failed/needs auth → say so, use an existing alternative (CLI, Playwright, local files).

## 3. Execute
Inspect existing implementation → smallest change → targeted check after each meaningful edit (syntax/lint/unit for touched files; full suite only before handoff or when risk is high). Independent checks run in parallel.

## 4. Quality gates
- UI: mobile 375 / tablet 768 / desktop 1280 screenshots via Playwright; contrast, focus states, labels, no horizontal scroll; follow DESIGN.md.
- Perf: no needless deps/bundle growth; check obvious N+1/large assets.
- Security: no secrets in code/logs; validate inputs; least privilege.

## 5. Verify & report
Report only with evidence: what changed, what was verified (command/result/screenshot), what remains unfinished or blocked. Unverified = say unverified. Never publish/deploy/delete prod data/change paid services without approval.
