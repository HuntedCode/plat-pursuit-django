"""Data migration: reverse 0186 by rewriting `pursuit_activity` configs back to
`recent_activity` and `recent_platinums`.

The Pursuit Feed feature is being deferred. Migration 0186 collapsed the
legacy `recent_activity` and `recent_platinums` dashboard module slugs into
a single hybrid `pursuit_activity` slug. This migration walks DashboardConfig
rows and unwinds that transformation:

- module_order: replace each `pursuit_activity` entry with `recent_activity`
  followed immediately by `recent_platinums`. Two slots restored from one.
- hidden_modules: replace `pursuit_activity` with `recent_activity` (the
  user explicitly hid the activity feed; the platinum widget defaults to
  visible). Drop any duplicate.
- module_settings: copy any `pursuit_activity` settings under
  `recent_activity` (the activity feed had `limit` configurable, same as
  the new module). `recent_platinums` defaults are restored from the
  registry on next render. Then drop the `pursuit_activity` key.
- tab_config['module_tab_overrides']: any `pursuit_activity` custom-tab
  assignment is reapplied to BOTH `recent_activity` and `recent_platinums`
  (matches the original semantics — they're sibling at_a_glance modules).

We can't perfectly recover the original ordering since 0186 dropped a slot
position. The recovery places `recent_platinums` immediately after
`recent_activity`, which matches the registry's default ordering.

Idempotent: running on a config that's already been reverted is a no-op
because every check looks for `pursuit_activity` first.

Reverse is a no-op since rolling back a rollback is silly — the Event
schema removal in 0188 takes the system back to its pre-Phase-1 state.
"""
from django.db import migrations


NEW_SLUG = 'pursuit_activity'
LEGACY_ACTIVITY = 'recent_activity'
LEGACY_PLATINUMS = 'recent_platinums'


def _restore_module_order(module_order):
    """Replace each `pursuit_activity` entry with both legacy slugs in sequence."""
    if not isinstance(module_order, list):
        return module_order
    if NEW_SLUG not in module_order:
        return module_order

    new_order = []
    for slug in module_order:
        if slug == NEW_SLUG:
            # Drop duplicates: only insert legacies if they're not already present
            if LEGACY_ACTIVITY not in new_order and LEGACY_ACTIVITY not in module_order:
                new_order.append(LEGACY_ACTIVITY)
            elif LEGACY_ACTIVITY not in new_order:
                # Already in module_order somewhere, will be added when we hit it
                pass
            else:
                pass
            if LEGACY_PLATINUMS not in new_order and LEGACY_PLATINUMS not in module_order:
                new_order.append(LEGACY_PLATINUMS)
        else:
            new_order.append(slug)

    # Final dedupe pass: preserve first occurrence
    seen = set()
    deduped = []
    for slug in new_order:
        if slug not in seen:
            seen.add(slug)
            deduped.append(slug)
    return deduped


def _restore_hidden_modules(hidden_modules):
    """Replace pursuit_activity with recent_activity in the hidden list."""
    if not isinstance(hidden_modules, list):
        return hidden_modules
    if NEW_SLUG not in hidden_modules:
        return hidden_modules
    new_hidden = [s for s in hidden_modules if s != NEW_SLUG]
    if LEGACY_ACTIVITY not in new_hidden:
        new_hidden.append(LEGACY_ACTIVITY)
    return new_hidden


def _restore_settings_dict(settings_dict):
    """Copy pursuit_activity settings to recent_activity, drop the new key."""
    if not isinstance(settings_dict, dict):
        return settings_dict
    if NEW_SLUG not in settings_dict:
        return settings_dict

    new_settings = {k: v for k, v in settings_dict.items() if k != NEW_SLUG}
    pursuit_settings = settings_dict.get(NEW_SLUG)
    if isinstance(pursuit_settings, dict) and LEGACY_ACTIVITY not in new_settings:
        new_settings[LEGACY_ACTIVITY] = pursuit_settings
    return new_settings


def restore_legacy_configs(apps, schema_editor):
    DashboardConfig = apps.get_model('trophies', 'DashboardConfig')
    for config in DashboardConfig.objects.all().iterator():
        changed = False

        new_order = _restore_module_order(config.module_order)
        if new_order != config.module_order:
            config.module_order = new_order
            changed = True

        new_hidden = _restore_hidden_modules(config.hidden_modules)
        if new_hidden != config.hidden_modules:
            config.hidden_modules = new_hidden
            changed = True

        new_settings = _restore_settings_dict(config.module_settings)
        if new_settings != config.module_settings:
            config.module_settings = new_settings
            changed = True

        # tab_config['module_tab_overrides']: per-user slug -> custom tab
        # name (premium feature). Apply pursuit_activity assignments to
        # BOTH legacy slugs.
        tab_config = config.tab_config or {}
        if isinstance(tab_config, dict):
            overrides = tab_config.get('module_tab_overrides') or {}
            if isinstance(overrides, dict) and NEW_SLUG in overrides:
                pursuit_tab = overrides[NEW_SLUG]
                new_overrides = {k: v for k, v in overrides.items() if k != NEW_SLUG}
                # Only set legacy slugs if they don't already have an override
                if LEGACY_ACTIVITY not in new_overrides:
                    new_overrides[LEGACY_ACTIVITY] = pursuit_tab
                if LEGACY_PLATINUMS not in new_overrides:
                    new_overrides[LEGACY_PLATINUMS] = pursuit_tab
                tab_config['module_tab_overrides'] = new_overrides
                config.tab_config = tab_config
                changed = True

        if changed:
            config.save(update_fields=[
                'module_order', 'hidden_modules', 'module_settings',
                'tab_config', 'updated_at',
            ])


def reverse_noop(apps, schema_editor):
    """Reverse is a no-op: rolling back this rollback would re-introduce
    pursuit_activity slugs, but the next migration in this revert series
    drops the Event model entirely, so there's no system to navigate back
    to. Leaving as a no-op."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('trophies', '0186_dashboardconfig_pursuit_activity'),
    ]

    operations = [
        migrations.RunPython(restore_legacy_configs, reverse_noop),
    ]
