from django.contrib import admin

from .models import Fundraiser, Donation, DonationBadgeClaim


@admin.register(Fundraiser)
class FundraiserAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'campaign_type', 'start_date', 'end_date', 'banner_active')
    list_filter = ('campaign_type', 'banner_active')
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ('created_at',)


@admin.register(Donation)
class DonationAdmin(admin.ModelAdmin):
    list_display = ('user', 'fundraiser', 'amount', 'provider', 'status', 'badge_picks_earned', 'badge_picks_used', 'completed_at')
    list_filter = ('status', 'provider', 'fundraiser')
    list_select_related = ('fundraiser', 'profile', 'user')
    search_fields = ('user__email', 'profile__psn_username', 'provider_transaction_id')
    readonly_fields = ('created_at', 'completed_at', 'provider_transaction_id')
    raw_id_fields = ('user', 'profile', 'fundraiser')


@admin.register(DonationBadgeClaim)
class DonationBadgeClaimAdmin(admin.ModelAdmin):
    list_display = ('series_name', 'profile', 'status', 'claimed_at', 'completed_at')
    list_filter = ('status',)
    list_select_related = ('donation', 'profile', 'badge')
    search_fields = ('series_name', 'series_slug', 'profile__psn_username')
    readonly_fields = ('claimed_at',)
    raw_id_fields = ('donation', 'profile', 'badge')
