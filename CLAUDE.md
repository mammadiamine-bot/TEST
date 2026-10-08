# CLAUDE.md

## Design work
For any UI/UX, visual, brand, banner, slide, or design-system task, use the skills in `.claude/skills/` (installed from alexitkin24-commits/awesome-claude-skills PR #1):

- `ui-ux-pro-max` — default starting point. Run `python3 .claude/skills/ui-ux-pro-max/scripts/search.py "<query>" --design-system` for a new page/project, or `--domain <domain>` / `--stack <stack>` for targeted questions.
- `design-system` — tokens (primitive → semantic → component), component specs.
- `ui-styling` — shadcn/ui + Tailwind implementation, dark mode.
- `brand`, `design`, `banner-design`, `slides` — brand identity, logos/CIP, banners, HTML decks.

Run the pre-delivery checklist in `.claude/skills/ui-ux-pro-max/references/pro-rules.md` before shipping UI.
