import json
import logging
from django.shortcuts import render
from django.http import StreamingHttpResponse, JsonResponse
from django.views.generic import ListView
from django.db.models import Q, Prefetch
from .models import Game, Trophy
from .forms import GameSearchForm
from .utils import redis_client

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
            if query:
                qs = qs.filter(Q(title_name__icontains=query))
            if platform:
                qs = qs.filter(title_platform__contains=[platform])
            
            qs = qs.prefetch_related(
                Prefetch('trophies', queryset=Trophy.objects.filter(trophy_type='platinum'), to_attr='platinum_trophy')
            )
        return qs.order_by('title_name')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['form'] = GameSearchForm(self.request.GET)
        return context
    
    def get_template_names(self):
        if self.request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return ['trophies/partials/game_cards.html']
        return super().get_template_names()