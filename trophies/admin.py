from django.contrib import admin
from .models import Profile, Game, Trophy, EarnedTrophy


# Register your models here.
@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("psn_username", "user", "is_linked", "last_synced", "sync_tier")
    list_filter = ("is_linked", "sync_tier")
    search_fields = ("psn_username", "user__username__iexact")
    raw_id_fields = ("user",)
    ordering = ("psn_username",)
    actions = ["link_to_user"]

    def link_to_user(self, request, queryset):
        for profile in queryset:
            if not profile.is_linked:
                profile.link_to_user(request.user)
                self.message_user(
                    request, f"Linked {profile.psn_username} to {request.user.username}"
                )

    link_to_user.short_description = "Link selected profiles to current user"


@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ("title", "psn_id", "platform", "total_trophies")
    search_fields = ("title", "psn_id")
    list_filter = ("platform",)
    ordering = ("title",)


@admin.register(Trophy)
class TrophyAdmin(admin.ModelAdmin):
    list_display = ("name", "game", "type", "earn_rate", "earned_by_count")
    list_filter = ("type", "game__platform")
    search_fields = ("name", "description")
    raw_id_fields = ("game",)
    ordering = ("name",)

    def earned_by_count(self, obj):
        return obj.earned_by.count()

    earned_by_count.short_description = "Earned By"


@admin.register(EarnedTrophy)
class EarnedTrophyAdmin(admin.ModelAdmin):
    list_display = ("profile", "trophy", "earned_date", "last_updated")
    list_filter = ("earned_date",)
    search_fields = ("profile__psn_username", "trophy__name")
    raw_id_fields = ("profile", "trophy")
