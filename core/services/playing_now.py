from django.db.models import Count, Avg, Q, Exists, OuterRef
from django.utils import timezone
from datetime import timedelta
from trophies.models import Game, ProfileGame

def get_playing_now(limit=10):
    week_ago = timezone.now() - timedelta(days=7)
    recent_plays_subq = ProfileGame.objects.filter(game=OuterRef('pk'), last_updated_datetime__gte=week_ago)
    top_games = Game.objects.filter(Exists(recent_plays_subq)).annotate(
        recent_plays=Count('played_by', filter=Q(played_by__last_updated_datetime__gte=week_ago)),
        avg_completion=Avg('played_by__progress')
    ).order_by('-recent_plays', '-played_count')[:limit]

    enriched = []
    for game in top_games:
        enriched.append({
            'image': game.title_icon_url,
            'title': game.title_name,
            'players': game.played_count,
            'avgCompletion': game.avg_completion or 0.0,
            'slug': f"/games/{game.np_communication_id}/",
        })
    return enriched