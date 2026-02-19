from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser, SubscriptionPeriod
from .forms import CustomUserCreationForm

@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    add_form = CustomUserCreationForm

    # Fields for list view (efficient, searchable)
    list_display = ('email', 'is_linked_to_profile', 'premium_tier', 'subscription_provider', 'email_prefs_summary', 'user_timezone', 'default_region', 'is_staff', 'is_active', 'date_joined')
    list_select_related = ('profile',)
    list_filter = ('is_staff', 'is_active', 'user_timezone')
    search_fields = ('email',)
    ordering = ('email',)

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal Info', {'fields': ('user_timezone', 'default_region', 'premium_tier')}),
        ('Subscription', {'fields': ('subscription_provider', 'stripe_customer_id', 'paypal_subscription_id', 'paypal_cancel_at')}),
        ('Email Preferences', {'fields': ('email_preferences',)}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Important Dates', {'fields': ('last_login', 'date_joined')}),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'user_timezone', 'password1', 'password2'),
        }),
    )

    class Meta:
        model = CustomUser

    def is_linked_to_profile(self, obj):
        return hasattr(obj, "profile") and obj.profile.is_linked

    is_linked_to_profile.boolean = True
    is_linked_to_profile.short_description = "PSN Linked"

    def email_prefs_summary(self, obj):
        """Display email preferences summary in list view."""
        from users.services.email_preference_service import EmailPreferenceService
        prefs = EmailPreferenceService.get_user_preferences(obj)

        # Check if globally unsubscribed
        if prefs.get('global_unsubscribe', False):
            return "❌ Unsubscribed (All)"

        # Count enabled preferences
        enabled = sum([
            prefs.get('monthly_recap', True),
            prefs.get('badge_notifications', True),
            prefs.get('milestone_notifications', True),
            prefs.get('admin_announcements', True),
        ])

        if enabled == 4:
            return "✅ All Enabled"
        elif enabled == 0:
            return "⊘ None Enabled"
        else:
            return f"◐ {enabled}/4 Enabled"

    email_prefs_summary.short_description = "Email Preferences"


@admin.register(SubscriptionPeriod)
class SubscriptionPeriodAdmin(admin.ModelAdmin):
    list_display = ('user', 'started_at', 'ended_at', 'provider', 'duration_days_display', 'notes')
    list_filter = ('provider', 'ended_at')
    search_fields = ('user__email', 'notes')
    raw_id_fields = ('user',)
    readonly_fields = ('duration_days_display',)
    ordering = ('-started_at',)

    def duration_days_display(self, obj):
        return f"{obj.duration_days} days"
    duration_days_display.short_description = "Duration"
