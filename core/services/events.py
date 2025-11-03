from django.utils import timezone
from trophies.models import Event

def get_upcoming_events():
    qs = Event.objects.filter(date__gte=timezone.now().date()).order_by('date')
    return [
        {
            'title': event.title,
            'date': event.date.isoformat(),
            'end_date': event.end_date.isoformat() if event.end_date else None,
            'color': event.color,
            'description': event.description,
            'time': event.time,
            'slug': event.slug,
        } for event in qs
    ]