"""
Django management command to populate the BannedWord table for UGC moderation
(comment bodies, and the new rating "quick take" blurbs).

Usage:
    python manage.py populate_banned_words
    python manage.py populate_banned_words --clear     # wipe existing words first
    python manage.py populate_banned_words --dry-run   # preview, make no changes

Tuning notes:
  * This is a content-moderation BLOCKLIST for a game community's user-generated text.
    Categories are grouped below so you can prune to taste.
  * `MILD_PROFANITY` is a game-review-friendly grey area (a blurb like "damn hard grind"
    is legitimate). Comment that block out if you'd rather allow casual swearing.
  * Boundary handling avoids the "Scunthorpe problem": single tokens are matched with
    word boundaries (\\bass\\b won't flag "class"); phrases / URLs match as substrings.
    Some slurs also list common plurals/variants explicitly, since \\bword\\b won't catch
    "words". Add/remove as your community needs.
  * `check_banned_words` is a plain filter, not a severity system -- a hit blocks the text.
"""
from django.core.cache import cache
from django.core.management.base import BaseCommand

from trophies.models import BannedWord
from users.models import CustomUser

# Grouped for tunability. The category name becomes the stored `notes`. Boundary mode is
# auto-derived per term (single token -> word boundaries; phrase/URL -> substring), with the
# handful of exceptions listed in SUBSTRING_OVERRIDES (short slurs that need plural coverage).
BANNED_WORDS = {
    'Strong profanity': [
        'fuck', 'fucker', 'fucking', 'fucked', 'motherfucker', 'fuckface', 'clusterfuck',
        'shit', 'shitty', 'bullshit', 'shithead', 'dipshit', 'batshit',
        'bitch', 'bitches', 'son of a bitch', 'bastard', 'prick', 'wanker', 'wank',
        'bollocks', 'twat', 'douchebag', 'asshole', 'assholes', 'dumbass', 'jackass',
        'piss', 'pissed', 'pissing',
    ],
    'Mild profanity (remove to allow casual swearing in reviews)': [
        'ass', 'arse', 'damn', 'goddamn', 'crap', 'hell', 'bloody',
    ],
    'Sexual / explicit': [
        'cunt', 'cock', 'dick', 'dickhead', 'pussy', 'porn', 'porno', 'pornographic',
        'blowjob', 'handjob', 'rimjob', 'cum', 'cumming', 'jizz', 'dildo', 'boner',
        'horny', 'gangbang', 'creampie', 'deepthroat', 'bukkake', 'nsfw', 'hentai',
        'masturbate', 'ejaculate', 'anal', 'titties', 'boobs', 'nudes',
    ],
    'Slur / hate speech': [
        # Racial / ethnic
        'nigger', 'nigga', 'niggers', 'coon', 'jigaboo', 'porchmonkey',
        'chink', 'gook', 'spic', 'spics', 'wetback', 'beaner', 'kike', 'kikes',
        'sandnigger', 'towelhead', 'raghead', 'jap', 'zipperhead', 'redskin', 'gyppo',
        # Homophobic / transphobic
        'faggot', 'faggots', 'fag', 'fags', 'faggotry', 'dyke', 'dykes',
        'tranny', 'trannies', 'shemale', 'ladyboy',
        # Ableist
        'retard', 'retards', 'retarded', 'spastic', 'mongoloid', 'cripple',
    ],
    'Spam / scam (substring match)': [
        'click here', 'buy now', 'free money', 'free robux', 'free vbucks', 'free gift card',
        'giveaway', 'promo code', 'discount code', 'act now', 'limited time offer',
        'verify your account', 'confirm your account', 'nigerian prince', 'wire transfer',
        'crypto giveaway', 'bitcoin giveaway', 'double your money', 'work from home',
        # Link / handle shorteners frequently used to route off-site
        'bit.ly/', 'tinyurl.com/', 'discord.gg/', 't.me/', 'cash.app/', 'paypal.me/',
        'onlyfans.com', 'http://', 'https://', 'www.',
    ],
    'Evasion / leetspeak variants': [
        'f*ck', 'fuk', 'fck', 'phuck', 'sh*t', 'sht', 'b*tch', 'a$$', 'a$$hole',
        'n1gger', 'n1gga', 'f4ggot', 'f@ggot', 'r3tard', 'c*nt',
        'f u c k', 's h i t',
    ],
}

# Short slurs whose plural/variant forms matter enough to justify a substring match
# despite Scunthorpe risk (these have essentially no innocent English substrings).
SUBSTRING_OVERRIDES = {'nigger', 'nigga', 'faggot', 'kike', 'wetback', 'sandnigger'}


def _use_boundaries(word):
    """Single tokens get word boundaries; phrases / URLs / spaced evasions match as substrings."""
    if word in SUBSTRING_OVERRIDES:
        return False
    return not any(c in word for c in (' ', '/', '.', '@'))


class Command(BaseCommand):
    help = 'Populate the BannedWord table with a robust UGC moderation blocklist'

    def add_arguments(self, parser):
        parser.add_argument('--clear', action='store_true',
                            help='Clear all existing banned words before adding new ones')
        parser.add_argument('--dry-run', action='store_true',
                            help='Preview what would be added without making changes')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        system_user, _ = CustomUser.objects.get_or_create(
            username='system',
            defaults={'email': 'system@platpursuit.com', 'is_staff': True},
        )

        # Flatten the grouped dict into rows, de-duplicating a word if it appears in
        # more than one category (first category wins).
        rows, seen = [], set()
        for category, words in BANNED_WORDS.items():
            for word in words:
                w = word.strip().lower()
                if not w or w in seen:
                    continue
                seen.add(w)
                rows.append({'word': w, 'use_boundaries': _use_boundaries(w), 'notes': category})

        if options['clear'] and not dry_run:
            count = BannedWord.objects.count()
            BannedWord.objects.all().delete()
            cache.delete('banned_words:active')
            self.stdout.write(self.style.WARNING(f'Cleared {count} existing banned words'))

        added = updated = skipped = 0
        for row in rows:
            word, use_boundaries, notes = row['word'], row['use_boundaries'], row['notes']

            if dry_run:
                exists = BannedWord.objects.filter(word=word).exists()
                mode = 'boundaries' if use_boundaries else 'substring'
                self.stdout.write(f"[{'EXISTS' if exists else 'NEW':>6}] \"{word}\" ({mode}) - {notes}")
                added += 0 if exists else 1
                skipped += 1 if exists else 0
                continue

            obj, created = BannedWord.objects.get_or_create(
                word=word,
                defaults={'use_word_boundaries': use_boundaries, 'added_by': system_user,
                          'notes': notes, 'is_active': True},
            )
            if created:
                added += 1
            elif obj.use_word_boundaries != use_boundaries or obj.notes != notes or not obj.is_active:
                obj.use_word_boundaries = use_boundaries
                obj.notes = notes
                obj.is_active = True
                obj.save(update_fields=['use_word_boundaries', 'notes', 'is_active'])
                updated += 1
            else:
                skipped += 1

        if not dry_run:
            cache.delete('banned_words:active')

        self.stdout.write('\n' + '=' * 60)
        if dry_run:
            self.stdout.write(self.style.WARNING('DRY RUN - no changes made'))
            self.stdout.write(f'Would add {added} new words ({skipped} already exist) '
                              f'across {len(BANNED_WORDS)} categories.')
        else:
            self.stdout.write(self.style.SUCCESS(f'Added {added} new words'))
            if updated:
                self.stdout.write(self.style.WARNING(f'Updated {updated} existing words'))
            self.stdout.write(f'Skipped {skipped} (unchanged). '
                              f'Total active: {BannedWord.objects.filter(is_active=True).count()}')
        self.stdout.write('=' * 60)
