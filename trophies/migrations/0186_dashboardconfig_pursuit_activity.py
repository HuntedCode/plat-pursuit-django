"""Data migration: rewrite legacy dashboard module slugs to pursuit_activity.

Phase 6 of the Community Hub initiative collapses the legacy `recent_activity`
and `recent_platinums` modules into a single hybrid `pursuit_activity` module.
This migration walks every DashboardConfig row and updates four fields:

- `module_order`: list of slugs. Replace both legacy slugs with one
  `pursuit_activity` entry, preserving the position of the FIRST legacy
  slug found and dropping the second so the user's customized order is
  honored without leaving a hole.
- `hidden_modules`: list of slugs. If either legacy slug was hidden,
  hide `pursuit_activity` instead. Dedupe so we don't end up with two
  entries.
- `module_settings`: dict keyed by slug. Merge any legacy settings into
  `pursuit_activity` (preferring `recent_activity` settings since the new
  module's defaults match the recent_activity shape: `limit` 8 with
  options 5/8/12).
- `tab_config['module_tab_overrides']`: dict keyed by slug. Same
  rewrite-and-merge pattern as module_settings; if the user assigned
  either legacy module to a custom tab, the new module inherits that
  assignment.

The migration is idempotent: re-running it on a config that already
references `pursuit_activity` is a no-op for that config.
"""
from django.db import migrations


LEGACY_SLUGS = ('recent_activity', 'recent_platinums')
NEW_SLUG = 'pursuit_activity'


def _rewrite_module_order(module_order):
    """Replace legacy slugs with one pursuit_activity entry, preserving position."""
    if not isinstance(module_order, list):
        return module_order
    new_order = []
    inserted = False
    for slug in module_order:
        if slug in LEGACY_SLUGS:
            if not inserted:
                new_order.append(NEW_SLUG)
                inserted = True
            # Otherwise: skip the second legacy slug (already replaced)
        else:
            new_order.append(slug)
    # Dedupe in case the user already had pursuit_activity AND a legacy slug
    seen = set()
    deduped = []
    for slug in new_order:
        if slug not in seen:
            seen.add(slug)
            deduped.append(slug)
    return deduped


def _rewrite_hidden_modules(hidden_modules):
    """If either legacy slug was hidden, hide pursuit_activity instead."""
    if not isinstance(hidden_modules, list):
        return hidden_modules
    new_hidden = [s for s in hidden_modules if s not in LEGACY_SLUGS]
    if any(s in LEGACY_SLUGS for s in hidden_modules):
        if NEW_SLUG not in new_hidden:
            new_hidden.append(NEW_SLUG)
    return new_hidden


def _rewrite_settings_dict(settings_dict):
    """Merge legacy module settings into pursuit_activity (recent_activity wins on conflict)."""
    if not isinstance(settings_dict, dict):
        return settings_dict
    new_settings = {k: v for k, v in settings_dict.items() if k not in LEGACY_SLUGS}
    legacy_payload = {}
    # Merge in priority order: recent_platinums first, then recent_activity
    # so recent_activity values overwrite (matches the new module's shape).
    if 'recent_platinums' in settings_dict and isinstance(settings_dict['recent_platinums'], dict):
        legacy_payload.update(settings_dict['recent_platinums'])
    if 'recent_activity' in settings_dict and isinstance(settings_dict['recent_activity'], dict):
        legacy_payload.update(settings_dict['recent_activity'])
    if legacy_payload:
        # Don't clobber existing pursuit_activity settings if the user
        # somehow already has them — let the existing entry win.
        existing = new_settings.get(NEW_SLUG, {})
        if isinstance(existing, dict):
            merged = {**legacy_payload, **existing}
        else:
            merged = legacy_payload
        new_settings[NEW_SLUG] = merged
    return new_settings


def rewrite_configs(apps, schema_editor):
    DashboardConfig = apps.get_model('trophies', 'DashboardConfig')
    for config in DashboardConfig.objects.all().iterator():
        changed = False

        new_order = _rewrite_module_order(config.module_order)
        if new_order != config.module_order:
            config.module_order = new_order
            changed = True

        new_hidden = _rewrite_hidden_modules(config.hidden_modules)
        if new_hidden != config.hidden_modules:
            config.hidden_modules = new_hidden
            changed = True

        new_settings = _rewrite_settings_dict(config.module_settings)
        if new_settings != config.module_settings:
            config.module_settings = new_settings
            changed = True

        # tab_config['module_tab_overrides'] is the per-user mapping of
        # slug -> custom tab name (premium feature). Same rewrite pattern.
        tab_config = config.tab_config or {}
        if isinstance(tab_config, dict):
            overrides = tab_config.get('module_tab_overrides') or {}
            if isinstance(overrides, dict) and any(s in LEGACY_SLUGS for s in overrides):
                new_overrides = {k: v for k, v in overrides.items() if k not in LEGACY_SLUGS}
                # If either legacy slug was assigned to a custom tab, give
                # pursuit_activity the same assignment. recent_activity wins
                # on conflict for the same reason as settings.
                if 'recent_platinums' in overrides:
                    new_overrides[NEW_SLUG] = overrides['recent_platinums']
                if 'recent_activity' in overrides:
                    new_overrides[NEW_SLUG] = overrides['recent_activity']
                tab_config['module_tab_overrides'] = new_overrides
                config.tab_config = tab_config
                changed = True

        if changed:
            config.save(update_fields=[
                'module_order', 'hidden_modules', 'module_settings',
                'tab_config', 'updated_at',
            ])


def reverse_noop(apps, schema_editor):
    """Reverse is a no-op: rolling back the schema (Phase 1) drops the Event
    table entirely, which removes any reason to keep the legacy slugs alive.
    Users on a rolled-back environment will see their pursuit_activity entries
    silently dropped from the registry, which is acceptable for a rollback path."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('trophies', '0185_day_zero_event'),
    ]

    operations = [
        migrations.RunPython(rewrite_configs, reverse_noop),
    ]
