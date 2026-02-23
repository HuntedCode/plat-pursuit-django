from rest_framework import serializers
from trophies.models import (
    Profile, EarnedTrophy, Comment, CommentVote,
    Checklist, ChecklistSection, ChecklistItem, ChecklistVote, UserChecklistProgress
)
from django.db.models import F
from django.utils.translation import gettext_lazy as _

class GenerateCodeSerializer(serializers.Serializer):
    psn_username = serializers.CharField(max_length=16, required=True)
    discord_id = serializers.IntegerField(required=False, min_value=0)

    def validate_psn_username(self, value):
        from django.core.validators import RegexValidator
        validator = RegexValidator(
            regex=r"^[a-zA-Z0-9_-]{3,16}$",
            message="PSN username must be 3-16 characters, using letters, numbers, hyphens or underscores."
        )
        validator(value)
        return value.lower()
    
    def validate(self, data):
        if 'discord_id' in data and Profile.objects.filter(discord_id=data['discord_id']).exists():
            raise serializers.ValidationError("This Discord account is already linked to a PSN account.")
        return data

class VerifySerializer(serializers.Serializer):
    discord_id = serializers.IntegerField(required=True, min_value=0)
    psn_username = serializers.CharField(max_length=16, required=True)

    def validate_psn_username(self, value):
        from django.core.validators import RegexValidator
        validator = RegexValidator(
            regex=r"^[a-zA-Z0-9_-]{3,16}$",
            message="PSN username must be 3-16 characters, using letters, numbers, hyphens or underscores."
        )
        validator(value)
        return value.lower()

    def validate(self, data):
        if Profile.objects.filter(discord_id=data['discord_id']).exists():
            raise serializers.ValidationError("Discord ID already linked to a PSN profile.")
        return data

class ProfileSerializer(serializers.ModelSerializer):
    total_trophies = serializers.IntegerField(source='get_total_trophies_from_summary', read_only=True)
    total_games = serializers.SerializerMethodField()
    rarest_trophies = serializers.SerializerMethodField()
    recent_platinums = serializers.SerializerMethodField()

    class Meta:
        model = Profile
        fields = [
            'display_psn_username', 'account_id', 'avatar_url', 'is_plus', 'trophy_level', 'progress',
            'earned_trophy_summary', 'last_synced', 'country', 'flag', 'is_discord_verified', 'psn_history_public',
            'total_trophies', 'total_games', 'rarest_trophies', 'recent_platinums'
        ]
        read_only_fields = fields
    
    def get_total_games(self, obj):
        return obj.played_games.count()
    
    def get_rarest_trophies(self, obj):
        rarest = obj.earned_trophy_entries.filter(earned=True).select_related('trophy__game').order_by('trophy__trophy_earn_rate')[:3]
        return [
            {
                'name': et.trophy.trophy_name,
                'earn_rate': et.trophy.trophy_earn_rate,
                'game': et.trophy.game.title_name,
            } for et in rarest
        ]
    
    def get_recent_platinums(self, obj):
        platinums = obj.earned_trophy_entries.filter(earned=True, trophy__trophy_type='platinum').select_related('trophy__game').order_by(F('earned_date_time').desc(nulls_last=True))[:3]
        return [
            {
                'name': et.trophy.trophy_name,
                'earned_date': et.earned_date_time.strftime('%Y-%m-%d %H:%M'),
                'game': et.trophy.game.title_name,
            } for et in platinums
        ]

class TrophyCaseSerializer(serializers.ModelSerializer):
    icon_url = serializers.CharField(source='trophy.trophy_icon_url')

    class Meta:
        model = EarnedTrophy
        fields = ['icon_url',]


class CommentAuthorSerializer(serializers.ModelSerializer):
    """Serializer for comment author info."""
    username = serializers.CharField(source='display_psn_username')

    class Meta:
        model = Profile
        fields = ['id', 'username', 'avatar_url', 'flag']


class CommentSerializer(serializers.ModelSerializer):
    """Serializer for Comment with nested replies."""
    author = CommentAuthorSerializer(source='profile', read_only=True)
    body = serializers.CharField(source='display_body', read_only=True)
    replies = serializers.SerializerMethodField()
    user_has_voted = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()
    can_delete = serializers.SerializerMethodField()

    class Meta:
        model = Comment
        fields = [
            'id', 'author', 'body', 'upvote_count',
            'is_edited', 'is_deleted', 'created_at', 'updated_at',
            'depth', 'parent', 'replies',
            'user_has_voted', 'can_edit', 'can_delete'
        ]
        read_only_fields = fields

    def get_replies(self, obj):
        """Serialize replies using prefetched data when available."""
        # Use prefetched replies if available (avoids N+1)
        if hasattr(obj, '_prefetched_objects_cache') and 'replies' in obj._prefetched_objects_cache:
            replies = [r for r in obj._prefetched_objects_cache['replies'] if not r.is_deleted]
            replies.sort(key=lambda r: (-r.upvote_count, -r.created_at.timestamp()))
        else:
            replies = obj.replies.filter(is_deleted=False).order_by('-upvote_count', '-created_at')
        return CommentSerializer(replies, many=True, context=self.context).data

    def get_user_has_voted(self, obj):
        # Use pre-fetched voted_comment_ids from context when available
        voted_ids = self.context.get('voted_comment_ids')
        if voted_ids is not None:
            return obj.id in voted_ids
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return False
        return CommentVote.objects.filter(comment=obj, profile=profile).exists()

    def get_can_edit(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        profile = getattr(request.user, 'profile', None)
        return profile and obj.profile == profile and not obj.is_deleted

    def get_can_delete(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        profile = getattr(request.user, 'profile', None)
        is_admin = request.user.is_staff
        return (profile and (obj.profile == profile or is_admin)) and not obj.is_deleted


class CommentCreateSerializer(serializers.Serializer):
    """Serializer for comment creation input."""
    body = serializers.CharField(max_length=2000, required=True)
    parent_id = serializers.IntegerField(required=False, allow_null=True)


# ---------- Checklist Serializers ----------

class ChecklistAuthorSerializer(serializers.ModelSerializer):
    """Serializer for checklist author info."""
    username = serializers.SerializerMethodField()
    author_has_platinum = serializers.SerializerMethodField()

    class Meta:
        model = Profile
        fields = ['id', 'username', 'avatar_url', 'flag', 'user_is_premium', 'author_has_platinum']

    def get_username(self, obj):
        return obj.display_psn_username or obj.psn_username

    def get_author_has_platinum(self, obj):
        """Check if author has platinum for the checklist's game/concept."""
        # Try to get the checklist from context (set by parent serializer)
        checklist = self.context.get('checklist')
        if not checklist or not checklist.concept:
            return False

        from trophies.models import ProfileGame
        pg = ProfileGame.objects.filter(
            profile=obj,
            game__concept=checklist.concept
        ).order_by('-progress').first()

        return pg.has_plat if pg else False


class ChecklistItemSerializer(serializers.ModelSerializer):
    """Serializer for checklist items."""
    image_url = serializers.SerializerMethodField()
    rendered_html = serializers.SerializerMethodField()

    class Meta:
        model = ChecklistItem
        fields = ['id', 'text', 'item_type', 'trophy_id', 'order', 'image_url', 'rendered_html']
        read_only_fields = fields

    def get_image_url(self, obj):
        """Return absolute URL for image items."""
        if obj.item_type == 'image' and obj.image:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.image.url)
            return obj.image.url
        return None

    def get_rendered_html(self, obj):
        """Return rendered HTML for text_area items."""
        if obj.item_type == 'text_area' and obj.text:
            from trophies.services.checklist_service import ChecklistService
            return ChecklistService.process_markdown(obj.text)
        return None


class ChecklistSectionSerializer(serializers.ModelSerializer):
    """Serializer for checklist sections with items."""
    items = ChecklistItemSerializer(many=True, read_only=True)
    item_count = serializers.IntegerField(read_only=True)
    thumbnail_url = serializers.SerializerMethodField()

    class Meta:
        model = ChecklistSection
        fields = ['id', 'subtitle', 'description', 'order', 'items', 'item_count', 'thumbnail_url']
        read_only_fields = fields

    def get_thumbnail_url(self, obj):
        """Return absolute URL for section thumbnail."""
        if obj.thumbnail:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.thumbnail.url)
            return obj.thumbnail.url
        return None


class ChecklistSerializer(serializers.ModelSerializer):
    """Serializer for checklist list view."""
    author = ChecklistAuthorSerializer(source='profile', read_only=True)
    total_items = serializers.IntegerField(read_only=True)
    section_count = serializers.SerializerMethodField()
    user_has_voted = serializers.SerializerMethodField()
    user_progress = serializers.SerializerMethodField()
    can_edit = serializers.SerializerMethodField()
    can_save_progress = serializers.SerializerMethodField()
    thumbnail_url = serializers.SerializerMethodField()

    class Meta:
        model = Checklist
        fields = [
            'id', 'title', 'description', 'author', 'status',
            'upvote_count', 'progress_save_count', 'total_items', 'section_count',
            'user_has_voted', 'user_progress', 'can_edit', 'can_save_progress',
            'created_at', 'published_at', 'thumbnail_url'
        ]
        read_only_fields = fields

    def get_section_count(self, obj):
        return obj.sections.count()

    def get_user_has_voted(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return False
        # Check if annotated value exists (from queryset optimization)
        if hasattr(obj, 'user_has_voted'):
            return obj.user_has_voted
        return ChecklistVote.objects.filter(checklist=obj, profile=profile).exists()

    def get_user_progress(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return None
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return None
        # Check if annotated value exists (from queryset optimization)
        if hasattr(obj, 'user_progress_percentage'):
            if obj.user_progress_percentage > 0:
                return {
                    'percentage': obj.user_progress_percentage
                }
            return None
        try:
            progress = UserChecklistProgress.objects.get(checklist=obj, profile=profile)
            return {
                'items_completed': progress.items_completed,
                'total_items': progress.total_items,
                'percentage': progress.progress_percentage
            }
        except UserChecklistProgress.DoesNotExist:
            return None

    def get_can_edit(self, obj):
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        profile = getattr(request.user, 'profile', None)
        return profile and obj.profile == profile and not obj.is_deleted

    def get_can_save_progress(self, obj):
        """Check if user can save progress (any authenticated user with a linked profile)."""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return False
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return False
        return profile.is_linked

    def get_thumbnail_url(self, obj):
        """Return absolute URL for thumbnail."""
        if obj.thumbnail:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.thumbnail.url)
            return obj.thumbnail.url
        return None

    def to_representation(self, instance):
        """Override to pass checklist context to author serializer."""
        # Add checklist to context for nested serializers
        self.fields['author'].context.update({'checklist': instance})
        return super().to_representation(instance)


class ChecklistDetailSerializer(ChecklistSerializer):
    """Serializer for checklist detail with sections and items."""
    sections = ChecklistSectionSerializer(many=True, read_only=True)
    user_completed_items = serializers.SerializerMethodField()

    class Meta(ChecklistSerializer.Meta):
        fields = ChecklistSerializer.Meta.fields + ['sections', 'user_completed_items']

    def get_user_completed_items(self, obj):
        """Get list of completed item IDs for the viewing user."""
        request = self.context.get('request')
        if not request or not request.user.is_authenticated:
            return []
        profile = getattr(request.user, 'profile', None)
        if not profile:
            return []
        try:
            progress = UserChecklistProgress.objects.get(checklist=obj, profile=profile)
            return progress.completed_items
        except UserChecklistProgress.DoesNotExist:
            return []


class ChecklistCreateSerializer(serializers.Serializer):
    """Serializer for checklist creation input."""
    title = serializers.CharField(max_length=200, required=True)
    description = serializers.CharField(max_length=2000, required=False, allow_blank=True)


class ChecklistUpdateSerializer(serializers.Serializer):
    """Serializer for checklist update input."""
    title = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(max_length=2000, required=False, allow_blank=True)


class ChecklistSectionCreateSerializer(serializers.Serializer):
    """Serializer for section creation input."""
    subtitle = serializers.CharField(max_length=200, required=True)
    description = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    order = serializers.IntegerField(required=False, min_value=0)


class ChecklistSectionUpdateSerializer(serializers.Serializer):
    """Serializer for section update input."""
    subtitle = serializers.CharField(max_length=200, required=False)
    description = serializers.CharField(max_length=1000, required=False, allow_blank=True)
    order = serializers.IntegerField(required=False, min_value=0)


class ChecklistItemCreateSerializer(serializers.Serializer):
    """Serializer for item creation input."""
    text = serializers.CharField(max_length=2000, required=False)  # Optional for trophies
    item_type = serializers.ChoiceField(
        choices=['item', 'sub_header', 'text_area', 'trophy'],  # Add trophy
        default='item',
        required=False
    )
    trophy_id = serializers.IntegerField(required=False, allow_null=True)
    order = serializers.IntegerField(required=False, min_value=0)

    def validate(self, data):
        item_type = data.get('item_type', 'item')

        if item_type == 'trophy':
            if not data.get('trophy_id'):
                raise serializers.ValidationError("trophy_id is required for trophy items.")
        elif item_type in ['item', 'sub_header', 'text_area']:
            if not data.get('text'):
                raise serializers.ValidationError("text is required for this item type.")

        return data


class ChecklistItemUpdateSerializer(serializers.Serializer):
    """Serializer for item update input."""
    text = serializers.CharField(max_length=2000, required=False)
    item_type = serializers.ChoiceField(
        choices=['item', 'sub_header', 'text_area'],
        required=False
    )
    trophy_id = serializers.IntegerField(required=False, allow_null=True)
    order = serializers.IntegerField(required=False, min_value=0)


class ChecklistItemBulkItemSerializer(serializers.Serializer):
    """Serializer for a single item in bulk upload."""
    text = serializers.CharField(max_length=500, required=True, allow_blank=False)
    item_type = serializers.ChoiceField(
        choices=['item', 'sub_header'],
        default='item',
        required=False
    )


class ChecklistItemBulkCreateSerializer(serializers.Serializer):
    """Serializer for bulk item creation input."""
    items = ChecklistItemBulkItemSerializer(many=True, required=True)

    def validate_items(self, value):
        if not value:
            raise serializers.ValidationError("At least one item is required.")

        if len(value) > 100:
            raise serializers.ValidationError("Bulk upload limited to 100 items at a time.")

        return value


class ChecklistReorderSerializer(serializers.Serializer):
    """Serializer for reordering sections or items."""
    ids = serializers.ListField(
        child=serializers.IntegerField(),
        required=True,
        min_length=1
    )


class ChecklistReportSerializer(serializers.Serializer):
    """Serializer for checklist report input."""
    reason = serializers.ChoiceField(
        choices=['spam', 'inappropriate', 'misinformation', 'plagiarism', 'other'],
        required=True
    )
    details = serializers.CharField(max_length=500, required=False, allow_blank=True)


class TrophySerializer(serializers.Serializer):
    """Serializer for Trophy model (for checklist trophy selection)."""
    id = serializers.IntegerField()
    trophy_name = serializers.CharField()
    trophy_detail = serializers.CharField()
    trophy_icon_url = serializers.URLField()
    trophy_type = serializers.CharField()
    trophy_rarity = serializers.IntegerField()
    trophy_earn_rate = serializers.FloatField()
    trophy_group_id = serializers.CharField()
    trophy_group_name = serializers.CharField(required=False, allow_blank=True)
    is_base_game = serializers.BooleanField(required=False)  # True if group_id == 'default'
    is_used = serializers.BooleanField(required=False)  # Annotated field


class GameSelectionSerializer(serializers.Serializer):
    """Serializer for setting checklist game."""
    game_id = serializers.IntegerField(required=True)


class ChecklistImageUploadSerializer(serializers.Serializer):
    """Upload checklist thumbnail."""
    thumbnail = serializers.ImageField(required=True)

    def validate_thumbnail(self, value):
        if value.size > 5 * 1024 * 1024:
            raise serializers.ValidationError("Image must be under 5MB.")
        return value


class SectionImageUploadSerializer(serializers.Serializer):
    """Upload section thumbnail."""
    thumbnail = serializers.ImageField(required=True)

    def validate_thumbnail(self, value):
        if value.size > 5 * 1024 * 1024:
            raise serializers.ValidationError("Image must be under 5MB.")
        return value


class ItemImageCreateSerializer(serializers.Serializer):
    """Create inline image item."""
    image = serializers.ImageField(required=True)
    text = serializers.CharField(required=False, max_length=500, allow_blank=True)
    order = serializers.IntegerField(required=False, min_value=0)

    def validate_image(self, value):
        if value.size > 5 * 1024 * 1024:
            raise serializers.ValidationError("Image must be under 5MB.")
        return value