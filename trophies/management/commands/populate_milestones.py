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
    # ── plat_count (20 tiers, max 1,000) ────────────────────────────────
    {'name': 'First Blood',           'criteria_type': 'plat_count', 'criteria_details': {'target': 1},    'description': 'Your first platinum. The one that started it all.',                      'title_name': 'Newcomer'},
    {'name': 'Hat Trick',             'criteria_type': 'plat_count', 'criteria_details': {'target': 3},    'description': 'Three platinums. You are getting the hang of this.'},
    {'name': 'Hooked',                'criteria_type': 'plat_count', 'criteria_details': {'target': 5},    'description': 'Five platinums down. There is no turning back now.'},
    {'name': 'Double Digits',         'criteria_type': 'plat_count', 'criteria_details': {'target': 10},   'description': 'Ten platinums earned. You are officially a hunter.'},
    {'name': 'On a Roll',             'criteria_type': 'plat_count', 'criteria_details': {'target': 15},   'description': 'Fifteen platinums. The momentum is building.',                          'title_name': 'Plat Hunter'},
    {'name': 'Score!',                'criteria_type': 'plat_count', 'criteria_details': {'target': 20},   'description': 'Twenty platinums. A nice round number.'},
    {'name': 'Quarter Century',       'criteria_type': 'plat_count', 'criteria_details': {'target': 25},   'description': 'Twenty-five platinums. The collection grows.'},
    {'name': 'Trophy Case Upgrade',   'criteria_type': 'plat_count', 'criteria_details': {'target': 35},   'description': 'Thirty-five platinums. You need a bigger shelf.'},
    {'name': 'The Grind Begins',      'criteria_type': 'plat_count', 'criteria_details': {'target': 50},   'description': 'Fifty platinums. That shelf is getting heavy.'},
    {'name': 'Dedicated Hunter',      'criteria_type': 'plat_count', 'criteria_details': {'target': 75},   'description': 'Seventy-five platinums. This is your calling.',                         'title_name': 'Veteran Hunter'},
    {'name': 'Centurion',             'criteria_type': 'plat_count', 'criteria_details': {'target': 100},  'description': 'One hundred platinums. A milestone worthy of legend.'},
    {'name': 'Beyond the Century',    'criteria_type': 'plat_count', 'criteria_details': {'target': 150},  'description': 'One hundred and fifty. The journey continues.'},
    {'name': 'Double Century',        'criteria_type': 'plat_count', 'criteria_details': {'target': 200},  'description': 'Two hundred platinums. Twice the legend.'},
    {'name': 'Platinum Hoarder',      'criteria_type': 'plat_count', 'criteria_details': {'target': 250},  'description': 'Two hundred and fifty platinums. You breathe trophies.'},
    {'name': 'Triple Century',        'criteria_type': 'plat_count', 'criteria_details': {'target': 300},  'description': 'Three hundred platinums. A staggering achievement.',                    'title_name': 'Platinum Elite'},
    {'name': 'Unstoppable',           'criteria_type': 'plat_count', 'criteria_details': {'target': 400},  'description': 'Four hundred platinums. Nothing can slow you down.'},
    {'name': 'Trophy Titan',          'criteria_type': 'plat_count', 'criteria_details': {'target': 500},  'description': 'Five hundred platinums. Few have walked this path.'},
    {'name': 'Living Legend',         'criteria_type': 'plat_count', 'criteria_details': {'target': 650},  'description': 'Six hundred and fifty platinums. Stories are told about you.'},
    {'name': 'Platinum Ascendant',    'criteria_type': 'plat_count', 'criteria_details': {'target': 800},  'description': 'Eight hundred platinums. You have ascended beyond.'},
    {'name': 'The Platinum Pursuit',  'criteria_type': 'plat_count', 'criteria_details': {'target': 1000}, 'description': 'One thousand platinums. You have completed the pursuit.',               'title_name': 'The Pursuer'},

    # ── trophy_count (20 tiers, max 50,000) ─────────────────────────────
    {'name': 'Getting Started',       'criteria_type': 'trophy_count', 'criteria_details': {'target': 50},    'description': 'Fifty trophies earned. Welcome to the hunt.',                       'title_name': 'Collector'},
    {'name': 'Trophy Rookie',         'criteria_type': 'trophy_count', 'criteria_details': {'target': 100},   'description': 'One hundred trophies in the case. The journey begins.'},
    {'name': 'Bronze Collector',      'criteria_type': 'trophy_count', 'criteria_details': {'target': 200},   'description': 'Two hundred trophies. Building a solid foundation.'},
    {'name': 'Rising Hunter',         'criteria_type': 'trophy_count', 'criteria_details': {'target': 350},   'description': 'Three hundred and fifty trophies and climbing.'},
    {'name': 'Silver Streak',         'criteria_type': 'trophy_count', 'criteria_details': {'target': 500},   'description': 'Five hundred trophies and counting.',                                'title_name': 'Trophy Hound'},
    {'name': 'Trophy Apprentice',     'criteria_type': 'trophy_count', 'criteria_details': {'target': 750},   'description': 'Seven hundred and fifty trophies. Learning the craft.'},
    {'name': 'Thousand Club',         'criteria_type': 'trophy_count', 'criteria_details': {'target': 1000},  'description': 'Welcome to the thousand trophy club.'},
    {'name': 'Serious Collector',     'criteria_type': 'trophy_count', 'criteria_details': {'target': 1500},  'description': 'Fifteen hundred trophies. Getting serious.'},
    {'name': 'Two Thousand Strong',   'criteria_type': 'trophy_count', 'criteria_details': {'target': 2000},  'description': 'Two thousand trophies. That is commitment.'},
    {'name': 'Trophy Vault',          'criteria_type': 'trophy_count', 'criteria_details': {'target': 2500},  'description': 'Your vault is overflowing with accomplishments.',                    'title_name': 'Trophy Warden'},
    {'name': 'Trophy Fanatic',        'criteria_type': 'trophy_count', 'criteria_details': {'target': 3500},  'description': 'Three thousand five hundred. Trophy hunting is life.'},
    {'name': 'Trophy Treasury',       'criteria_type': 'trophy_count', 'criteria_details': {'target': 5000},  'description': 'Five thousand trophies. A treasury of memories.'},
    {'name': 'Trophy Hoarder',        'criteria_type': 'trophy_count', 'criteria_details': {'target': 7500},  'description': 'Seven thousand five hundred. Where do you put them all?'},
    {'name': 'Stockpiler',            'criteria_type': 'trophy_count', 'criteria_details': {'target': 10000}, 'description': 'Ten thousand trophies. This is a lifestyle.'},
    {'name': 'Trophy Overlord',       'criteria_type': 'trophy_count', 'criteria_details': {'target': 15000}, 'description': 'Fifteen thousand trophies. You rule this domain.',                   'title_name': 'Trophy Sovereign'},
    {'name': 'Trophy Warlord',        'criteria_type': 'trophy_count', 'criteria_details': {'target': 20000}, 'description': 'Twenty thousand trophies. A force of nature.'},
    {'name': 'Trophy Monarch',        'criteria_type': 'trophy_count', 'criteria_details': {'target': 25000}, 'description': 'Twenty-five thousand trophies. All hail the monarch.'},
    {'name': 'Trophy Colossus',       'criteria_type': 'trophy_count', 'criteria_details': {'target': 30000}, 'description': 'Thirty thousand trophies. A towering achievement.'},
    {'name': 'Trophy Demigod',        'criteria_type': 'trophy_count', 'criteria_details': {'target': 40000}, 'description': 'Forty thousand trophies. Approaching godhood.'},
    {'name': 'Trophy Transcendent',   'criteria_type': 'trophy_count', 'criteria_details': {'target': 50000}, 'description': 'Fifty thousand trophies. Beyond mortal comprehension.',              'title_name': 'Trophy Eternal'},

    # ── playtime_hours (20 tiers, max 10,000) ───────────────────────────
    {'name': 'Getting Comfy',         'criteria_type': 'playtime_hours', 'criteria_details': {'target': 25},    'description': 'Twenty-five hours in. Just settling in.',                         'title_name': 'Casual'},
    {'name': 'Weekend Warrior',       'criteria_type': 'playtime_hours', 'criteria_details': {'target': 50},    'description': 'Fifty hours. A solid weekend\'s worth.'},
    {'name': 'Hundred Hours',         'criteria_type': 'playtime_hours', 'criteria_details': {'target': 100},   'description': 'One hundred hours of playtime logged.'},
    {'name': 'Regular',               'criteria_type': 'playtime_hours', 'criteria_details': {'target': 175},   'description': 'One hundred and seventy-five hours. Gaming is your thing.'},
    {'name': 'Committed',             'criteria_type': 'playtime_hours', 'criteria_details': {'target': 250},   'description': 'Two hundred and fifty hours. You show up consistently.',            'title_name': 'Dedicated'},
    {'name': 'Enthusiast',            'criteria_type': 'playtime_hours', 'criteria_details': {'target': 400},   'description': 'Four hundred hours. More than a casual hobby.'},
    {'name': 'Half a Grand',          'criteria_type': 'playtime_hours', 'criteria_details': {'target': 500},   'description': 'Five hundred hours. Gaming is more than a hobby now.'},
    {'name': 'Absorbed',              'criteria_type': 'playtime_hours', 'criteria_details': {'target': 750},   'description': 'Seven hundred and fifty hours. Fully immersed.'},
    {'name': 'Time Well Spent',       'criteria_type': 'playtime_hours', 'criteria_details': {'target': 1000},  'description': 'One thousand hours. Or so you keep telling yourself.'},
    {'name': 'Deeply Invested',       'criteria_type': 'playtime_hours', 'criteria_details': {'target': 1500},  'description': 'Fifteen hundred hours in the chair.',                              'title_name': 'Veteran'},
    {'name': 'Two Thousand Hours',    'criteria_type': 'playtime_hours', 'criteria_details': {'target': 2000},  'description': 'Two thousand hours. That is a lot of controller time.'},
    {'name': 'Seasoned',              'criteria_type': 'playtime_hours', 'criteria_details': {'target': 2500},  'description': 'Two thousand five hundred hours in the trenches.'},
    {'name': 'Marathon Gamer',        'criteria_type': 'playtime_hours', 'criteria_details': {'target': 3000},  'description': 'Three thousand hours. You play the long game.'},
    {'name': 'Iron Endurance',        'criteria_type': 'playtime_hours', 'criteria_details': {'target': 3500},  'description': 'Three thousand five hundred hours. Unstoppable.'},
    {'name': 'Tireless',              'criteria_type': 'playtime_hours', 'criteria_details': {'target': 4000},  'description': 'Four thousand hours. You do not know the meaning of a break.',      'title_name': 'Timeless'},
    {'name': 'Lifetime Gamer',        'criteria_type': 'playtime_hours', 'criteria_details': {'target': 5000},  'description': 'Five thousand hours. A lifetime of gaming memories.'},
    {'name': 'Beyond Dedication',     'criteria_type': 'playtime_hours', 'criteria_details': {'target': 6000},  'description': 'Six thousand hours. This transcends dedication.'},
    {'name': 'All-Timer',             'criteria_type': 'playtime_hours', 'criteria_details': {'target': 7500},  'description': 'Seven thousand five hundred hours. Time has no meaning.'},
    {'name': 'Eternal Player',        'criteria_type': 'playtime_hours', 'criteria_details': {'target': 9000},  'description': 'Nine thousand hours. Gaming is eternal.'},
    {'name': 'No Life (Affectionate)','criteria_type': 'playtime_hours', 'criteria_details': {'target': 10000}, 'description': 'Ten thousand hours. Touch grass? Never heard of it.',               'title_name': 'The Eternal'},

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
    {'name': 'Quarter Thousand',      'criteria_type': 'rating_count', 'criteria_details': {'target': 250}, 'description': 'Two hundred and fifty ratings. A quarter of a thousand.'},
    {'name': 'Prolific Critic',       'criteria_type': 'rating_count', 'criteria_details': {'target': 300}, 'description': 'Three hundred ratings. Your library is a review index.'},
    {'name': 'Rating Machine',        'criteria_type': 'rating_count', 'criteria_details': {'target': 375}, 'description': 'Three hundred and seventy-five. The reviews never stop.'},
    {'name': 'Rating Legend',         'criteria_type': 'rating_count', 'criteria_details': {'target': 450}, 'description': 'Four hundred and fifty ratings. Few have rated so many.'},
    {'name': 'The Final Verdict',     'criteria_type': 'rating_count', 'criteria_details': {'target': 500}, 'description': 'Five hundred ratings. The ultimate authority on quality.',             'title_name': 'The Authority'},

    # ── comment_upvotes (20 tiers, max 1,000) ───────────────────────────
    {'name': 'First Like',            'criteria_type': 'comment_upvotes', 'criteria_details': {'target': 1},    'description': 'Your first upvote. Someone appreciates you.',                    'title_name': 'Conversant'},
    {'name': 'Noticed',               'criteria_type': 'comment_upvotes', 'criteria_details': {'target': 3},    'description': 'Three upvotes. People are taking notice.'},
    {'name': 'Conversation Starter',  'criteria_type': 'comment_upvotes', 'criteria_details': {'target': 5},    'description': 'Five upvotes. Your comments are resonating.'},
    {'name': 'Engaging',              'criteria_type': 'comment_upvotes', 'criteria_details': {'target': 10},   'description': 'Ten upvotes. You spark good discussions.'},
    {'name': 'Voice of the Community','criteria_type': 'comment_upvotes', 'criteria_details': {'target': 25},   'description': 'Twenty-five upvotes. People listen when you speak.',               'title_name': 'Commenter'},
    {'name': 'Well Said',             'criteria_type': 'comment_upvotes', 'criteria_details': {'target': 40},   'description': 'Forty upvotes. You have a way with words.'},
    {'name': 'Insightful',            'criteria_type': 'comment_upvotes', 'criteria_details': {'target': 60},   'description': 'Sixty upvotes. Your insights cut deep.'},
    {'name': 'Resonant',              'criteria_type': 'comment_upvotes', 'criteria_details': {'target': 80},   'description': 'Eighty upvotes. Your words resonate with many.'},
    {'name': 'Popular Opinion',       'criteria_type': 'comment_upvotes', 'criteria_details': {'target': 100},  'description': 'One hundred upvotes. Your wisdom is widely appreciated.'},
    {'name': 'Crowd Favorite',        'criteria_type': 'comment_upvotes', 'criteria_details': {'target': 135},  'description': 'One hundred and thirty-five upvotes. The crowd loves you.',       'title_name': 'Thought Leader'},
    {'name': 'Thought Leader',        'criteria_type': 'comment_upvotes', 'criteria_details': {'target': 175},  'description': 'One hundred and seventy-five upvotes. You lead the conversation.'},
    {'name': 'Community Pillar',      'criteria_type': 'comment_upvotes', 'criteria_details': {'target': 225},  'description': 'Two hundred and twenty-five upvotes. A true pillar.'},
    {'name': 'Word Warrior',          'criteria_type': 'comment_upvotes', 'criteria_details': {'target': 300},  'description': 'Three hundred upvotes. Your words are your weapon.'},
    {'name': 'Forum Legend',          'criteria_type': 'comment_upvotes', 'criteria_details': {'target': 400},  'description': 'Four hundred upvotes. Your legacy is written in comments.'},
    {'name': 'Trophy Talk Legend',    'criteria_type': 'comment_upvotes', 'criteria_details': {'target': 500},  'description': 'Five hundred upvotes. Your words carry weight.',                  'title_name': 'Community Sage'},
    {'name': 'Rising Icon',           'criteria_type': 'comment_upvotes', 'criteria_details': {'target': 600},  'description': 'Six hundred upvotes. An icon in the making.'},
    {'name': 'Upvote Magnet',         'criteria_type': 'comment_upvotes', 'criteria_details': {'target': 700},  'description': 'Seven hundred upvotes. Everything you say turns to gold.'},
    {'name': 'Community Icon',        'criteria_type': 'comment_upvotes', 'criteria_details': {'target': 800},  'description': 'Eight hundred upvotes. An icon of the community.'},
    {'name': 'Voice of Many',         'criteria_type': 'comment_upvotes', 'criteria_details': {'target': 900},  'description': 'Nine hundred upvotes. The voice of many.'},
    {'name': 'Voice of a Thousand',   'criteria_type': 'comment_upvotes', 'criteria_details': {'target': 1000}, 'description': 'One thousand upvotes. Your voice echoes across the platform.',    'title_name': 'The Oracle'},

    # ── checklist_upvotes (20 tiers, max 1,000) ─────────────────────────
    {'name': 'First Thumbs Up',       'criteria_type': 'checklist_upvotes', 'criteria_details': {'target': 1},    'description': 'Your first checklist upvote. Someone found your work helpful.', 'title_name': 'Helper'},
    {'name': 'Noticed Work',          'criteria_type': 'checklist_upvotes', 'criteria_details': {'target': 3},    'description': 'Three upvotes. Your checklists are getting attention.'},
    {'name': 'Helpful Hunter',        'criteria_type': 'checklist_upvotes', 'criteria_details': {'target': 5},    'description': 'Five people found your checklists helpful.'},
    {'name': 'Rising Guide',          'criteria_type': 'checklist_upvotes', 'criteria_details': {'target': 10},   'description': 'Ten upvotes. You are becoming a go-to resource.'},
    {'name': 'Guide Maker',           'criteria_type': 'checklist_upvotes', 'criteria_details': {'target': 25},   'description': 'Twenty-five upvotes. You are guiding fellow hunters.',          'title_name': 'Guide'},
    {'name': 'Trail Blazer',          'criteria_type': 'checklist_upvotes', 'criteria_details': {'target': 40},   'description': 'Forty upvotes. You blaze trails for others to follow.'},
    {'name': 'Community Aide',        'criteria_type': 'checklist_upvotes', 'criteria_details': {'target': 60},   'description': 'Sixty upvotes. You make the community better.'},
    {'name': 'Reliable Resource',     'criteria_type': 'checklist_upvotes', 'criteria_details': {'target': 80},   'description': 'Eighty upvotes. Hunters rely on your work.'},
    {'name': 'Path Finder',           'criteria_type': 'checklist_upvotes', 'criteria_details': {'target': 100},  'description': 'One hundred upvotes. You light the way for others.'},
    {'name': 'Wayfinder',             'criteria_type': 'checklist_upvotes', 'criteria_details': {'target': 135},  'description': 'One hundred and thirty-five upvotes. You always find the way.', 'title_name': 'Cartographer'},
    {'name': 'Guide Veteran',         'criteria_type': 'checklist_upvotes', 'criteria_details': {'target': 175},  'description': 'One hundred and seventy-five upvotes. A veteran guide maker.'},
    {'name': 'Mapmaker',              'criteria_type': 'checklist_upvotes', 'criteria_details': {'target': 225},  'description': 'Two hundred and twenty-five upvotes. Mapping the path for all.'},
    {'name': 'Expedition Leader',     'criteria_type': 'checklist_upvotes', 'criteria_details': {'target': 300},  'description': 'Three hundred upvotes. You lead expeditions to completion.'},
    {'name': 'Checklist Architect',   'criteria_type': 'checklist_upvotes', 'criteria_details': {'target': 400},  'description': 'Four hundred upvotes. Your designs are masterful.'},
    {'name': 'Checklist Champion',    'criteria_type': 'checklist_upvotes', 'criteria_details': {'target': 500},  'description': 'Five hundred upvotes. The community\'s guiding star.',          'title_name': 'Sherpa'},
    {'name': 'Guiding Light',         'criteria_type': 'checklist_upvotes', 'criteria_details': {'target': 600},  'description': 'Six hundred upvotes. A beacon for lost hunters.'},
    {'name': 'Platinum Sherpa',       'criteria_type': 'checklist_upvotes', 'criteria_details': {'target': 700},  'description': 'Seven hundred upvotes. You carry hunters to the summit.'},
    {'name': 'Master Cartographer',   'criteria_type': 'checklist_upvotes', 'criteria_details': {'target': 800},  'description': 'Eight hundred upvotes. Your maps are legendary.'},
    {'name': 'Trailhead Legend',      'criteria_type': 'checklist_upvotes', 'criteria_details': {'target': 900},  'description': 'Nine hundred upvotes. A trailhead legend.'},
    {'name': 'The Cartographer General', 'criteria_type': 'checklist_upvotes', 'criteria_details': {'target': 1000}, 'description': 'One thousand upvotes. The ultimate guide to all things platinum.', 'title_name': 'The Navigator'},

    # ── badge_count (20 tiers, max 200) ─────────────────────────────────
    {'name': 'Badge Beginner',        'criteria_type': 'badge_count', 'criteria_details': {'target': 1},   'description': 'Your first badge earned. The collection starts here.',                  'title_name': 'Badge Holder'},
    {'name': 'Badge Duo',             'criteria_type': 'badge_count', 'criteria_details': {'target': 2},   'description': 'Two badges. A budding collection.'},
    {'name': 'Tri Badge',             'criteria_type': 'badge_count', 'criteria_details': {'target': 3},   'description': 'Three badges. Things are looking good.'},
    {'name': 'Badge Collector',       'criteria_type': 'badge_count', 'criteria_details': {'target': 5},   'description': 'Five badges in the showcase. Something special is forming.'},
    {'name': 'Badge Enthusiast',      'criteria_type': 'badge_count', 'criteria_details': {'target': 8},   'description': 'Eight badges. Your dedication shows.',                                 'title_name': 'Badge Fan'},
    {'name': 'Double Digit Badges',   'criteria_type': 'badge_count', 'criteria_details': {'target': 10},  'description': 'Ten badges earned. Now we are talking.'},
    {'name': 'Badge Devotee',         'criteria_type': 'badge_count', 'criteria_details': {'target': 15},  'description': 'Fifteen badges. Devoted to the craft.'},
    {'name': 'Badge Curator',         'criteria_type': 'badge_count', 'criteria_details': {'target': 20},  'description': 'Twenty badges. A curated showcase.'},
    {'name': 'Badge Connoisseur',     'criteria_type': 'badge_count', 'criteria_details': {'target': 25},  'description': 'Twenty-five badges. A refined taste in achievements.'},
    {'name': 'Badge Scholar',         'criteria_type': 'badge_count', 'criteria_details': {'target': 35},  'description': 'Thirty-five badges. You have studied the system well.',                'title_name': 'Badge Veteran'},
    {'name': 'Badge Aficionado',      'criteria_type': 'badge_count', 'criteria_details': {'target': 50},  'description': 'Fifty badges. You have explored every corner.'},
    {'name': 'Badge Veteran',         'criteria_type': 'badge_count', 'criteria_details': {'target': 60},  'description': 'Sixty badges. A seasoned collector.'},
    {'name': 'Badge Master',          'criteria_type': 'badge_count', 'criteria_details': {'target': 75},  'description': 'Seventy-five badges. Mastery is within reach.'},
    {'name': 'Badge Commander',       'criteria_type': 'badge_count', 'criteria_details': {'target': 90},  'description': 'Ninety badges. You command an impressive collection.'},
    {'name': 'Badge Centurion',       'criteria_type': 'badge_count', 'criteria_details': {'target': 100}, 'description': 'One hundred badges. A century of achievements.',                      'title_name': 'Badge Elite'},
    {'name': 'Badge Overlord',        'criteria_type': 'badge_count', 'criteria_details': {'target': 120}, 'description': 'One hundred and twenty badges. You rule the badge realm.'},
    {'name': 'Badge Legend',          'criteria_type': 'badge_count', 'criteria_details': {'target': 140}, 'description': 'One hundred and forty badges. Legendary status.'},
    {'name': 'Badge Titan',           'criteria_type': 'badge_count', 'criteria_details': {'target': 160}, 'description': 'One hundred and sixty badges. A titan of collecting.'},
    {'name': 'Badge Colossus',        'criteria_type': 'badge_count', 'criteria_details': {'target': 180}, 'description': 'One hundred and eighty badges. A colossal collection.'},
    {'name': 'Badge Transcendent',    'criteria_type': 'badge_count', 'criteria_details': {'target': 200}, 'description': 'Two hundred badges. Beyond all expectations.',                        'title_name': 'Badge Immortal'},

    # ── completion_count (20 tiers, max 500) ────────────────────────────
    {'name': 'Clean Sweep',           'criteria_type': 'completion_count', 'criteria_details': {'target': 1},   'description': 'Your first 100% completion. Every trophy, every challenge.',     'title_name': 'Finisher'},
    {'name': 'Thorough',              'criteria_type': 'completion_count', 'criteria_details': {'target': 3},   'description': 'Three perfect completions. You leave no stone unturned.'},
    {'name': 'Completionist',         'criteria_type': 'completion_count', 'criteria_details': {'target': 5},   'description': 'Five games at 100%. You leave no trophy behind.'},
    {'name': 'Detail Oriented',       'criteria_type': 'completion_count', 'criteria_details': {'target': 8},   'description': 'Eight perfect games. You notice every detail.'},
    {'name': 'Perfectionist',         'criteria_type': 'completion_count', 'criteria_details': {'target': 10},  'description': 'Ten games at 100%. Nothing escapes your eye.',                   'title_name': 'Completionist'},
    {'name': 'Meticulous',            'criteria_type': 'completion_count', 'criteria_details': {'target': 15},  'description': 'Fifteen completions. Meticulously done.'},
    {'name': 'Twenty Perfects',       'criteria_type': 'completion_count', 'criteria_details': {'target': 20},  'description': 'Twenty games at 100%. A solid portfolio.'},
    {'name': 'Relentless',            'criteria_type': 'completion_count', 'criteria_details': {'target': 30},  'description': 'Thirty completions. You do not know "good enough."'},
    {'name': 'Precision Hunter',      'criteria_type': 'completion_count', 'criteria_details': {'target': 40},  'description': 'Forty perfect games. Surgical precision.'},
    {'name': 'Half Century Perfect',  'criteria_type': 'completion_count', 'criteria_details': {'target': 50},  'description': 'Fifty completions. Half a century of perfection.',               'title_name': 'Perfectionist'},
    {'name': 'Flawless Record',       'criteria_type': 'completion_count', 'criteria_details': {'target': 75},  'description': 'Seventy-five games at 100%. An impeccable record.'},
    {'name': 'The Hundred Percenter', 'criteria_type': 'completion_count', 'criteria_details': {'target': 100}, 'description': 'One hundred games at 100%. Peak performance.'},
    {'name': 'Beyond Perfect',        'criteria_type': 'completion_count', 'criteria_details': {'target': 125}, 'description': 'One hundred and twenty-five completions. Beyond perfection.'},
    {'name': 'Unbreakable',           'criteria_type': 'completion_count', 'criteria_details': {'target': 150}, 'description': 'One hundred and fifty completions. An unbreakable streak.'},
    {'name': 'Master Completionist',  'criteria_type': 'completion_count', 'criteria_details': {'target': 200}, 'description': 'Two hundred games at 100%. A master of the art.',                'title_name': 'Maximizer'},
    {'name': 'Quarter Thousand Perfect', 'criteria_type': 'completion_count', 'criteria_details': {'target': 250}, 'description': 'Two hundred and fifty completions. Staggering.'},
    {'name': 'Completion Machine',    'criteria_type': 'completion_count', 'criteria_details': {'target': 300}, 'description': 'Three hundred games at 100%. You are a machine.'},
    {'name': 'Completion Deity',      'criteria_type': 'completion_count', 'criteria_details': {'target': 375}, 'description': 'Three hundred and seventy-five completions. Approaching divine.'},
    {'name': 'Flawless Legend',       'criteria_type': 'completion_count', 'criteria_details': {'target': 450}, 'description': 'Four hundred and fifty completions. Flawless, legendary.'},
    {'name': 'Completion Incarnate',  'criteria_type': 'completion_count', 'criteria_details': {'target': 500}, 'description': 'Five hundred games at 100%. Perfection made manifest.',          'title_name': 'The Absolute'},

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
    {'name': 'Stage Conqueror',       'criteria_type': 'stage_count', 'criteria_details': {'target': 450},  'description': 'Four hundred and fifty stages. Conquering all in your path.'},
    {'name': 'Stage Colossus',        'criteria_type': 'stage_count', 'criteria_details': {'target': 550},  'description': 'Five hundred and fifty stages. A colossal achievement.'},
    {'name': 'Stage Overlord',        'criteria_type': 'stage_count', 'criteria_details': {'target': 700},  'description': 'Seven hundred stages. The overlord of badges.'},
    {'name': 'Stage Ascendant',       'criteria_type': 'stage_count', 'criteria_details': {'target': 850},  'description': 'Eight hundred and fifty stages. Ascending to new heights.'},
    {'name': 'Stage Transcendent',    'criteria_type': 'stage_count', 'criteria_details': {'target': 1000}, 'description': 'One thousand stages. Is there anything left to complete?',            'title_name': 'Stage Eternal'},

    # ── az_progress (10 tiers, max 52) ──────────────────────────────────
    {'name': 'Five Letters Down',     'criteria_type': 'az_progress', 'criteria_details': {'target': 5},  'description': 'Five letters completed. The alphabet awaits.'},
    {'name': 'Halfway Literate',      'criteria_type': 'az_progress', 'criteria_details': {'target': 10}, 'description': 'Ten letters done. Almost halfway through the alphabet.'},
    {'name': 'Over the Hump',         'criteria_type': 'az_progress', 'criteria_details': {'target': 15}, 'description': 'Fifteen letters completed. The finish line is in sight.'},
    {'name': 'Home Stretch',          'criteria_type': 'az_progress', 'criteria_details': {'target': 20}, 'description': 'Twenty letters down. Just six more to go.'},
    {'name': 'A-Z Conqueror',         'criteria_type': 'az_progress', 'criteria_details': {'target': 26}, 'description': 'The entire alphabet conquered. Every letter, every platinum.',        'title_name': 'Alphabet Hunter'},
    {'name': 'Second Run: Five',      'criteria_type': 'az_progress', 'criteria_details': {'target': 31}, 'description': 'Five letters into your second A-Z. Back for more.'},
    {'name': 'Second Run: Ten',       'criteria_type': 'az_progress', 'criteria_details': {'target': 36}, 'description': 'Ten letters on the second run. You know the drill.'},
    {'name': 'Second Run: Sixteen',   'criteria_type': 'az_progress', 'criteria_details': {'target': 42}, 'description': 'Sixteen letters on round two. Past the halfway mark again.'},
    {'name': 'Second Run: Twenty-One','criteria_type': 'az_progress', 'criteria_details': {'target': 47}, 'description': 'Twenty-one letters on the second run. The home stretch again.'},
    {'name': 'Double A-Z',            'criteria_type': 'az_progress', 'criteria_details': {'target': 52}, 'description': 'Two complete A-Z challenges. Double alphabet, double legend.',        'title_name': 'A-Z Legend'},

    # ── calendar_month_* (12 one-off milestones) ────────────────────────
    {'name': 'January Hunter',   'criteria_type': 'calendar_month_jan', 'criteria_details': {'target': 1}, 'description': 'Every day in January covered with a platinum.'},
    {'name': 'February Hunter',  'criteria_type': 'calendar_month_feb', 'criteria_details': {'target': 1}, 'description': 'Every day in February covered with a platinum.'},
    {'name': 'March Hunter',     'criteria_type': 'calendar_month_mar', 'criteria_details': {'target': 1}, 'description': 'Every day in March covered with a platinum.'},
    {'name': 'April Hunter',     'criteria_type': 'calendar_month_apr', 'criteria_details': {'target': 1}, 'description': 'Every day in April covered with a platinum.'},
    {'name': 'May Hunter',       'criteria_type': 'calendar_month_may', 'criteria_details': {'target': 1}, 'description': 'Every day in May covered with a platinum.'},
    {'name': 'June Hunter',      'criteria_type': 'calendar_month_jun', 'criteria_details': {'target': 1}, 'description': 'Every day in June covered with a platinum.'},
    {'name': 'July Hunter',      'criteria_type': 'calendar_month_jul', 'criteria_details': {'target': 1}, 'description': 'Every day in July covered with a platinum.'},
    {'name': 'August Hunter',    'criteria_type': 'calendar_month_aug', 'criteria_details': {'target': 1}, 'description': 'Every day in August covered with a platinum.'},
    {'name': 'September Hunter', 'criteria_type': 'calendar_month_sep', 'criteria_details': {'target': 1}, 'description': 'Every day in September covered with a platinum.'},
    {'name': 'October Hunter',   'criteria_type': 'calendar_month_oct', 'criteria_details': {'target': 1}, 'description': 'Every day in October covered with a platinum.'},
    {'name': 'November Hunter',  'criteria_type': 'calendar_month_nov', 'criteria_details': {'target': 1}, 'description': 'Every day in November covered with a platinum.'},
    {'name': 'December Hunter',  'criteria_type': 'calendar_month_dec', 'criteria_details': {'target': 1}, 'description': 'Every day in December covered with a platinum.'},

    # ── calendar_months_total (4 tiers) ─────────────────────────────────
    {'name': 'Quarterly Calendar',    'criteria_type': 'calendar_months_total', 'criteria_details': {'target': 3},  'description': 'Three months of the calendar fully covered.'},
    {'name': 'Half Year Calendar',    'criteria_type': 'calendar_months_total', 'criteria_details': {'target': 6},  'description': 'Six months complete. The calendar is taking shape.',           'title_name': 'Calendar Hunter'},
    {'name': 'Three Quarters Cal.',   'criteria_type': 'calendar_months_total', 'criteria_details': {'target': 9},  'description': 'Nine months filled. Just one season to go.'},
    {'name': 'Year-Round Hunter',     'criteria_type': 'calendar_months_total', 'criteria_details': {'target': 12}, 'description': 'Every month of the year fully covered. Incredible.',          'title_name': 'Calendar Master'},

    # ── calendar_complete (1 tier, one-off) ─────────────────────────────
    {'name': '365 Days of Platinum',  'criteria_type': 'calendar_complete', 'criteria_details': {'target': 1}, 'description': 'Every single day of the year covered. The ultimate calendar conquest.', 'title_name': 'Calendar Legend'},

    # ── subscription_months (20 tiers, max 60) ──────────────────────────
    {'name': 'First Month',             'criteria_type': 'subscription_months', 'criteria_details': {'target': 1},  'description': 'Your first month as a supporter. Welcome to the inner circle.',          'premium_only': True, 'title_name': 'Supporter'},
    {'name': 'Month Two',               'criteria_type': 'subscription_months', 'criteria_details': {'target': 2},  'description': 'Two months in. Glad you are still here.',                                'premium_only': True},
    {'name': 'Quarterly Supporter',     'criteria_type': 'subscription_months', 'criteria_details': {'target': 3},  'description': 'Three months of support. A full quarter of loyalty.',                    'premium_only': True},
    {'name': 'Sticking Around',         'criteria_type': 'subscription_months', 'criteria_details': {'target': 4},  'description': 'Four months strong. You are not going anywhere.',                       'premium_only': True},
    {'name': 'Half Year Strong',        'criteria_type': 'subscription_months', 'criteria_details': {'target': 6},  'description': 'Six months of premium. Half a year of dedication.',                     'premium_only': True, 'title_name': 'Patron'},
    {'name': 'Committed Supporter',     'criteria_type': 'subscription_months', 'criteria_details': {'target': 8},  'description': 'Eight months of support. Your commitment is real.',                     'premium_only': True},
    {'name': 'Ten Months In',           'criteria_type': 'subscription_months', 'criteria_details': {'target': 10}, 'description': 'Ten months as premium. Nearly a year.',                                 'premium_only': True},
    {'name': 'The First Year',          'criteria_type': 'subscription_months', 'criteria_details': {'target': 12}, 'description': 'One full year of premium. A true supporter.',                            'premium_only': True, 'title_name': 'Annual'},
    {'name': 'Fifteen Months',          'criteria_type': 'subscription_months', 'criteria_details': {'target': 15}, 'description': 'Fifteen months. Well past the one year mark.',                           'premium_only': True},
    {'name': 'Year and a Half',         'criteria_type': 'subscription_months', 'criteria_details': {'target': 18}, 'description': 'Eighteen months of loyalty. That is real dedication.',                   'premium_only': True, 'title_name': 'Devotee'},
    {'name': 'Twenty-One Months',       'criteria_type': 'subscription_months', 'criteria_details': {'target': 21}, 'description': 'Twenty-one months. The journey keeps going.',                            'premium_only': True},
    {'name': 'Two Year Veteran',        'criteria_type': 'subscription_months', 'criteria_details': {'target': 24}, 'description': 'Two full years of premium. A cornerstone of the community.',             'premium_only': True},
    {'name': 'Twenty-Seven Months',     'criteria_type': 'subscription_months', 'criteria_details': {'target': 27}, 'description': 'Twenty-seven months. Closing in on two and a half years.',               'premium_only': True},
    {'name': 'Two and a Half Years',    'criteria_type': 'subscription_months', 'criteria_details': {'target': 30}, 'description': 'Thirty months. You have been here since the early days.',                'premium_only': True, 'title_name': 'Stalwart'},
    {'name': 'Thirty-Three Months',     'criteria_type': 'subscription_months', 'criteria_details': {'target': 33}, 'description': 'Thirty-three months. The road stretches behind you.',                    'premium_only': True},
    {'name': 'Three Year Pillar',       'criteria_type': 'subscription_months', 'criteria_details': {'target': 36}, 'description': 'Three years of premium. You are a pillar of Platinum Pursuit.',          'premium_only': True},
    {'name': 'Forty-Two Months',        'criteria_type': 'subscription_months', 'criteria_details': {'target': 42}, 'description': 'Forty-two months. The answer to everything, including loyalty.',         'premium_only': True, 'title_name': 'Pillar'},
    {'name': 'Four Year Foundation',    'criteria_type': 'subscription_months', 'criteria_details': {'target': 48}, 'description': 'Four years of support. You are part of the foundation.',                 'premium_only': True},
    {'name': 'Fifty-Four Months',       'criteria_type': 'subscription_months', 'criteria_details': {'target': 54}, 'description': 'Fifty-four months. The summit is in view.',                              'premium_only': True},
    {'name': 'Five Year Legend',        'criteria_type': 'subscription_months', 'criteria_details': {'target': 60}, 'description': 'Five years of premium. A legend of loyalty and passion.',                'premium_only': True, 'title_name': 'Founding Patron'},

    # ── is_premium (1 tier, one-off) ────────────────────────────────────
    {'name': 'Premium Member',        'criteria_type': 'is_premium', 'criteria_details': {'target': 1}, 'description': 'Welcome to premium. Thank you for supporting Platinum Pursuit.',           'title_name': 'Subscriber'},

    # ── psn_linked (1 tier, one-off) ────────────────────────────────────
    {'name': 'Identity Confirmed',    'criteria_type': 'psn_linked', 'criteria_details': {'target': 1}, 'description': 'PSN profile linked. Welcome to the pursuit, hunter.',                      'title_name': 'Hunter'},

    # ── discord_linked (1 tier, one-off) ────────────────────────────────
    {'name': 'Connected',             'criteria_type': 'discord_linked', 'criteria_details': {'target': 1}, 'description': 'Discord linked. You are part of the inner circle now.'},
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
