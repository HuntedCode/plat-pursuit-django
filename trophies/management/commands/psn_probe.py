"""
Probe PSN API endpoints directly and dump raw payloads.

Useful for troubleshooting sync discrepancies (e.g. trophy level out-of-sync,
unexpected privacy gating, missing trophy groups) by hitting PSN endpoints
ad hoc and seeing exactly what PSN returned at that moment.

Examples:
    python manage.py psn_probe profile_legacy --user abu_abu
    python manage.py psn_probe trophy_summary --user abu_abu
    python manage.py psn_probe trophy_titles --user abu_abu --first 10
    python manage.py psn_probe trophies --user abu_abu --np-comm-id NPWR48976_00 --platform PS4
    python manage.py psn_probe game_details --np-title-id PPSA28997_00 --platform PS5

NPSSO_TOKEN must be set in .env (64-char cookie pulled from playstation.com DevTools).
"""

import json
import os
from dataclasses import asdict, is_dataclass
from typing import Any

import requests
from django.core.management.base import BaseCommand, CommandError
from dotenv import load_dotenv
from psnawp_api import PSNAWP
from psnawp_api.models.trophies.trophy_constants import PlatformType

load_dotenv()


SIMPLE_ENDPOINTS = {
    'profile': lambda user: user.profile(),
    'profile_legacy': lambda user: user.get_profile_legacy(),
    'presence': lambda user: user.get_presence(),
    'region': lambda user: user.get_region(),
    'friendship': lambda user: user.friendship(),
    'trophy_summary': lambda user: user.trophy_summary(),
}

ITERATOR_ENDPOINTS = {
    'trophy_titles',
    'trophy_titles_for_title',
    'title_stats',
    'trophies',
}

OBJECT_ENDPOINTS = {
    'trophy_groups_summary',
    'game_details',
}


def _serialize(obj: Any) -> Any:
    """Best-effort JSON-friendly conversion for psnawp objects."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [_serialize(v) for v in obj]
    if is_dataclass(obj):
        return _serialize(asdict(obj))
    if hasattr(obj, '__dict__'):
        return _serialize({k: v for k, v in vars(obj).items() if not k.startswith('_')})
    return str(obj)


class Command(BaseCommand):
    help = "Probe PSN API endpoints directly and dump raw payloads (troubleshooting tool)."

    ENDPOINTS = sorted(set(SIMPLE_ENDPOINTS) | ITERATOR_ENDPOINTS | OBJECT_ENDPOINTS)

    def add_arguments(self, parser):
        parser.add_argument(
            'endpoint', choices=self.ENDPOINTS,
            help='PSN endpoint to call.',
        )
        parser.add_argument(
            '--user', default='abu_abu',
            help='PSN online ID (default: abu_abu).',
        )
        parser.add_argument(
            '--np-comm-id', dest='np_comm_id',
            help='Game NP communication ID (e.g. NPWR48976_00). '
                 'Required for: trophies, trophy_groups_summary.',
        )
        parser.add_argument(
            '--np-title-id', dest='np_title_id',
            help='Game NP title ID (e.g. PPSA28997_00). Required for: game_details.',
        )
        parser.add_argument(
            '--platform', default='PS5',
            choices=['PS3', 'PS4', 'PS5', 'PSVITA', 'PSPC'],
            help='Platform for trophies / trophy_groups_summary / game_details (default: PS5).',
        )
        parser.add_argument(
            '--trophy-group-id', dest='trophy_group_id', default='all',
            help='Trophy group for the trophies endpoint: "default" (base game), '
                 '"all" (everything), "001" etc for a specific DLC (default: all).',
        )
        parser.add_argument(
            '--include-progress', action='store_true',
            help='Include user progress on trophies (doubles API call cost).',
        )
        parser.add_argument(
            '--title-ids', dest='title_ids',
            help='Comma-separated NP communication IDs for trophy_titles_for_title.',
        )
        parser.add_argument(
            '--limit', type=int, default=None,
            help='Limit on paginated endpoints (default: no limit).',
        )
        parser.add_argument(
            '--offset', type=int, default=0,
            help='Offset on paginated endpoints (default: 0).',
        )
        parser.add_argument(
            '--page-size', dest='page_size', type=int, default=200,
            help='Page size on paginated endpoints (default: 200).',
        )
        parser.add_argument(
            '--first', type=int, default=5,
            help='Print only the first N items from iterator endpoints. '
                 '0 = print all (default: 5).',
        )

    def handle(self, *args, **options):
        token = os.getenv('NPSSO_TOKEN')
        if not token or len(token) != 64:
            raise CommandError(
                'Set NPSSO_TOKEN in .env (64-char NPSSO cookie from playstation.com).'
            )

        endpoint = options['endpoint']
        username = options['user'].lower()

        try:
            psnawp = PSNAWP(token)
            user = psnawp.user(online_id=username)

            self.stdout.write(self.style.SUCCESS(
                f'User: {user.online_id} (account_id={user.account_id})'
            ))
            self.stdout.write(self.style.SUCCESS(f'Endpoint: {endpoint}'))
            self.stdout.write('')

            if endpoint in SIMPLE_ENDPOINTS:
                self._dump(SIMPLE_ENDPOINTS[endpoint](user))
                return

            platform = PlatformType(options['platform'])

            if endpoint == 'trophy_titles':
                self._dump_iterator(
                    user.trophy_titles(
                        limit=options['limit'],
                        offset=options['offset'],
                        page_size=options['page_size'],
                    ),
                    options['first'],
                )
            elif endpoint == 'trophy_titles_for_title':
                title_ids = self._require_title_ids(options['title_ids'])
                self._dump_iterator(
                    user.trophy_titles_for_title(title_ids=title_ids),
                    options['first'],
                )
            elif endpoint == 'title_stats':
                self._dump_iterator(
                    user.title_stats(
                        limit=options['limit'],
                        offset=options['offset'],
                        page_size=options['page_size'],
                    ),
                    options['first'],
                )
            elif endpoint == 'trophies':
                np_comm_id = self._require(options['np_comm_id'], '--np-comm-id', endpoint)
                self._dump_iterator(
                    user.trophies(
                        np_communication_id=np_comm_id,
                        platform=platform,
                        include_progress=options['include_progress'],
                        trophy_group_id=options['trophy_group_id'],
                        limit=options['limit'],
                        offset=options['offset'],
                        page_size=options['page_size'],
                    ),
                    options['first'],
                )
            elif endpoint == 'trophy_groups_summary':
                np_comm_id = self._require(options['np_comm_id'], '--np-comm-id', endpoint)
                self._dump(user.trophy_groups_summary(np_comm_id, platform))
            elif endpoint == 'game_details':
                np_title_id = self._require(options['np_title_id'], '--np-title-id', endpoint)
                game_title = psnawp.game_title(
                    np_title_id, platform, account_id=user.account_id
                )
                self._dump(game_title.get_details())

        except ValueError as e:
            raise CommandError(
                f'Auth/Token Error: {e} - regenerate NPSSO via playstation.com DevTools.'
            )
        except requests.exceptions.HTTPError as e:
            status = getattr(e.response, 'status_code', None)
            if status == 429:
                raise CommandError('Rate limit hit (429). Wait ~15 min before retrying.')
            raise CommandError(f'HTTP {status}: {e}')

    @staticmethod
    def _require(value, flag, endpoint):
        if not value:
            raise CommandError(f'{flag} is required for the {endpoint} endpoint.')
        return value

    @staticmethod
    def _require_title_ids(raw):
        if not raw:
            raise CommandError('--title-ids is required for trophy_titles_for_title.')
        ids = [t.strip() for t in raw.split(',') if t.strip()]
        if not ids:
            raise CommandError('--title-ids must contain at least one ID.')
        return ids

    def _dump(self, payload):
        self.stdout.write(json.dumps(_serialize(payload), indent=2, default=str))

    def _dump_iterator(self, iterator, first):
        items = []
        for i, item in enumerate(iterator):
            if first and i >= first:
                items.append({
                    '__truncated__':
                    f'showing first {first} items; pass --first 0 to show all.'
                })
                break
            items.append(_serialize(item))
        self.stdout.write(json.dumps(items, indent=2, default=str))
