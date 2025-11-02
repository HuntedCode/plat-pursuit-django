from django.db.models import Count, Avg, Q
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

    # Supplement if needed
    remaining = limit - len(featured)
    if remaining > 0:
        auto_qs = Game.objects.annotate(
            recent_earns=Count('trophies__earned_trophy_entries', filter=Q(trophies__earned_trophy_entries__earned_date_time__gte=week_ago)),
            play_count=Count('played_by')
        ).filter(recent_earns__gt=0).order_by('-recent_earns', '-play_count')[:remaining]
        featured.extend(auto_qs)
    
    # Enrich with stats
    enriched = []
    for game in featured:
        pg_qs = game.played_by.all()
        et_qs = EarnedTrophy.objects.filter(trophy__game=game, earned=True)
        enriched.append({
            'name': game.title_name,
            'trophies': game.defined_trophies,
            'trophiesEarned': et_qs.count(),
            'platform': ', '.join(game.title_platform),
            'avgCompletion': pg_qs.aggregate(avg=Avg('progress'))['avg'] or 0,
            'image': game.title_image if game.title_image else game.title_icon_url,
            'slug': f"/games/{game.np_communication_id}/",
        })
    return enriched
    