"""
REST API for the Community Trophy Tracker.

Three endpoints, all read-only and public (aggregate community data, no PII):

  GET /api/community-stats/<YYYY-MM-DD>/
      Historical daily summary. 404 if no row exists.

  GET /api/community-stats/today/
      Live, in-progress totals for today (ET). 60s Redis-cached so abuse
      via flooding cannot pile DB load. Includes a freshness note since
      Discord-only profiles can lag up to ~12h.

  GET /api/community-stats/records/
      Current all-time maxima for each tracked stat, paired with the date
      each record was set.
"""
import logging
from datetime import datetime

from django.core.cache import cache
from django.utils import timezone
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status as http_status

from core.models import CommunityTrophyDay
from core.services.community_trophy_tracker import ET, build_today_payload

logger = logging.getLogger(__name__)

TODAY_CACHE_TTL_SECONDS = 60


def _serialize_day(day: CommunityTrophyDay) -> dict:
    return {
        'date': day.date.isoformat(),
        'total_trophies': day.total_trophies,
        'total_platinums': day.total_platinums,
        'total_ultra_rares': day.total_ultra_rares,
        'pp_score': day.pp_score,
        'posted_at': day.posted_at.isoformat() if day.posted_at else None,
    }


class CommunityStatsDayView(APIView):
    """GET /api/community-stats/<YYYY-MM-DD>/"""
    permission_classes = [AllowAny]

    def get(self, request, date_str):
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return Response(
                {'error': 'Invalid date format. Expected YYYY-MM-DD.'},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        try:
            day = CommunityTrophyDay.objects.get(date=target_date)
        except CommunityTrophyDay.DoesNotExist:
            return Response(
                {'error': f'No community stats recorded for {date_str}.'},
                status=http_status.HTTP_404_NOT_FOUND,
            )

        return Response(_serialize_day(day))


class CommunityStatsTodayView(APIView):
    """GET /api/community-stats/today/

    Live partial-day totals. 60s cache keyed on ET date so the response
    naturally refreshes after midnight ET without manual invalidation.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        today_et = timezone.now().astimezone(ET).date()
        cache_key = f"community_trophy_today:{today_et.isoformat()}"

        payload = cache.get(cache_key)
        if payload is None:
            try:
                payload = build_today_payload(today_et)
            except Exception:
                logger.exception("build_today_payload failed for %s", today_et)
                return Response(
                    {'error': 'Failed to compute today stats.'},
                    status=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                )
            cache.set(cache_key, payload, TODAY_CACHE_TTL_SECONDS)

        return Response(payload)


class CommunityStatsRecordsView(APIView):
    """GET /api/community-stats/records/

    For each tracked stat, returns the highest historical value and the
    date that record was set. Values are null when no rows exist yet.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        rows = list(CommunityTrophyDay.objects.all())
        if not rows:
            return Response({
                'max_trophies': None,
                'max_platinums': None,
                'max_ultra_rares': None,
                'max_pp_score': None,
            })

        def best(attr):
            winner = max(rows, key=lambda r: getattr(r, attr))
            return {'value': getattr(winner, attr), 'date': winner.date.isoformat()}

        return Response({
            'max_trophies': best('total_trophies'),
            'max_platinums': best('total_platinums'),
            'max_ultra_rares': best('total_ultra_rares'),
            'max_pp_score': best('pp_score'),
        })
