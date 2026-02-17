from django.contrib import admin
from .models import NotificationTemplate, Notification, ScheduledNotification, NotificationLog


@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'notification_type', 'auto_trigger_enabled', 'priority', 'updated_at']
    list_filter = ['notification_type', 'auto_trigger_enabled', 'priority']
    search_fields = ['name', 'title_template', 'message_template']
    readonly_fields = ['created_at', 'updated_at']

    fieldsets = [
        ('Basic Info', {
            'fields': ['name', 'notification_type', 'icon', 'priority']
        }),
        ('Template Content', {
            'fields': ['title_template', 'message_template'],
            'description': 'Use {variable_name} for substitution (e.g., {username}, {trophy_name}, {game_name})'
        }),
        ('Action Configuration', {
            'fields': ['action_url_template', 'action_text'],
            'description': 'Optional action button/link'
        }),
        ('Automation Settings', {
            'fields': ['auto_trigger_enabled', 'trigger_event'],
            'description': 'Configure automatic notification creation'
        }),
        ('Timestamps', {
            'fields': ['created_at', 'updated_at'],
            'classes': ['collapse']
        }),
    ]


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['recipient', 'notification_type', 'title_short', 'priority', 'is_read', 'created_at']
    list_select_related = ('recipient',)
    list_filter = ['notification_type', 'is_read', 'priority', 'created_at']
    search_fields = ['recipient__email', 'recipient__username', 'title', 'message']
    date_hierarchy = 'created_at'
    readonly_fields = ['created_at', 'read_at', 'template']
    raw_id_fields = ('recipient', 'template')

    fieldsets = [
        ('Recipient', {
            'fields': ['recipient']
        }),
        ('Content', {
            'fields': ['notification_type', 'template', 'title', 'message', 'icon']
        }),
        ('Action', {
            'fields': ['action_url', 'action_text'],
            'classes': ['collapse']
        }),
        ('Metadata', {
            'fields': ['priority', 'metadata'],
            'classes': ['collapse']
        }),
        ('Status', {
            'fields': ['is_read', 'read_at', 'created_at']
        }),
    ]

    def title_short(self, obj):
        """Display truncated title."""
        return obj.title[:50] + '...' if len(obj.title) > 50 else obj.title
    title_short.short_description = 'Title'

    def has_add_permission(self, request):
        """Disable manual creation via admin - use the API instead."""
        return False


@admin.register(ScheduledNotification)
class ScheduledNotificationAdmin(admin.ModelAdmin):
    list_display = ['title_short', 'target_type', 'status', 'scheduled_at', 'recipient_count', 'created_by']
    list_select_related = ('created_by',)
    list_filter = ['status', 'target_type', 'notification_type', 'priority', 'scheduled_at']
    search_fields = ['title', 'message', 'created_by__email']
    readonly_fields = ['created_at', 'sent_at', 'recipient_count', 'error_message']
    date_hierarchy = 'scheduled_at'

    fieldsets = [
        ('Content', {
            'fields': ['notification_type', 'title', 'message', 'icon', 'priority']
        }),
        ('Action', {
            'fields': ['action_url', 'action_text'],
            'classes': ['collapse']
        }),
        ('Targeting', {
            'fields': ['target_type', 'target_criteria']
        }),
        ('Scheduling', {
            'fields': ['scheduled_at', 'status']
        }),
        ('Tracking', {
            'fields': ['created_by', 'created_at', 'sent_at', 'recipient_count', 'error_message'],
            'classes': ['collapse']
        }),
    ]

    actions = ['cancel_selected']

    def title_short(self, obj):
        """Display truncated title."""
        return obj.title[:50] + '...' if len(obj.title) > 50 else obj.title
    title_short.short_description = 'Title'

    @admin.action(description='Cancel selected pending notifications')
    def cancel_selected(self, request, queryset):
        updated = queryset.filter(status='pending').update(status='cancelled')
        self.message_user(request, f'{updated} notification(s) cancelled.')


@admin.register(NotificationLog)
class NotificationLogAdmin(admin.ModelAdmin):
    list_display = ['title_short', 'target_type', 'recipient_count', 'sent_at', 'sent_by', 'was_scheduled']
    list_select_related = ('sent_by',)
    list_filter = ['was_scheduled', 'notification_type', 'target_type', 'sent_at']
    search_fields = ['title', 'message', 'sent_by__email']
    readonly_fields = ['sent_at', 'scheduled_notification', 'notification_type', 'title', 'message',
                       'target_type', 'target_criteria', 'recipient_count', 'sent_by', 'was_scheduled']
    date_hierarchy = 'sent_at'

    fieldsets = [
        ('Content', {
            'fields': ['notification_type', 'title', 'message']
        }),
        ('Targeting', {
            'fields': ['target_type', 'target_criteria']
        }),
        ('Results', {
            'fields': ['recipient_count', 'sent_at', 'sent_by', 'was_scheduled', 'scheduled_notification']
        }),
    ]

    def title_short(self, obj):
        """Display truncated title."""
        return obj.title[:50] + '...' if len(obj.title) > 50 else obj.title
    title_short.short_description = 'Title'

    def has_add_permission(self, request):
        """Logs are created automatically, not manually."""
        return False

    def has_change_permission(self, request, obj=None):
        """Logs are read-only."""
        return False

    def has_delete_permission(self, request, obj=None):
        """Allow deletion for cleanup purposes."""
        return True
