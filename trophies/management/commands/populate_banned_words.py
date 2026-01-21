"""
Django management command to populate the BannedWord table with common inappropriate words.

Usage:
    python manage.py populate_banned_words
    python manage.py populate_banned_words --clear  # Clear existing words first
    python manage.py populate_banned_words --dry-run  # Preview what would be added
"""
from django.core.management.base import BaseCommand
from django.core.cache import cache
from trophies.models import BannedWord
from users.models import CustomUser


class Command(BaseCommand):
    help = 'Populate the BannedWord table with common inappropriate words'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear all existing banned words before adding new ones',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Preview what would be added without making changes',
        )

    def handle(self, *args, **options):
        # Get the system user (or create one) for tracking who added these words
        system_user, _ = CustomUser.objects.get_or_create(
            username='system',
            defaults={'email': 'system@platpursuit.com', 'is_staff': True}
        )

        # Common inappropriate words to ban
        # These are basic examples - customize this list for your community
        banned_words_list = [
            # Profanity (basic examples - add more as needed)
            {'word': 'fuck', 'use_boundaries': True, 'notes': 'Common profanity'},
            {'word': 'shit', 'use_boundaries': True, 'notes': 'Common profanity'},
            {'word': 'ass', 'use_boundaries': True, 'notes': 'Common profanity'},
            {'word': 'damn', 'use_boundaries': True, 'notes': 'Common profanity'},
            {'word': 'bitch', 'use_boundaries': True, 'notes': 'Common profanity'},
            {'word': 'bastard', 'use_boundaries': True, 'notes': 'Common profanity'},
            {'word': 'crap', 'use_boundaries': True, 'notes': 'Common profanity'},

            # Slurs and hate speech (add more specific ones as needed)
            {'word': 'retard', 'use_boundaries': True, 'notes': 'Ableist slur'},
            {'word': 'retarded', 'use_boundaries': True, 'notes': 'Ableist slur'},

            # Spam-related
            {'word': 'click here', 'use_boundaries': False, 'notes': 'Spam indicator'},
            {'word': 'buy now', 'use_boundaries': False, 'notes': 'Spam indicator'},
            {'word': 'free money', 'use_boundaries': False, 'notes': 'Spam indicator'},
            {'word': 'bit.ly/', 'use_boundaries': False, 'notes': 'Link shortener spam'},

            # Common leetspeak variations (examples)
            {'word': 'f*ck', 'use_boundaries': True, 'notes': 'Profanity variation'},
            {'word': 'sh*t', 'use_boundaries': True, 'notes': 'Profanity variation'},
            {'word': 'a$$', 'use_boundaries': True, 'notes': 'Profanity variation'},
        ]

        if options['clear'] and not options['dry_run']:
            count = BannedWord.objects.all().count()
            BannedWord.objects.all().delete()
            self.stdout.write(self.style.WARNING(f'Cleared {count} existing banned words'))
            # Clear cache
            cache.delete('banned_words:active')

        added = 0
        skipped = 0
        updated = 0

        for word_data in banned_words_list:
            word = word_data['word']
            use_boundaries = word_data['use_boundaries']
            notes = word_data.get('notes', '')

            if options['dry_run']:
                exists = BannedWord.objects.filter(word=word).exists()
                status = 'EXISTS' if exists else 'NEW'
                boundary_status = 'with boundaries' if use_boundaries else 'substring match'
                self.stdout.write(f'[{status}] "{word}" ({boundary_status}) - {notes}')
                if not exists:
                    added += 1
                else:
                    skipped += 1
            else:
                obj, created = BannedWord.objects.get_or_create(
                    word=word,
                    defaults={
                        'use_word_boundaries': use_boundaries,
                        'added_by': system_user,
                        'notes': notes,
                        'is_active': True,
                    }
                )

                if created:
                    added += 1
                    self.stdout.write(
                        self.style.SUCCESS(f'✓ Added: "{word}" ({notes})')
                    )
                else:
                    # Update existing word if settings changed
                    if obj.use_word_boundaries != use_boundaries or obj.notes != notes:
                        obj.use_word_boundaries = use_boundaries
                        obj.notes = notes
                        obj.save()
                        updated += 1
                        self.stdout.write(
                            self.style.WARNING(f'↻ Updated: "{word}" ({notes})')
                        )
                    else:
                        skipped += 1
                        self.stdout.write(f'  Skipped (exists): "{word}"')

        # Clear cache after adding words
        if not options['dry_run']:
            cache.delete('banned_words:active')
            self.stdout.write(self.style.SUCCESS('\nCleared banned words cache'))

        # Summary
        self.stdout.write('\n' + '='*60)
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('DRY RUN - No changes made'))
            self.stdout.write(f'Would add: {added} new banned words')
            self.stdout.write(f'Already exist: {skipped} words')
        else:
            self.stdout.write(self.style.SUCCESS(f'✓ Added: {added} new banned words'))
            if updated > 0:
                self.stdout.write(self.style.WARNING(f'↻ Updated: {updated} existing words'))
            self.stdout.write(f'  Skipped: {skipped} words (already exist)')
            self.stdout.write(f'\nTotal active banned words: {BannedWord.objects.filter(is_active=True).count()}')
        self.stdout.write('='*60)
