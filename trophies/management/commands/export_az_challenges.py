"""Export every A-Z Platinum Challenge's progress to a durable JSON file.

Run this BEFORE the Lane 2 challenge teardown drops the challenge tables. The export is keyed on stable PSN
ids (profile psn_username, game np_communication_id) rather than DB primary keys, so a future rebuilt
Challenge system can re-import it cleanly after the old rows are gone. A-Z progress lives in
Challenge(challenge_type='az') + its AZChallengeSlot rows (game per letter + completion), NOT in the
milestone tables, so this captures the whole picture.
"""
import json

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Export A-Z challenge progress (per-letter game + completion) to JSON before the teardown."

    def add_arguments(self, parser):
        parser.add_argument('--output', default='az_challenge_export.json',
                            help="File to write the JSON export to (default: az_challenge_export.json).")
        parser.add_argument('--include-deleted', action='store_true',
                            help="Also export soft-deleted challenges (default: active only).")

    def handle(self, *args, **options):
        from trophies.models import Challenge

        qs = Challenge.objects.filter(challenge_type='az').select_related('profile').prefetch_related('az_slots__game')
        if not options['include_deleted']:
            qs = qs.filter(is_deleted=False)

        challenges = []
        slot_total = 0
        for ch in qs.iterator(chunk_size=200):
            slots = []
            for slot in sorted(ch.az_slots.all(), key=lambda s: s.letter):
                game = slot.game
                slots.append({
                    'letter': slot.letter,
                    'game_np_communication_id': game.np_communication_id if game else None,
                    'game_title': game.title_name if game else None,
                    'is_completed': slot.is_completed,
                    'completed_at': slot.completed_at.isoformat() if slot.completed_at else None,
                    'assigned_at': slot.assigned_at.isoformat() if slot.assigned_at else None,
                })
            slot_total += len(slots)
            challenges.append({
                'profile_id': ch.profile_id,
                'psn_username': ch.profile.psn_username if ch.profile_id else None,
                'name': ch.name,
                'cover_letter': ch.cover_letter,
                'completed_count': ch.completed_count,
                'is_complete': ch.is_complete,
                'completed_at': ch.completed_at.isoformat() if ch.completed_at else None,
                'is_deleted': ch.is_deleted,
                'created_at': ch.created_at.isoformat() if ch.created_at else None,
                'slots': slots,
            })

        payload = {
            'exported_at': timezone.now().isoformat(),
            'schema': 'az_challenge_export.v1',
            'challenge_count': len(challenges),
            'slot_count': slot_total,
            'challenges': challenges,
        }
        with open(options['output'], 'w', encoding='utf-8') as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)

        self.stdout.write(self.style.SUCCESS(
            f"Exported {len(challenges)} A-Z challenge(s) / {slot_total} letter slot(s) to {options['output']}."
        ))
