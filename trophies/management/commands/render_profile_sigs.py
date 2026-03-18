"""
Management command to render profile signature images.

Usage:
    python manage.py render_profile_sigs                    # All enabled sigs
    python manage.py render_profile_sigs --profile=username # Single profile
    python manage.py render_profile_sigs --cleanup          # Remove orphaned sig files
    python manage.py render_profile_sigs --force            # Re-render even if unchanged
    python manage.py render_profile_sigs --svg-only         # Only render SVG (skip Playwright)
"""
from django.core.management.base import BaseCommand
from trophies.models import Profile, ProfileCardSettings


class Command(BaseCommand):
    help = 'Render profile card forum signature images (PNG + SVG)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--profile',
            type=str,
            help='PSN username of specific profile to render',
        )
        parser.add_argument(
            '--cleanup',
            action='store_true',
            help='Remove orphaned sig files and exit',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Force re-render even if data hash is unchanged',
        )
        parser.add_argument(
            '--svg-only',
            action='store_true',
            help='Only render SVG files (skip Playwright PNG)',
        )

    def handle(self, *args, **options):
        from core.services.profile_card_renderer import (
            render_sig_png, render_sig_svg, render_all_sigs,
            cleanup_orphaned_sigs,
        )

        if options['cleanup']:
            removed = cleanup_orphaned_sigs()
            self.stdout.write(
                self.style.SUCCESS(f"Cleanup complete. Removed {removed} orphaned files.")
            )
            return

        if options['profile']:
            try:
                profile = Profile.objects.get(psn_username__iexact=options['profile'])
            except Profile.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f"Profile '{options['profile']}' not found.")
                )
                return

            if options['force']:
                # Clear the hash to force re-render
                ProfileCardSettings.objects.filter(profile=profile).update(sig_render_hash='')

            if options['svg_only']:
                path = render_sig_svg(profile)
                self.stdout.write(self.style.SUCCESS(f"SVG rendered: {path}"))
            else:
                png_path, svg_path = render_all_sigs(profile)
                self.stdout.write(self.style.SUCCESS(
                    f"Rendered: PNG={png_path}, SVG={svg_path}"
                ))
            return

        # Batch mode: all profiles with public sig enabled
        qs = ProfileCardSettings.objects.filter(
            public_sig_enabled=True,
        ).select_related('profile')

        total = qs.count()
        if total == 0:
            self.stdout.write(self.style.WARNING("No profiles with public sig enabled."))
            return

        self.stdout.write(f"Rendering sigs for {total} profiles...")

        if options['force']:
            qs.update(sig_render_hash='')

        success = 0
        errors = 0
        skipped = 0

        for card_settings in qs.iterator():
            profile = card_settings.profile
            try:
                if options['svg_only']:
                    render_sig_svg(profile)
                else:
                    render_all_sigs(profile)
                success += 1
            except Exception as e:
                errors += 1
                self.stdout.write(
                    self.style.ERROR(f"  Error for {profile.psn_username}: {e}")
                )

            if (success + errors) % 10 == 0:
                self.stdout.write(f"  Progress: {success + errors}/{total}")

        self.stdout.write(self.style.SUCCESS(
            f"Done. Success: {success}, Errors: {errors}"
        ))

        # Cleanup after batch
        removed = cleanup_orphaned_sigs()
        if removed:
            self.stdout.write(f"Cleaned up {removed} orphaned files.")
