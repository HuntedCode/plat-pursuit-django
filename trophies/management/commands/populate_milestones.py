"""
Populate milestone definitions and associated titles.

Idempotent: safe to re-run. Uses update_or_create keyed on milestone name,
so re-running will update existing milestones with any changed definitions.
Creates Title objects for milestones that have title rewards.

Usage:
    python manage.py populate_milestones           # Create/update all milestones + titles
    python manage.py populate_milestones --dry-run  # Preview without writing to DB
"""
from django.core.management.base import BaseCommand
from trophies.models import Milestone, Title


# fmt: off
MILESTONE_DEFINITIONS = [
    # ── rating_count (20 tiers, max 500) ────────────────────────────────
    {'name': 'First Impression',      'criteria_type': 'rating_count', 'criteria_details': {'target': 1},   'description': 'You rated your first game. Your voice matters here.',                  'title_name': 'Opinionated'},
    {'name': 'Three Stars',           'criteria_type': 'rating_count', 'criteria_details': {'target': 3},   'description': 'Three ratings in. You have thoughts.'},
    {'name': 'Getting Vocal',         'criteria_type': 'rating_count', 'criteria_details': {'target': 5},   'description': 'Five games rated. The community is listening.'},
    {'name': 'Critic in Training',    'criteria_type': 'rating_count', 'criteria_details': {'target': 10},  'description': 'Ten games rated. The community values your input.'},
    {'name': 'Thoughtful',            'criteria_type': 'rating_count', 'criteria_details': {'target': 15},  'description': 'Fifteen ratings. You consider each game carefully.',                   'title_name': 'Reviewer'},
    {'name': 'Regular Reviewer',      'criteria_type': 'rating_count', 'criteria_details': {'target': 20},  'description': 'Twenty games rated. A reliable opinion.'},
    {'name': 'Well-Rounded',          'criteria_type': 'rating_count', 'criteria_details': {'target': 30},  'description': 'Thirty ratings. You have seen it all.'},
    {'name': 'Experienced Critic',    'criteria_type': 'rating_count', 'criteria_details': {'target': 40},  'description': 'Forty games rated. Your perspective is seasoned.'},
    {'name': 'Seasoned Reviewer',     'criteria_type': 'rating_count', 'criteria_details': {'target': 50},  'description': 'Fifty games rated. A trusted voice in the community.'},
    {'name': 'Discerning Taste',      'criteria_type': 'rating_count', 'criteria_details': {'target': 65},  'description': 'Sixty-five ratings. You know quality when you see it.',                'title_name': 'Connoisseur'},
    {'name': 'Game Connoisseur',      'criteria_type': 'rating_count', 'criteria_details': {'target': 80},  'description': 'Eighty games rated. A refined palate.'},
    {'name': "Rate 'Em All",          'criteria_type': 'rating_count', 'criteria_details': {'target': 100}, 'description': 'One hundred ratings. You have played and judged them all.'},
    {'name': 'Beyond a Hundred',      'criteria_type': 'rating_count', 'criteria_details': {'target': 125}, 'description': 'One hundred and twenty-five. Still going strong.'},
    {'name': 'Serial Rater',          'criteria_type': 'rating_count', 'criteria_details': {'target': 150}, 'description': 'One hundred and fifty ratings. You rate everything you touch.'},
    {'name': 'Two Hundred Strong',    'criteria_type': 'rating_count', 'criteria_details': {'target': 200}, 'description': 'Two hundred ratings. A pillar of the community.',                     'title_name': 'Critic'},
    {'name': 'Quarter Thousand',      'criteria_type': 'rating_count', 'criteria_details': {'target': 250}, 'description': 'Two hundred and fifty ratings. A quarter of a thousand.',          'title_name': 'Pundit'},
    {'name': 'Prolific Critic',       'criteria_type': 'rating_count', 'criteria_details': {'target': 300}, 'description': 'Three hundred ratings. Your library is a review index.',              'title_name': 'Prolific Critic'},
    {'name': 'Rating Machine',        'criteria_type': 'rating_count', 'criteria_details': {'target': 375}, 'description': 'Three hundred and seventy-five. The reviews never stop.',            'title_name': 'Rating Savant'},
    {'name': 'Rating Legend',         'criteria_type': 'rating_count', 'criteria_details': {'target': 450}, 'description': 'Four hundred and fifty ratings. Few have rated so many.',            'title_name': 'Rating Legend'},
    {'name': 'The Final Verdict',     'criteria_type': 'rating_count', 'criteria_details': {'target': 500}, 'description': 'Five hundred ratings. The ultimate authority on quality.',             'title_name': 'The Authority'},

    # ── unique_badge_count (20 tiers, max 100) ────────────────────────────
    {'name': 'First Discovery',         'criteria_type': 'unique_badge_count', 'criteria_details': {'target': 1},   'description': 'Your first unique badge discovered. The world of badges opens up.',    'title_name': 'Seeker'},
    {'name': 'Dual Discovery',          'criteria_type': 'unique_badge_count', 'criteria_details': {'target': 2},   'description': 'Two unique badges discovered. You are branching out.'},
    {'name': 'Early Explorer',          'criteria_type': 'unique_badge_count', 'criteria_details': {'target': 3},   'description': 'Three unique badges. The collection is taking shape.'},
    {'name': 'Curious Collector',       'criteria_type': 'unique_badge_count', 'criteria_details': {'target': 5},   'description': 'Five unique badges. Curiosity drives the hunt.'},
    {'name': 'Badge Scout',             'criteria_type': 'unique_badge_count', 'criteria_details': {'target': 8},   'description': 'Eight unique badges scouted. You know where to look.',                  'title_name': 'Scout'},
    {'name': 'Double Digits Diverse',   'criteria_type': 'unique_badge_count', 'criteria_details': {'target': 10},  'description': 'Ten unique badges. Your reach is expanding.'},
    {'name': 'Broadening Horizons',     'criteria_type': 'unique_badge_count', 'criteria_details': {'target': 15},  'description': 'Fifteen unique badges. Horizons are wide open.'},
    {'name': 'Twenty Unique',           'criteria_type': 'unique_badge_count', 'criteria_details': {'target': 20},  'description': 'Twenty unique badges. A diverse portfolio of pursuits.'},
    {'name': 'Quarter Century Unique',  'criteria_type': 'unique_badge_count', 'criteria_details': {'target': 25},  'description': 'Twenty-five unique badges. One-quarter of a hundred.'},
    {'name': 'Thirty Explored',         'criteria_type': 'unique_badge_count', 'criteria_details': {'target': 30},  'description': 'Thirty unique badges in your collection.',                              'title_name': 'Trailblazer'},
    {'name': 'Forty Frontiers',         'criteria_type': 'unique_badge_count', 'criteria_details': {'target': 40},  'description': 'Forty unique badges. Pushing into new frontiers.'},
    {'name': 'Fifty Finds',             'criteria_type': 'unique_badge_count', 'criteria_details': {'target': 50},  'description': 'Fifty unique badges discovered. Halfway to mastery.'},
    {'name': 'Cartographer of Badges',  'criteria_type': 'unique_badge_count', 'criteria_details': {'target': 55},  'description': 'Fifty-five unique badges. Mapping the badge world.'},
    {'name': 'Badge Prospector',        'criteria_type': 'unique_badge_count', 'criteria_details': {'target': 60},  'description': 'Sixty unique badges. Striking gold everywhere.'},
    {'name': 'Vast Collection',         'criteria_type': 'unique_badge_count', 'criteria_details': {'target': 65},  'description': 'Sixty-five unique badges. Your collection is vast.',                    'title_name': 'Curator'},
    {'name': 'Badge Archaeologist',     'criteria_type': 'unique_badge_count', 'criteria_details': {'target': 70},  'description': 'Seventy unique badges unearthed. You dig deep.',                       'title_name': 'Archaeologist'},
    {'name': 'Badge Voyager',           'criteria_type': 'unique_badge_count', 'criteria_details': {'target': 75},  'description': 'Seventy-five unique badges. The voyage continues.',                    'title_name': 'Voyager'},
    {'name': 'Badge Pioneer',           'criteria_type': 'unique_badge_count', 'criteria_details': {'target': 80},  'description': 'Eighty unique badges. Pioneering uncharted territory.',                 'title_name': 'Pioneer'},
    {'name': 'Badge Expedition',        'criteria_type': 'unique_badge_count', 'criteria_details': {'target': 90},  'description': 'Ninety unique badges. The final expedition begins.',                    'title_name': 'Expedition Captain'},
    {'name': 'Badge Omnibus',           'criteria_type': 'unique_badge_count', 'criteria_details': {'target': 100}, 'description': 'One hundred unique badges collected. Every corner explored.',            'title_name': 'The Cataloger'},

    # ── stage_count (20 tiers, max 1,000) ───────────────────────────────
    {'name': 'Stage One',             'criteria_type': 'stage_count', 'criteria_details': {'target': 1},    'description': 'Your first badge stage completed. One step at a time.',               'title_name': 'Starter'},
    {'name': 'Early Steps',           'criteria_type': 'stage_count', 'criteria_details': {'target': 3},    'description': 'Three stages done. Finding your footing.'},
    {'name': 'Stage Starter',         'criteria_type': 'stage_count', 'criteria_details': {'target': 5},    'description': 'Five badge stages completed. You are on the path.'},
    {'name': 'Gaining Ground',        'criteria_type': 'stage_count', 'criteria_details': {'target': 8},    'description': 'Eight stages. Steady progress.'},
    {'name': 'Dozen Stages',          'criteria_type': 'stage_count', 'criteria_details': {'target': 12},   'description': 'Twelve stages completed. A solid dozen.',                             'title_name': 'Stage Runner'},
    {'name': 'Stage Runner',          'criteria_type': 'stage_count', 'criteria_details': {'target': 18},   'description': 'Eighteen stages. Building real momentum.'},
    {'name': 'Quarter Hundred',       'criteria_type': 'stage_count', 'criteria_details': {'target': 25},   'description': 'Twenty-five stages done. A quarter of a hundred.'},
    {'name': 'Stage Strider',         'criteria_type': 'stage_count', 'criteria_details': {'target': 35},   'description': 'Thirty-five stages. Striding through badge content.'},
    {'name': 'Stage Veteran',         'criteria_type': 'stage_count', 'criteria_details': {'target': 50},   'description': 'Fifty stages completed. You know these badges inside and out.'},
    {'name': 'Stage Expert',          'criteria_type': 'stage_count', 'criteria_details': {'target': 75},   'description': 'Seventy-five stages. Expert-level progress.',                         'title_name': 'Stage Master'},
    {'name': 'Stage Master',          'criteria_type': 'stage_count', 'criteria_details': {'target': 100},  'description': 'One hundred stages. A true badge scholar.'},
    {'name': 'Stage Warrior',         'criteria_type': 'stage_count', 'criteria_details': {'target': 150},  'description': 'One hundred and fifty stages. A badge warrior.'},
    {'name': 'Stage Commander',       'criteria_type': 'stage_count', 'criteria_details': {'target': 200},  'description': 'Two hundred stages. Commanding respect.'},
    {'name': 'Stage Dominator',       'criteria_type': 'stage_count', 'criteria_details': {'target': 250},  'description': 'Two hundred and fifty stages. Dominating badge content.'},
    {'name': 'Stage Legend',          'criteria_type': 'stage_count', 'criteria_details': {'target': 350},  'description': 'Three hundred and fifty stages. Your legend grows.',                  'title_name': 'Stage Overlord'},
    {'name': 'Stage Conqueror',       'criteria_type': 'stage_count', 'criteria_details': {'target': 450},  'description': 'Four hundred and fifty stages. Conquering all in your path.',   'title_name': 'Stage Conqueror'},
    {'name': 'Stage Colossus',        'criteria_type': 'stage_count', 'criteria_details': {'target': 550},  'description': 'Five hundred and fifty stages. A colossal achievement.',           'title_name': 'Stage Colossus'},
    {'name': 'Stage Overlord',        'criteria_type': 'stage_count', 'criteria_details': {'target': 700},  'description': 'Seven hundred stages. The overlord of badges.',                    'title_name': 'Stage Titan'},
    {'name': 'Stage Ascendant',       'criteria_type': 'stage_count', 'criteria_details': {'target': 850},  'description': 'Eight hundred and fifty stages. Ascending to new heights.',        'title_name': 'Stage Ascendant'},
    {'name': 'Stage Transcendent',    'criteria_type': 'stage_count', 'criteria_details': {'target': 1000}, 'description': 'One thousand stages. Is there anything left to complete?',            'title_name': 'Stage Eternal'},

    # ── is_premium (1 tier, one-off) ────────────────────────────────────
    {'name': 'Premium Member',        'criteria_type': 'is_premium', 'criteria_details': {'target': 1}, 'description': 'Welcome to premium. Thank you for supporting Platinum Pursuit.',           'title_name': 'Subscriber'},

    # ── psn_linked (1 tier, one-off) ────────────────────────────────────
    {'name': 'Identity Confirmed',    'criteria_type': 'psn_linked', 'criteria_details': {'target': 1}, 'description': 'PSN profile linked. Welcome to the pursuit, hunter.',                      'title_name': 'Hunter'},

    # ── discord_linked (1 tier, one-off) ────────────────────────────────
    {'name': 'Connected',             'criteria_type': 'discord_linked', 'criteria_details': {'target': 1}, 'description': 'Discord linked. You are part of the inner circle now.'},

    # ── manual (special milestones, granted programmatically) ──────────
    {'name': 'Badge Artwork Patron',  'criteria_type': 'manual', 'criteria_details': {'target': 1}, 'description': 'Donated to the Badge Artwork Fundraiser. Your generosity brings our badges to life.', 'title_name': 'Patron of the Arts'},
    {'name': 'Platinum Race Winner',  'criteria_type': 'manual', 'criteria_details': {'target': 1}, 'description': 'Won an official Plat Pursuit Plat Race! Gotta go fast!', 'title_name': 'Fastest Plat in the West'},
    {'name': 'Unboxed!',             'criteria_type': 'manual', 'criteria_details': {'target': 1}, 'description': 'The reel spinner giveth. 0.1% of the time, it giveth a knife. Congratulations, you beautiful anomaly.', 'title_name': 'Case Hardened'},

]
# fmt: on


class Command(BaseCommand):
    help = 'Populate milestone definitions and associated titles. Idempotent: safe to re-run.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview changes without writing to DB',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        created_count = 0
        updated_count = 0
        skipped_count = 0
        titles_created = 0

        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN MODE: No changes will be made.\n'))

        for defn in MILESTONE_DEFINITIONS:
            name = defn['name']
            title_name = defn.get('title_name')

            if dry_run:
                exists = Milestone.objects.filter(name=name).exists()
                if exists:
                    self.stdout.write(f'  [SKIP] {name}')
                    skipped_count += 1
                else:
                    title_str = f' (Title: {title_name})' if title_name else ''
                    self.stdout.write(f'  [CREATE] {name}{title_str}')
                    created_count += 1
                continue

            # Create Title if specified
            title_obj = None
            if title_name:
                title_obj, t_created = Title.objects.get_or_create(name=title_name)
                if t_created:
                    titles_created += 1

            milestone, created = Milestone.objects.update_or_create(
                name=name,
                defaults={
                    'description': defn['description'],
                    'criteria_type': defn['criteria_type'],
                    'criteria_details': defn['criteria_details'],
                    'premium_only': defn.get('premium_only', False),
                    'title': title_obj,
                },
            )
            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f'  Created: {name}'))
            else:
                updated_count += 1
                self.stdout.write(f'  Updated: {name}')

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS(
            f'Done. Milestones: {created_count} created, {updated_count} updated, '
            f'{skipped_count} skipped. Titles: {titles_created} created.'
        ))
