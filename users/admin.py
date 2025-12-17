from django.contrib import admin
from django.contrib.auth.admin import UserAdmin  # Extend for auth features
from .models import CustomUser
from .forms import CustomUserCreationForm  # If adding custom add/change forms

@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    add_form = CustomUserCreationForm

    # Fields for list view (efficient, searchable)
    list_display = ('email', 'is_linked_to_profile', 'user_timezone', 'is_staff', 'is_active', 'date_joined')
    list_filter = ('is_staff', 'is_active', 'user_timezone')
    search_fields = ('email',)
    ordering = ('email',)

    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal Info', {'fields': ('user_timezone',)}),
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
