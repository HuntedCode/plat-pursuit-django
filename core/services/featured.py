from django.db.models import Count, Avg, Q
from django.utils import timezone
from datetime import timedelta
from trophies.models import FeaturedGame, ProfileGame, EarnedTrophy, Game

def get_featured_games(limit=6):
    now = timezone.now()

    # Manual overrides
    manual_qs = FeaturedGame.objects.filter(
        Q(start_date__lte=now) | Q(start_date__isnull=True),
        Q(end_date__gte=now) | Q(end_date__isnull=True)
    ).select_related('game')[:limit]
    featured = [fg.game for fg in manual_qs]
    
    if len(featured) < limit:
        algo_games = compute_top_games()
        featured += [g for g in algo_games][:limit - len(featured)]

    # Enrich with stats
    enriched = []
    game_ids = [g.id for g in featured]
    pg_stats = (
        ProfileGame.objects.filter(game__id__in=game_ids)
        .values('game_id')
        .annotate(avg_completion=Avg('progress'))
    )
    pg_dict = {item['game_id']: item['avg_completion'] or 0 for item in pg_stats}
    et_counts = EarnedTrophy.objects.filter(trophy__game__id__in=game_ids, earned=True).values('trophy__game__id').annotate(total_earned=Count('id'))
    et_dict = {item['trophy__game__id']: item['total_earned'] for item in et_counts}

    for game in featured:
        enriched.append({
            'name': game.title_name,
            'trophies': game.defined_trophies,
            'trophiesEarned': et_dict.get(game.id, 0),
            'platform': ', '.join(game.title_platform),
            'avgCompletion': pg_dict.get(game.id, 0),
            'image': game.get_icon_url(),
            'slug': f"/games/{game.np_communication_id}/",
        })
    return enriched
    
def compute_top_games(limit=6):
    now = timezone.now()
    past_date = now - timedelta(days=7)

    player_counts = ProfileGame.objects.filter(
        last_updated_datetime__gte=past_date,
    ).exclude(
        game__shovelware_status__in=['auto_flagged', 'manually_flagged'],
    ).values('game__id').annotate(players=Count('profile', distinct=True)).order_by('-players')

    trophy_counts = EarnedTrophy.objects.filter(
        earned_date_time__gte=past_date,
    ).exclude(
        trophy__game__shovelware_status__in=['auto_flagged', 'manually_flagged'],
    ).values('trophy__game__id').annotate(trophies=Count('id')).order_by('-trophies')

    games = {}
    max_players = max(p['players'] for p in player_counts[:limit * 2]) if player_counts else 1
    max_trophies = max(t['trophies'] for t in trophy_counts[:limit * 2]) if trophy_counts else 1
    for p in player_counts:
        games[p['game__id']] = {'players': p['players']}
    for t in trophy_counts:
        gid = t['trophy__game__id']
        if gid in games:
            games[gid]['trophies'] = t['trophies']
        else:
            games[gid] = {'players': 0, 'trophies': t['trophies']}
    

    for gid, stats in games.items():
        norm_players = stats.get('players', 0) / max_players
        norm_trophies = stats.get('trophies', 0) / max_trophies
        stats['score'] = 0.6 * norm_players + 0.4 * norm_trophies
    
    top_ids = [gid for gid, stats in sorted(games.items(), key=lambda x: x[1]['score'], reverse=True)][:limit * 2]
    top_games = Game.objects.filter(
        id__in=top_ids,
    ).order_by('-id')[:limit]

    return list(top_games)