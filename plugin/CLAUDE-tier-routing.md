<!-- claude-token-monitor:tier-routing:start -->
## Complexity Tier Routing

> Managed block — installed by `claude-token-monitor/plugin/hooks/install.sh`.
> Re-running the installer replaces everything between the start/end markers.
> Hand edits here will be overwritten; edit `plugin/CLAUDE-tier-routing.md` in the repo instead.

Before starting non-trivial work, classify the turn against the table below and route accordingly.

| Tier | Signal | Action |
|------|--------|--------|
| **Tier-2 routine** | Single-file edit at known location, add test following existing pattern, rename, typo, format, add log / null-check / docstring, update README section, generate data model from known schema, simple diagram, follow-up tweak ("đổi Y thành Z") | **Delegate via the Agent tool** to `subagent_type: routine-worker` (runs on Sonnet). Hand over exact file:line references and the literal change. Do NOT re-explore before delegating. |
| **Tier-3 main** | Open-ended ("why is X failing"), multi-file refactor, review for correctness, design decisions, debugging unknown failures, anything needing >4 file reads to understand | **Handle inline on main.** Do not delegate. |

Routing rules:
- When in doubt between Tier-2 and Tier-3, prefer Tier-2 — an unnecessary delegation is cheap; an unnecessary Opus turn is expensive (Opus ≈ 5× Sonnet).
- Never delegate the *classification itself* to a subagent. Classifying is a single-token decision; a dispatcher agent would cost more than it saves.
- If `routine-worker` escalates back (task turned out to be Tier-3), handle it inline. Do not re-delegate.
- When you delegate, the Agent-tool prompt must be self-contained: include file paths, the exact change, and any project conventions. The subagent has no conversation history.
<!-- claude-token-monitor:tier-routing:end -->
