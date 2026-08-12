import csv

from django.contrib import admin
from django.http import HttpResponse
from django.utils import timezone

from .models import EmailLog, SiteEvent


def _export_siteevents_csv(modeladmin, request, queryset):
    """Export selected SiteEvent records as CSV."""
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="siteevents_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'
    writer = csv.writer(response)
    writer.writerow(['id', 'event_type', 'object_id', 'occurred_at', 'user_id'])
    for row in queryset.values_list('id', 'event_type', 'object_id', 'occurred_at', 'user_id'):
        writer.writerow(row)
    return response


_export_siteevents_csv.short_description = "Export selected as CSV"


@admin.register(SiteEvent)
class SiteEventAdmin(admin.ModelAdmin):
    list_display = ('event_type', 'object_id', 'occurred_at', 'user_id')
    list_filter = ('event_type', 'occurred_at')
    search_fields = ('object_id', 'user_id')
    date_hierarchy = 'occurred_at'
    ordering = ('-occurred_at',)
    list_per_page = 100
    actions = [_export_siteevents_csv]
    readonly_fields = ('event_type', 'object_id', 'occurred_at', 'user_id')

    def has_add_permission(self, request):
        return False


@admin.register(EmailLog)
class EmailLogAdmin(admin.ModelAdmin):
    list_display = ('recipient_email', 'email_type', 'subject', 'status', 'triggered_by', 'created_at')
    list_filter = ('email_type', 'status', 'triggered_by')
    search_fields = ('recipient_email', 'user__email', 'subject')
    raw_id_fields = ('user',)
    readonly_fields = ('user', 'recipient_email', 'email_type', 'subject', 'status', 'triggered_by', 'created_at', 'metadata')
    ordering = ('-created_at',)
    date_hierarchy = 'created_at'
    list_per_page = 100

    def has_add_permission(self, request):
        return False
