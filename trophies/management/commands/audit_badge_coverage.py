"""Daily audit: alert when a franchise/collection/developer badge is missing a game.

For each badge SERIES that tracks a franchise, collection, and/or developer, finds
concepts of that source not covered by the series' stages and emails the findings to
the badge-alerts inbox. A gap usually means a new game needs adding to the series.
A developer source includes its subsidiaries and former identities, and each
developer-side gap names the family studio(s) IGDB credits as its developer.

It also reports developer series pinned to a company IGDB has since renamed, even when
they have no gaps: a stale pin silently stops new games from being flagged.

Series-level, not per-edition: stages belong to the series, and every edition works the
same stage list, so coverage is one question per series regardless of how many editions
it ships in.

By default the email is sent only when there is something to act on (a gap or a stale
pin); pass --always for a daily heartbeat (an "all clear" email even when there is not).
"""

from django.conf import settings
from django.core.mail import send_mail
from django.core.management.base import BaseCommand

from trophies.services.badge_coverage_service import audit_badge_coverage, stale_developer_pins

ALERT_EMAIL = 'badge-alerts@platpursuit.com'


def _plural(count, singular, plural=None):
    return singular if count == 1 else (plural or singular + 's')


def _pin_lines(pins):
    lines = [
        f"{len(pins)} developer {_plural(len(pins), 'badge')} {_plural(len(pins), 'needs its', 'need their')} "
        f"company checked. A badge pinned to a former company name does NOT check games from the "
        f"studios under the new name.",
    ]
    for pin in pins:
        series, developer, current = pin['series'], pin['developer'], pin['current']
        if pin['is_loop']:
            action = f"IGDB's rename chain for {developer.name} loops; check the company records"
        else:
            action = f"repoint developer from {developer.name} to {current.name}"
        lines.append(f"    - {series.name}  [series: {series.series_slug}]: {action}")
    lines.append("")
    return lines


def _subject(findings, pins):
    if not findings and not pins:
        return "[PlatPursuit] Badge coverage: all clear"
    parts = []
    if findings:
        total = sum(len(f['missing']) for f in findings)
        parts.append(
            f"{total} unassigned {_plural(total, 'concept')} across "
            f"{len(findings)} {_plural(len(findings), 'series', 'series')}"
        )
    if pins:
        parts.append(f"{len(pins)} {_plural(len(pins), 'badge')} to repoint")
    return f"[PlatPursuit] Badge coverage: {'; '.join(parts)}"


def format_report(findings, pins):
    """Plain-text report body for audit_badge_coverage() findings and stale_developer_pins() pins."""
    if not findings and not pins:
        return ("Badge coverage audit: every tracked franchise/collection/developer "
                "series covers its concepts. No gaps found.")

    lines = _pin_lines(pins) if pins else []
    if not findings:
        lines.append("No coverage gaps found.")
        return "\n".join(lines).rstrip() + "\n"

    total = sum(len(f['missing']) for f in findings)
    lines += [
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
        credited = finding['credited_developers']
        for concept in finding['missing']:
            title = concept.unified_title or concept.concept_id
            line = f"    - {title}  (slug: {concept.slug or 'none'}, concept_id: {concept.concept_id})"
            if concept.id in credited:
                line += f"  [developed by: {', '.join(credited[concept.id])}]"
            lines.append(line)
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
                            help='Send the email even when nothing is found (heartbeat).')

    def handle(self, *args, **options):
        findings = audit_badge_coverage()
        pins = stale_developer_pins()
        report = format_report(findings, pins)
        self.stdout.write(report)

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('Dry run: no email sent.'))
            return

        if not findings and not pins and not options['always']:
            self.stdout.write(self.style.SUCCESS(
                'No gaps or stale pins found; no email sent (use --always to send a heartbeat).'
            ))
            return

        send_mail(
            _subject(findings, pins), report, settings.DEFAULT_FROM_EMAIL, [ALERT_EMAIL],
            fail_silently=False,
        )
        self.stdout.write(self.style.SUCCESS(f"Emailed findings to {ALERT_EMAIL}."))
