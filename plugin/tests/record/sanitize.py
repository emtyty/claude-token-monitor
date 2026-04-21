#!/usr/bin/env python3
"""Sanitize summary.cast + report.html before publishing demo videos.

Replaces real project paths with generic demo names. Longest paths first
so prefix collisions (e.g. catcher/DreemCatcher vs catcher) are safe.
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

# Ordered longest-first. Keep distinct aliases so the `suggest` breakdown
# still reads as "multiple projects" rather than one blob.
PATH_MAP: list[tuple[str, str]] = [
    ('~/Documents/dreem/catcher//claude/worktrees/ecstatic/mcnulty', '~/projects/web-app/worktree'),
    ('~/Documents/dreem/catcher/DreemCatcher',                       '~/projects/web-app/client'),
    ('~/Documents/tools/claude-token-monitor',                       '~/projects/token-monitor'),
    ('~/Documents/qa/playwright/agent',                              '~/projects/qa-agent'),
    ('~/Documents/lighthousekelvin/Kelvin',                          '~/projects/analytics/kelvin'),
    ('~/Documents/tools/claude/token/monitor',                       '~/projects/token-monitor'),
    ('~/Documents/tools/claude/watcher',                             '~/projects/watcher'),
    ('~/Documents/tools/tech/radar',                                 '~/projects/tech-radar'),
    ('~/Documents/dreem/admin/portal',                               '~/projects/admin-dashboard'),
    ('~/Documents/lighthousekelvin',                                 '~/projects/analytics'),
    ('~/Documents/dreem/catcher',                                    '~/projects/web-app'),
    ('~/Documents/web/dreem/studio',                                 '~/projects/design-studio'),
    ('~/Documents/dreem/studio',                                     '~/projects/design-studio'),
    ('~/Documents/aidlc/extension',                                  '~/projects/browser-ext'),
    ('~/Documents/aidlc/pipeline',                                   '~/projects/data-pipeline'),
    ('~/Documents/lhappautotest',                                    '~/projects/qa-auto'),
    ('~/Documents/devtool',                                          '~/projects/devtool'),
]

# Column-wrap continuations that leak leaf names. When the PTY wrapped
# ~/Documents/dreem/catcher/DreemCatcher across a column boundary we end up
# with a prefix on one row and the tail (e.g. 'eemCatcher') on the next.
# PATH_MAP rewrites the prefix but the orphan tail is still recognisable.
# Replace with same-width blanks to preserve table column alignment.
WRAP_SCRUB: list[tuple[str, str]] = [
    ('~/projects/web-app/Dre',   '~/projects/web-app   '),
    ('~/projects/web-app/Dr',    '~/projects/web-app  '),
    ('eemCatcher)', '          )'),
    ('eemCatcher',  '          '),
    ('DreemCatcher', 'client       '),
    ('Kelvin',      'kelvin'),
    ('mcnulty',     'feature'),
]

# Anything still matching ~/Documents/... after the map is unknown — redact.
LEFTOVER = re.compile(r'~/Documents/[\w\-./]+')

# Suspicious leaf-name words for a final report (not auto-redacted — surface
# them so the caller can decide).
SUSPECT = re.compile(
    r'\b(eemCatcher|DreemCatcher|Kelvin|mcnulty|lhapp|lighthouse|catcher|dreem|aidlc)\b',
    re.IGNORECASE,
)


def sanitize(text: str) -> str:
    for old, new in PATH_MAP:
        text = text.replace(old, new)
    for old, new in WRAP_SCRUB:
        text = text.replace(old, new)
    text = LEFTOVER.sub('~/projects/redacted', text)
    return text


def main() -> int:
    here = Path(__file__).resolve().parent

    targets = [
        here / 'summary.cast',
        here / 'report.html',
    ]

    for target in targets:
        if not target.exists():
            print(f'! skip (missing): {target.name}')
            continue
        raw = target.read_text()
        cleaned = sanitize(raw)
        target.write_text(cleaned)

        # Report what was replaced
        leftover = LEFTOVER.findall(cleaned)
        suspects = sorted(set(SUSPECT.findall(cleaned)))
        status = 'clean' if not leftover and not suspects else (
            f'leftover: {len(leftover)}' + (f', suspects: {suspects}' if suspects else '')
        )
        print(f'✓ {target.name}  ({status})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
