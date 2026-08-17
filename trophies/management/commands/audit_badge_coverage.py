"""Daily audit: alert when a franchise/collection/developer badge is missing a game.

For each badge SERIES that tracks a franchise, collection, and/or developer, finds
concepts of that source not covered by the series' stages and emails the findings to
the badge-alerts inbox. A gap usually means a new game needs adding to the series.

Series-level, not per-edition: stages belong to the series, and every edition works the
same stage list, so coverage is one question per series regardless of how many editions
it ships in.

By default the email is sent only when there are gaps; pass --always for a daily
heartbeat (an "all clear" email even when nothing is missing).
"""

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand

from trophies.services.badge_coverage_service import audit_badge_coverage

ALERT_EMAIL = 'badge-alerts@platpursuit.com'


def _plural(count, singular, plural=None):
    return singular if count == 1 else (plural or singular + 's')


def format_report(findings):
    """Plain-text report body for the given audit_badge_coverage() findings."""
    total = sum(len(f['missing']) for f in findings)
    if not findings:
        return ("Badge coverage audit: every tracked franchise/collection/developer "
                "series covers its concepts. No gaps found.")

    lines = [
        f"Badge coverage audit: {total} {_plural(total, 'concept')} across "
        f"{len(findings)} {_plural(len(findings), 'series', 'series')} "
        f"{_plural(total, 'is', 'are')} NOT assigned to a badge stage.",
        "A gap usually means a new game needs adding to the badge, or a data error occurred.",
        "",
    ]
    for finding in findings:
        series = finding['series']
        sources = []
        if finding['franchise']:
            sources.append(f"franchise: {finding['franchise'].name}")
        if finding['collection']:
            sources.append(f"collection: {finding['collection'].name}")
        if finding['developer']:
            sources.append(f"developer: {finding['developer'].name}")
        lines.append(f"{series.name}  ({'; '.join(sources)})  [series: {series.series_slug}]")
        for concept in finding['missing']:
            title = concept.unified_title or concept.concept_id
            lines.append(f"    - {title}  (slug: {concept.slug or 'none'}, concept_id: {concept.concept_id})")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


class Command(BaseCommand):
    help = (
        "Audit franchise/collection/developer badge series for concepts missing "
        "from their stages and email findings to the badge-alerts inbox."
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
            f"[PlatPursuit] Badge coverage: {total} unassigned "
            f"{_plural(total, 'concept')} across {len(findings)} {_plural(len(findings), 'series', 'series')}"
            if findings else
            "[PlatPursuit] Badge coverage: all clear"
        )
        send_mail(
            subject, report, settings.DEFAULT_FROM_EMAIL, [ALERT_EMAIL],
            fail_silently=False,
        )
        self.stdout.write(self.style.SUCCESS(f"Emailed findings to {ALERT_EMAIL}."))
