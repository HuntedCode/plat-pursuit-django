import csv

from django.contrib import admin
from django.http import HttpResponse
from django.utils import timezone

from .models import AnalyticsSession, EmailLog, PageView, SiteEvent


def _export_pageviews_csv(modeladmin, request, queryset):
    """Export selected PageView records as CSV."""
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="pageviews_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'
    writer = csv.writer(response)
    writer.writerow(['id', 'page_type', 'object_id', 'viewed_at', 'session_id', 'user_id', 'ip_address'])
    for row in queryset.values_list('id', 'page_type', 'object_id', 'viewed_at', 'session_id', 'user_id', 'ip_address'):
        writer.writerow(row)
    return response


_export_pageviews_csv.short_description = "Export selected as CSV"


def _export_sessions_csv(modeladmin, request, queryset):
    """Export selected AnalyticsSession records as CSV."""
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="analytics_sessions_{timezone.now().strftime("%Y%m%d_%H%M%S")}.csv"'
    writer = csv.writer(response)
    writer.writerow(['session_id', 'user_id', 'ip_address', 'created_at', 'last_activity', 'page_count', 'referrer', 'user_agent'])
    for row in queryset.values_list('session_id', 'user_id', 'ip_address', 'created_at', 'last_activity', 'page_count', 'referrer', 'user_agent'):
        writer.writerow(row)
    return response


_export_sessions_csv.short_description = "Export selected as CSV"


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


@admin.register(AnalyticsSession)
class AnalyticsSessionAdmin(admin.ModelAdmin):
    list_display = ('session_id', 'user_id', 'created_at', 'last_activity', 'page_count', 'ip_address')
    list_filter = ('created_at', 'last_activity')
    search_fields = ('session_id', 'user_id', 'ip_address')
    date_hierarchy = 'created_at'
    ordering = ('-created_at',)
    list_per_page = 100
    actions = [_export_sessions_csv]
    readonly_fields = ('session_id', 'user_id', 'ip_address', 'created_at',
                       'last_activity', 'ended_at', 'page_count', 'page_sequence',
                       'referrer', 'user_agent')

    def has_add_permission(self, request):
        return False


@admin.register(PageView)
class PageViewAdmin(admin.ModelAdmin):
    list_display = ('page_type', 'object_id', 'viewed_at', 'session_id', 'user_id', 'ip_address')
    list_filter = ('page_type', 'viewed_at')
    search_fields = ('object_id', 'user_id', 'ip_address', 'session_id')
    date_hierarchy = 'viewed_at'
    ordering = ('-viewed_at',)
    list_per_page = 100
    actions = [_export_pageviews_csv]
    readonly_fields = ('page_type', 'object_id', 'viewed_at', 'session_id', 'user_id', 'ip_address')

    def has_add_permission(self, request):
        return False


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
