"""
Data migration: rename the 'community' dashboard tab slug to 'share'.

Rewrites DashboardConfig.tab_config JSON fields that reference 'community':
  - active_tab
  - tab_order array entries
  - module_tab_overrides values
"""
import json
from django.db import migrations


def rename_community_to_share(apps, schema_editor):
    DashboardConfig = apps.get_model('trophies', 'DashboardConfig')

    for config in DashboardConfig.objects.filter(tab_config__contains='community'):
        tc = config.tab_config
        if not isinstance(tc, dict):
            continue

        changed = False

        # active_tab
        if tc.get('active_tab') == 'community':
            tc['active_tab'] = 'share'
            changed = True

        # tab_order
        tab_order = tc.get('tab_order')
        if isinstance(tab_order, list) and 'community' in tab_order:
            tc['tab_order'] = ['share' if t == 'community' else t for t in tab_order]
            changed = True

        # module_tab_overrides values
        overrides = tc.get('module_tab_overrides')
        if isinstance(overrides, dict):
            new_overrides = {}
            for slug, target in overrides.items():
                if target == 'community':
                    new_overrides[slug] = 'share'
                    changed = True
                else:
                    new_overrides[slug] = target
            if changed:
                tc['module_tab_overrides'] = new_overrides

        if changed:
            config.tab_config = tc
            config.save(update_fields=['tab_config'])


def rename_share_to_community(apps, schema_editor):
    DashboardConfig = apps.get_model('trophies', 'DashboardConfig')

    for config in DashboardConfig.objects.filter(tab_config__contains='share'):
        tc = config.tab_config
        if not isinstance(tc, dict):
            continue

        changed = False

        if tc.get('active_tab') == 'share':
            tc['active_tab'] = 'community'
            changed = True

        tab_order = tc.get('tab_order')
        if isinstance(tab_order, list) and 'share' in tab_order:
            tc['tab_order'] = ['community' if t == 'share' else t for t in tab_order]
            changed = True

        overrides = tc.get('module_tab_overrides')
        if isinstance(overrides, dict):
            new_overrides = {}
            for slug, target in overrides.items():
                if target == 'share':
                    new_overrides[slug] = 'community'
                    changed = True
                else:
                    new_overrides[slug] = target
            if changed:
                tc['module_tab_overrides'] = new_overrides

        if changed:
            config.tab_config = tc
            config.save(update_fields=['tab_config'])


class Migration(migrations.Migration):

    dependencies = [
        ('trophies', '0167_profile_card_settings'),
    ]

    operations = [
        migrations.RunPython(
            rename_community_to_share,
            rename_share_to_community,
        ),
    ]
