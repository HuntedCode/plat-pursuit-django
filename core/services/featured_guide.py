
from random import choice
from django.db.models import Q
from django.utils import timezone
from trophies.models import FeaturedGuide, Concept

def get_featured_guide():
    featured_qs = FeaturedGuide.objects.filter(
        Q(start_date__lte=timezone.now()) & (Q(end_date__gte=timezone.now()) | Q(end_date__isnull=True))
    ).order_by('-priority').first()
    if featured_qs:
        featured_concept = featured_qs.concept
    else:
        guides = Concept.objects.exclude(Q(guide_slug__isnull=True) | Q(guide_slug=''))
        if guides.exists():
            featured_concept = choice(guides)
        else:
            featured_concept = None
    return featured_concept.id