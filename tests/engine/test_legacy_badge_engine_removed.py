"""The legacy tier-based badge engine is gone (cutover 5b.4 + 5b.5).

`badge_service`, `badge_refresh_service`, `xp_service`, `redis_leaderboard_service`, `leaderboard_service`
and the signals that tied them together were deleted. The `Badge` / `UserBadge` / `UserBadgeProgress` /
`ProfileGamification` TABLES are retained for rollback and audit -- that retention is the whole reason this
file exists, because a live table with no writer is exactly the thing a future change re-wires by accident.

What is defended here:

  - the modules stay deleted
  - nothing writes the legacy tables on the sync path any more
  - the retained tables are not silently re-read as if they were current
  - `BadgeAdmin` STAYS, which is a real exception and not an oversight

See docs/architecture/badge-system.md and docs/design/rebuild/badge-backend-rebuild.md.
"""
import ast
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

DELETED_MODULES = [
    'trophies.services.badge_service',
    'trophies.services.badge_refresh_service',
    'trophies.services.xp_service',
    'trophies.services.redis_leaderboard_service',
    'trophies.services.leaderboard_service',
    'trophies.utils',
]

DELETED_COMMANDS = [
    'trophies/management/commands/check_all_badges.py',
    'trophies/management/commands/populate_badges.py',
    'trophies/management/commands/refresh_badge_series.py',
    'trophies/management/commands/check_profile_badge_series.py',
    'trophies/management/commands/update_badge_requirements.py',
    'trophies/management/commands/recalc_badge_rarity.py',
    'trophies/management/commands/recalculate_gamification.py',
    'trophies/management/commands/backfill_stage_completions.py',
    'core/management/commands/update_leaderboards.py',
    'core/management/commands/audit_profile_gamification.py',
]


@pytest.mark.parametrize('name', DELETED_MODULES)
def test_the_legacy_services_are_deleted(name):
    assert importlib.util.find_spec(name) is None, f'{name} is back'


@pytest.mark.parametrize('rel', DELETED_COMMANDS)
def test_the_legacy_commands_are_deleted(rel):
    assert not (ROOT / rel).exists(), f'{rel} is back'


def test_the_sync_path_evaluates_only_the_new_engine():
    """`_job_sync_complete` ran BOTH engines during cutover 5a. If the legacy call came back, a hunter would
    be pinged twice for one earn -- once tier-shaped, once edition-shaped -- which is the specific reason
    the new engine's announcement was suppressed for a whole phase.

    AST, not a substring search: `token_keeper` explains this history in a comment, and a naive scan reads
    the explanation as the offence.
    """
    tree = ast.parse((ROOT / 'trophies/token_keeper.py').read_text(encoding='utf-8'))
    called = {
        node.func.id for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    imported = {
        alias.name for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) for alias in node.names
    }

    assert 'check_profile_badges' not in called | imported, 'the legacy engine is back on the sync path'
    assert 'evaluate_for_sync' in called, 'the sync path no longer evaluates badges at all'


def test_the_retained_tables_have_no_writer():
    """`Badge` / `UserBadge` / `UserBadgeProgress` / `ProfileGamification` are kept for rollback and audit.
    A retained table is fine; a retained table someone starts writing again is a silent fork of the truth,
    with two XP economies claiming to be the same number.

    Scoped to the app's own service/signal/view layer -- `admin.py` is exempt (see the test below) and so
    are migrations, tests and the design docs that discuss the history.
    """
    writes = []
    for path in sorted(ROOT.glob('trophies/**/*.py')) + sorted(ROOT.glob('core/**/*.py')):
        rel = path.relative_to(ROOT).as_posix()
        if '/migrations/' in rel or rel.endswith('admin.py'):
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            # `Model.objects.create(...)` / `.update(...)` / `.update_or_create(...)` etc.
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {'create', 'update', 'update_or_create', 'get_or_create', 'bulk_create'}:
                continue
            # Walk back to the leftmost name in the attribute chain.
            root = node.func
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id in {
                'Badge', 'UserBadge', 'UserBadgeProgress', 'ProfileGamification',
            }:
                writes.append(f'{rel}:{node.lineno} writes {root.id}')

    assert not writes, (
        'the retained legacy badge tables are being written again: ' + '; '.join(writes)
    )


def test_badge_admin_is_deliberately_kept():
    """The exception, pinned so a later sweep does not "finish the job".

    `art_reveal.ArtRevealItem` has a live FK to `Badge`, and its inline's `autocomplete_fields` requires a
    registered admin for the model. Without this registration Django raises admin.E039 and the ENTIRE admin
    site fails its system check -- not just art_reveal.
    """
    from django.contrib import admin
    from trophies.models import Badge

    assert Badge in admin.site._registry, (
        'BadgeAdmin was removed; art_reveal.ArtRevealItemInline.autocomplete_fields needs it (admin.E039)'
    )


def test_the_admin_no_longer_registers_the_dead_denorms():
    """The other half: the models with no remaining live consumer did go."""
    from django.contrib import admin
    from trophies.models import ProfileGamification, UserBadge, UserBadgeProgress

    for model in (UserBadge, UserBadgeProgress, ProfileGamification):
        assert model not in admin.site._registry, f'{model.__name__} admin is back'
