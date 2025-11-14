import json
import logging
from django.shortcuts import render
from django.http import StreamingHttpResponse, JsonResponse
from django.views.generic import ListView
from django.db.models import Q, Prefetch, OuterRef, Subquery, Value, IntegerField
from django.db.models.functions import Coalesce
from .models import Game, Trophy
from .forms import GameSearchForm
from .utils import redis_client, TITLE_STATS_SUPPORTED_PLATFORMS

logger = logging.getLogger('psn_api')

# Create your views here.
def monitoring_dashboard(request):
    return render(request, 'monitoring.html')

def token_stats_sse(request):
    def event_stream():
        pubsub = redis_client.pubsub()
        pubsub.subscribe("token_keeper_stats")
        try:
            for message in pubsub.listen():
                if message['type'] == 'message':
                    try:
                        stats = json.loads(message['data'])
                        redis_client.set("token_keeper_latest_stats", json.dumps(stats), ex=60)
                        yield f"data: {json.dumps(stats)}\n\n"
                    except json.JSONDecodeError as e:
                        logger.error(f"Error decoding SSE stats: {e}")
                        yield f"data: {{'error': 'Invalid stats data'}}\n\n"
        except Exception as e:
            logger.error(f"Error in SSE stream: {e}")
            yield f"data: {{'error': '{str(e)}'}}\n\n"
        finally:
            pubsub.unsubscribe()
    
    response = StreamingHttpResponse(event_stream(), content_type='text/event-stream')
    response['Cache-Control'] = 'no-cache'
    return response

def token_stats(request):
    try:
        stats_json = redis_client.get("token_keeper_latest_stats")
        stats = json.loads(stats_json) if stats_json else {}
        return JsonResponse(stats)
    except Exception as e:
        logger.error(f"Error fetching token stats: {e}")
        return JsonResponse({'error': str(e)}, status=500)
    
class GamesListView(ListView):
    model = Game
    template_name = 'trophies/game_list.html'
    paginate_by = 50

    def get_queryset(self):
        qs = super().get_queryset()
        form = GameSearchForm(self.request.GET)
        if form.is_valid():
            query = form.cleaned_data.get('query')
            platform = form.cleaned_data.get('platform')
            letter = form.cleaned_data.get('letter')
            show_legacy = form.cleaned_data.get('show_legacy')
            show_only_platinum = form.cleaned_data.get('show_only_platinum')
            sort_val = form.cleaned_data.get('sort')

            if query:
                qs = qs.filter(Q(title_name__icontains=query))
            if platform:
                qs = qs.filter(title_platform__contains=[platform])
            if letter:
                if letter == '0-9':
                    qs = qs.filter(title_name__regex=r'^[0-9]')
                else:
                    qs = qs.filter(title_name__istartswith=letter)
            
            if not show_legacy:
                supported_filter = Q()
                for plat in TITLE_STATS_SUPPORTED_PLATFORMS:
                    supported_filter |= Q(title_platform__contains=plat)
                qs = qs.filter(supported_filter)
            
            if show_only_platinum:
                qs = qs.filter(trophies__trophy_type='platinum').distinct()

            # Sorting
            platinums_earned = Subquery(Trophy.objects.filter(game=OuterRef('pk'), trophy_type='platinum').values('earned_count')[:1])
            qs = qs.annotate(platinums_earned_count=Coalesce(platinums_earned, Value(0), output_field=IntegerField()))

            if sort_val == 'played':
                order = ['-played_count', 'title_name']
            elif sort_val == 'played_inv':
                order = ['played_count', 'title_name']
            elif sort_val == 'plat_earned':
                order = ['-platinums_earned_count', 'title_name']
            elif sort_val == 'plat_earned_inv':
                order = ['platinums_earned_count', 'title_name']
            else:
                order = ['title_name']

            qs = qs.prefetch_related(
                Prefetch('trophies', queryset=Trophy.objects.filter(trophy_type='platinum'), to_attr='platinum_trophy')
            )
        return qs.order_by(*order)
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = GameSearchForm(self.request.GET)
        return context
    
    def get_template_names(self):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return ['trophies/partials/game_cards.html']
        return super().get_template_names()