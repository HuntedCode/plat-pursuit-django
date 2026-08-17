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
    'trophies/management/commands/backfill_earn_ranks.py',
]


#: Files that STILL write the legacy `Badge` table, deliberately listed rather than silently unscanned.
#:
#: Both are live features that predate the cutover and were never repointed:
#:   - `fundraiser/services/donation_service.py` credits a donor by writing `Badge.funded_by` on every
#:     completed artwork donation. The badge display reads `BadgeSeries.funded_by` (via
#:     `GroupBadge.effective_funded_by`), so that credit currently lands where nothing renders it.
#:   - `art_reveal/models.py` pushes released artwork onto `Badge.badge_image`.
#:
#: Shrinking this set is the goal. Adding to it needs a reason in the commit message.
KNOWN_LEGACY_WRITERS = {
    'fundraiser/services/donation_service.py',
    'art_reveal/models.py',
}


def test_the_known_legacy_writers_still_exist():
    """The exemption list must not rot into a lie in the other direction. If one of these files stops
    writing `Badge` (because it got repointed), delete its entry -- an exemption for a file that no longer
    needs one quietly re-opens a hole."""
    for rel in KNOWN_LEGACY_WRITERS:
        assert (ROOT / rel).exists(), f'{rel} is gone; drop it from KNOWN_LEGACY_WRITERS'
        assert 'Badge' in (ROOT / rel).read_text(encoding='utf-8'), (
            f'{rel} no longer references Badge; drop it from KNOWN_LEGACY_WRITERS'
        )


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

    Three things this has to get right, all of which an earlier version got wrong:

    - **Resolve the chain THROUGH calls.** `Model.objects.filter(...).update(...)` is the project's most
      common write form, and a walk that stops at the first `Call` node never sees the model name.
    - **Cover every write verb**, `save`/`delete`/`bulk_update` included, not just `create`/`update`.
    - **Scan `api/` too.** The legacy `UserBadge` snapshots that this cutover repointed lived in
      `api/views.py`, so excluding that package left the one file most likely to regress unguarded.
    """
    watched = {'Badge', 'UserBadge', 'UserBadgeProgress', 'ProfileGamification'}
    verbs = {'create', 'update', 'update_or_create', 'get_or_create', 'bulk_create', 'bulk_update',
             'save', 'delete'}
    writes = []

    def chain_root(node):
        """Leftmost Name in an attribute/call chain: `X.objects.filter(...).update` -> `X`."""
        while True:
            if isinstance(node, ast.Attribute):
                node = node.value
            elif isinstance(node, ast.Call):
                node = node.func
            else:
                return node

    # EVERY first-party app. An earlier version scanned only `trophies` + `core` and reported the tables
    # as writer-free; the 5b audit found two live writers in the apps it never looked at. A guard whose
    # scope is narrower than the claim it backs is worse than no guard.
    roots = ['trophies', 'core', 'api', 'notifications', 'users', 'milestones', 'fundraiser',
             'art_reveal', 'plat_pursuit']
    for root_pkg in roots:
        for path in sorted((ROOT / root_pkg).glob('**/*.py')):
            rel = path.relative_to(ROOT).as_posix()
            if '/migrations/' in rel or rel.endswith('admin.py') or rel in KNOWN_LEGACY_WRITERS:
                continue
            tree = ast.parse(path.read_text(encoding='utf-8'))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                # `Model.objects...verb(...)`
                if isinstance(node.func, ast.Attribute) and node.func.attr in verbs:
                    base = chain_root(node.func)
                    if isinstance(base, ast.Name) and base.id in watched:
                        writes.append(f'{rel}:{node.lineno} writes {base.id} (.{node.func.attr})')
                # Bare constructor: `UserBadge(profile=...)`
                elif isinstance(node.func, ast.Name) and node.func.id in watched:
                    writes.append(f'{rel}:{node.lineno} constructs {node.func.id}')

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
