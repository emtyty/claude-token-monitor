## Complexity Tier Routing

> This file is installed to `~/.claude/claude-token-monitor-tier-routing.md` by
> `claude-token-monitor/plugin/hooks/install.sh` and imported into `~/.claude/CLAUDE.md`
> via `@claude-token-monitor-tier-routing.md`. Re-running the installer overwrites this file.
> Edit `plugin/CLAUDE-tier-routing.md` in the repo instead.

Before starting non-trivial work, classify the turn against the table below and route accordingly.

| Tier | Signal | Action |
|------|--------|--------|
| **Tier-2 routine** | See "Tier-2 triggers" list below. | **Delegate via the Agent tool** to `subagent_type: routine-worker` (runs on Sonnet). Hand over exact file:line references and the literal change. Do NOT re-explore before delegating. |
| **Tier-3 main** | Open-ended question, multi-file refactor, review for correctness, design decisions, debugging unknown failures, anything needing more than 4 file reads to understand. | **Handle inline on main.** Do not delegate. |

### Tier-2 triggers (delegate to routine-worker)

1. Single-file edit at a known location (rename, typo, format, add comment, change config value)
2. Adding or updating unit tests following an existing pattern
3. Applying a small well-defined refactor the user already described (extract method, inline variable)
4. Adding a log line, null-check, or guard clause at a specific spot
5. Writing boilerplate (getters/setters, Codable conformance, DI registration)
6. Updating imports, removing dead code the user pointed out
7. Fixing a lint/compile error with an obvious fix
8. Writing a docstring for a given function
9. Updating a README section
10. Generating a simple data model from a known schema
11. Follow-up tweaks within a conversation after the main work is done (for example: "now also change X", "đổi Y thành Z", "thêm field W") — each small turn is its own routine task
12. Applying a user's explicit directive targeted at a specific file/line (for example: "fix the 3 bugs in FILE.md", "rename foo to bar in service.swift")

Concrete examples (all → delegate):
- fix typo in README line 42
- Add null-check at UserService.swift:88
- Rename getUserInfo to fetchUserInfo
- Add a Codable conformance to Session struct

### Tier-3 signals (handle inline, do not delegate)

- Architectural decisions, feature design
- Debugging unknown failures
- Multi-file refactors touching unfamiliar code
- Reviewing code for subtle bugs
- Anything requiring reading more than 5 files to understand context
- Anything the user framed as open-ended ("figure out why...", "what should we do about...", "how should this work")

### Routing rules

- When in doubt between Tier-2 and Tier-3, prefer Tier-2 — an unnecessary delegation is cheap; an unnecessary Opus turn is expensive (Opus ≈ 5× Sonnet).
- Never delegate the *classification itself* to a subagent. Classifying is a single-token decision; a dispatcher agent would cost more than it saves.
- If `routine-worker` escalates back (task turned out to be Tier-3), handle it inline. Do not re-delegate.
- When you delegate, the Agent-tool prompt must be self-contained: include file paths, the exact change, and any project conventions. The subagent has no conversation history.
