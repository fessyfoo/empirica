"""Argparse parsers for `empirica voice` subcommand group.

Voice profiles store prosodic patterns (tendencies, anti-patterns,
register-by-platform) extracted from a person's writing samples. The
samples themselves live in Cortex/Qdrant; the .yaml profile is the
portable summary that any AI can load and apply.

Profile resolution order:
  1. {cwd}/.empirica/voice/{name}.yaml   (project-local override)
  2. ~/.empirica/voice/{name}.yaml       (user-global, default)
"""

from __future__ import annotations


def add_voice_parsers(subparsers):
    """Register the `voice` subcommand group with list/show/apply."""
    voice_root = subparsers.add_parser(
        'voice',
        help='Prosodic voice profiles — load tendencies for outreach drafting',
        description="""
Load and apply prosodic voice profiles for outreach drafting.
Voice profiles distill writing-pattern signals (tendencies,
anti-patterns, register-per-platform) into a portable .yaml the
AI can adopt before drafting an email, post, or comment.

Profiles are looked up first in the project-local
.empirica/voice/, then in ~/.empirica/voice/. The samples behind
a profile live in Cortex/Qdrant; populate via
mcp__cortex__populate_voice.
        """,
    )
    voice_subs = voice_root.add_subparsers(
        dest='voice_action', metavar='action'
    )

    # ─── list ─────────────────────────────────────────────────────────────
    lst = voice_subs.add_parser(
        'list',
        help='List available voice profiles (project-local + global)',
    )
    lst.add_argument('--output', choices=['json', 'human'], default='human',
                     help='Output format (default: human)')

    # ─── show ─────────────────────────────────────────────────────────────
    show = voice_subs.add_parser(
        'show',
        help='Print full profile yaml + computed summary',
    )
    show.add_argument('name', help='Profile name (filename without .yaml)')
    show.add_argument('--output', choices=['json', 'human'], default='human',
                      help='Output format (default: human)')

    # ─── apply ────────────────────────────────────────────────────────────
    apply_p = voice_subs.add_parser(
        'apply',
        help='Print structured AI guidance for adopting a voice in a register',
        description='Outputs voice tendencies + anti-patterns scoped to a '
                    'platform register (email, reddit, devto, ...). Designed '
                    'for the calling AI to internalize before drafting.',
    )
    apply_p.add_argument('name', help='Profile name (filename without .yaml)')
    apply_p.add_argument('--register',
                         help='Platform register: email | reddit | devto | linkedin | medium | book. '
                              'Falls back to natural_register if unset.')
    apply_p.add_argument('--output', choices=['json', 'human'], default='human',
                         help='Output format (default: human)')
