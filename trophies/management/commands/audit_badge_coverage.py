"""Daily audit: alert when a franchise/developer badge is missing one of its games.

For each tier-1 badge that tracks a franchise/developer, finds concepts of that
franchise/developer not covered by the badge's stages and emails the findings to the
badge-alerts inbox. A gap usually means a new game needs adding to the badge.

By default the email is sent only when there are gaps; pass --always for a daily
heartbeat (an "all clear" email even when nothing is missing).
"""

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand

from trophies.services.badge_coverage_service import audit_badge_coverage

ALERT_EMAIL = 'badge-alerts@platpursuit.com'


def format_report(findings):
    """Plain-text report body for the given audit_badge_coverage() findings."""
    total = sum(len(f['missing']) for f in findings)
    if not findings:
        return ("Badge coverage audit: every tracked franchise/developer badge "
                "covers its concepts. No gaps found.")

    lines = [
        f"Badge coverage audit: {total} concept(s) across {len(findings)} badge(s) "
        f"are NOT assigned to a badge stage.",
        "A gap usually means a new game needs adding to the badge, or a data error occurred.",
        "",
    ]
    for finding in findings:
        badge = finding['badge']
        sources = []
        if finding['franchise']:
            sources.append(f"franchise: {finding['franchise'].name}")
        if finding['developer']:
            sources.append(f"developer: {finding['developer'].name}")
        lines.append(f"{badge.name}  ({'; '.join(sources)})  [series: {badge.series_slug}]")
        for concept in finding['missing']:
            title = concept.unified_title or concept.concept_id
            lines.append(f"    - {title}  (slug: {concept.slug or 'none'}, concept_id: {concept.concept_id})")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


class Command(BaseCommand):
    help = (
        "Audit tier-1 franchise/developer badges for concepts missing from their "
        "stages and email findings to the badge-alerts inbox."
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Print the report; do not send email.')
        parser.add_argument('--always', action='store_true',
                            help='Send the email even when no gaps are found (heartbeat).')

    def handle(self, *args, **options):
        findings = audit_badge_coverage()
        report = format_report(findings)
        self.stdout.write(report)

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('Dry run: no email sent.'))
            return

        if not findings and not options['always']:
            self.stdout.write(self.style.SUCCESS(
                'No gaps found; no email sent (use --always to send a heartbeat).'
            ))
            return

        total = sum(len(f['missing']) for f in findings)
        subject = (
            f"[PlatPursuit] Badge coverage: {total} unassigned concept(s) "
            f"across {len(findings)} badge(s)"
            if findings else
            "[PlatPursuit] Badge coverage: all clear"
        )
        send_mail(
            subject, report, settings.DEFAULT_FROM_EMAIL, [ALERT_EMAIL],
            fail_silently=False,
        )
        self.stdout.write(self.style.SUCCESS(f"Emailed findings to {ALERT_EMAIL}."))
