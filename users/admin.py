from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser


# Register your models here.
@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = (
        "username",
        "email",
        "is_linked_to_profile",
        "date_joined",
        "is_active",
        "user_timezone",
    )
    list_filter = ("is_active", "is_staff", "date_joined", "user_timezone")
    search_fields = ("username__iexact", "email__iexact")
    ordering = ("username",)
    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Personal Info", {"fields": ("email", "first_name", "last_name", "user_timezone")}),
        (
            "Permissions",
            {
                "fields": (
                    "is_active",
                    "is_staff",
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": ("username", "email", "password1", "password2"),
            },
        ),
    )

    def is_linked_to_profile(self, obj):
        return hasattr(obj, "profile") and obj.profile.is_linked

    is_linked_to_profile.boolean = True
    is_linked_to_profile.short_description = "PSN Linked"
