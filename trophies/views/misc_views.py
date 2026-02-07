from django.http import HttpResponseRedirect
from django.urls import reverse_lazy
from django.views.generic import View


class SearchView(View):
    """
    AJAX endpoint for universal search across games, trophies, and profiles.

    Returns JSON results for autocomplete functionality in the site-wide search bar.
    Searches across game titles, trophy names, and PSN usernames based on type parameter.
    """
    def get(self, request, *args, **kwargs):
        search_type = request.GET.get('type')
        query = request.GET.get('query', '')

        if search_type == 'game':
            return HttpResponseRedirect(reverse_lazy('games_list') + f"?query={query}")
        elif search_type == 'trophy':
            return HttpResponseRedirect(reverse_lazy('trophies_list') + f"?query={query}")
        elif search_type == 'user':
            return HttpResponseRedirect(reverse_lazy('profiles_list') + f"?query={query}")
        else:
            return HttpResponseRedirect(reverse_lazy('home'))
