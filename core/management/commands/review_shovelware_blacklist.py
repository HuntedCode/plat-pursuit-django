import logging

from django.core.management.base import BaseCommand
from django.db.models import Count, Q

from trophies.models import DeveloperReputation, Game, Genre, Theme
from trophies.services.shovelware_detection_service import ShovelwareDetectionService

logger = logging.getLogger("psn_api")


class Command(BaseCommand):
    help = (
        "Review aid: list every currently-blacklisted developer with its "
        "shovelware proportion, the dominant genres/themes of its qualifying "
        "concepts, and a few sample games. Read-only. Use it to decide which "
        "developers to whitelist (e.g. visual-novel studios whose high earn "
        "rates are legitimate). Whitelist via the DeveloperReputation admin."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--samples', type=int, default=3,
            help='How many top qualifying concepts to list per developer (default: 3).',
        )
        parser.add_argument(
            '--limit', type=int, default=None,
            help='Only show the first N developers (after sorting). Default: all.',
        )
        parser.add_argument(
            '--include-whitelisted', action='store_true',
            help='Also include developers already whitelisted (normally excluded).',
        )

    def handle(self, *args, **options):
        sample_count = options['samples']
        limit = options['limit']
        include_whitelisted = options['include_whitelisted']

        flag_t = ShovelwareDetectionService.FLAG_THRESHOLD       # 80 (enter)
        evidence_t = ShovelwareDetectionService.EVIDENCE_THRESHOLD  # 70 (stay)

        entries = DeveloperReputation.objects.filter(is_blacklisted=True).select_related('company')
        if not include_whitelisted:
            entries = entries.filter(is_whitelisted=False)

        rows = []
        for entry in entries.iterator(chunk_size=100):
            company = entry.company

            denom_ids = list(
                DeveloperReputation.primary_developed_concepts(company).values_list('id', flat=True)
            )
            denom = len(denom_ids)

            # Evidence-threshold (70%) qualifying concepts, materialized once.
            qual_rows = list(
                DeveloperReputation.qualifying_concepts_for(company, threshold=evidence_t)
                .values('id', 'concept_id', 'unified_title', '_median_rate')
            )
            num_evidence = len(qual_rows)
            num_enter = sum(1 for r in qual_rows if (r['_median_rate'] or 0) >= flag_t)
            qual_ids = [r['id'] for r in qual_rows]

            flagged_games = Game.objects.filter(
                concept_id__in=denom_ids, shovelware_status='auto_flagged',
            ).count()

            top_genres = list(
                Genre.objects
                .annotate(c=Count('genre_concepts', filter=Q(genre_concepts__concept_id__in=qual_ids)))
                .filter(c__gt=0).order_by('-c', 'name')
                .values_list('name', 'c')[:4]
            )
            top_themes = list(
                Theme.objects
                .annotate(c=Count('theme_concepts', filter=Q(theme_concepts__concept_id__in=qual_ids)))
                .filter(c__gt=0).order_by('-c', 'name')
                .values_list('name', 'c')[:4]
            )

            samples = sorted(qual_rows, key=lambda r: r['_median_rate'] or 0, reverse=True)[:sample_count]

            rows.append({
                'company': company,
                'whitelisted': entry.is_whitelisted,
                'denom': denom,
                'num_evidence': num_evidence,
                'num_enter': num_enter,
                'flagged_games': flagged_games,
                'genres': top_genres,
                'themes': top_themes,
                'samples': samples,
            })

        if not rows:
            self.stdout.write(self.style.SUCCESS("No blacklisted developers to review."))
            return

        # Most-impactful first: how many games each blacklist currently flags.
        rows.sort(key=lambda r: (r['flagged_games'], r['num_evidence']), reverse=True)
        if limit is not None:
            rows = rows[:limit]

        total_flagged = sum(r['flagged_games'] for r in rows)
        self.stdout.write(
            f"Blacklisted developers: {len(rows)}  "
            f"(flagging {total_flagged} game(s) across their catalogs)\n"
        )

        for r in rows:
            company = r['company']
            denom = r['denom'] or 1  # guard against divide-by-zero in display only
            enter_pct = round(100 * r['num_enter'] / denom)
            evidence_pct = round(100 * r['num_evidence'] / denom)

            self.stdout.write("=" * 80)
            wl = "  whitelisted=True" if r['whitelisted'] else ""
            self.stdout.write(f"{company.name}  (company_id={company.id}){wl}")
            self.stdout.write(
                f"  proportion: enter(median>={flag_t:.0f}%) {r['num_enter']}/{r['denom']} = {enter_pct}%"
                f"   evidence(median>={evidence_t:.0f}%) {r['num_evidence']}/{r['denom']} = {evidence_pct}%"
            )
            if r['genres']:
                self.stdout.write("  genres: " + ", ".join(f"{n} ({c})" for n, c in r['genres']))
            if r['themes']:
                self.stdout.write("  themes: " + ", ".join(f"{n} ({c})" for n, c in r['themes']))
            self.stdout.write(f"  auto-flagged games in rated concepts: {r['flagged_games']}")
            if r['samples']:
                self.stdout.write("  top qualifying concepts (median plat %):")
                for s in r['samples']:
                    title = s['unified_title'] or '(untitled)'
                    self.stdout.write(
                        f"     {(s['_median_rate'] or 0):5.1f}%  {title} [concept {s['concept_id']}]"
                    )

        self.stdout.write("=" * 80)
        self.stdout.write(
            "\nTo exempt a legitimate studio, set is_whitelisted=True on its "
            "DeveloperReputation entry in admin (this clears its flags immediately)."
        )
