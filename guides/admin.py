from django.contrib import admin
from .models import GuideCategory, Guide, Vote

# Register your models here.
@admin.register(GuideCategory)
class GuideCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'slug', 'description_preview')
    search_fields = ('name', 'slug')
    prepopulated_fields = {'slug': ('name',)}
    ordering = ('name',)

    def description_preview(self, obj):
        return obj.description[:50] + '...' if obj.description else ''
    description_preview.short_description = 'Description'

class VoteInline(admin.TabularInline):
    model = Vote
    extra = 0
    fields = ('user', 'value')
    readonly_fields = ('user', 'value')
    ordering = ('-id',)

@admin.register(Guide)
class GuideAdmin(admin.ModelAdmin):
    list_display = ('title', 'author', 'concept', 'vote_score', 'created_at', 'updated_at')
    list_filter = ('categories', 'created_at')
    search_fields = ('title', 'content', 'author__username', 'concept__unified_title')
    raw_id_fields = ('concept',)
    inlines = [VoteInline]
    ordering = ('-vote_score', '-updated_at')
    readonly_fields = ('vote_score',)

@admin.register(Vote)
class VoteAdmin(admin.ModelAdmin):
    list_display = ('user', 'guide', 'value')
    list_filter = ('value',)
    search_fields = ('user__username', 'guide__title')
    ordering = ('-id',)