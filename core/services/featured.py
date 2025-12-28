from django.db.models import Count, Avg, Q, Exists, OuterRef
from django.utils import timezone
from datetime import timedelta
from trophies.models import FeaturedGame, Game, ProfileGame, EarnedTrophy

def get_featured_games(limit=6):
    now = timezone.now()
    week_ago = now - timedelta(days=7)

    # Manual overrides
    manual_qs = FeaturedGame.objects.filter(
        Q(start_date__lte=now) | Q(start_date__isnull=True),
        Q(end_date__gte=now) | Q(end_date__isnull=True)
    ).select_related('game')[:limit]
    featured = [fg.game for fg in manual_qs]
    
    # Enrich with stats
    enriched = []
    game_ids = [g.id for g in featured]
    pg_stats = ProfileGame.objects.filter(game__id__in=game_ids).aggregate(avg_completion=Avg('progress')) or {'avg_completion': 0}
    et_counts = EarnedTrophy.objects.filter(trophy__game__id__in=game_ids, earned=True).values('trophy__game__id').annotate(total_earned=Count('id'))
    et_dict = {item['trophy__game__id']: item['total_earned'] for item in et_counts}

    for game in featured:
        enriched.append({
            'name': game.title_name,
            'trophies': game.defined_trophies,
            'trophiesEarned': et_dict.get(game.id, 0),
            'platform': ', '.join(game.title_platform),
            'avgCompletion': pg_stats['avg_completion'],
            'image': game.get_icon_url(),
            'slug': f"/games/{game.np_communication_id}/",
        })
    return enriched
    