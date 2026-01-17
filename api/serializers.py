from rest_framework import serializers
from trophies.models import Profile, EarnedTrophy, Comment, CommentVote
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
        rarest = obj.earned_trophy_entries.filter(earned=True).select_related('trophy').order_by('trophy__trophy_earn_rate')[:3]
        return [
            {
                'name': et.trophy.trophy_name,
                'earn_rate': et.trophy.trophy_earn_rate,
                'game': et.trophy.game.title_name,
            } for et in rarest
        ]
    
    def get_recent_platinums(self, obj):
        platinums = obj.earned_trophy_entries.filter(earned=True, trophy__trophy_type='platinum').select_related('trophy').order_by(F('earned_date_time').desc(nulls_last=True))[:3]
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
            'id', 'author', 'body', 'image', 'upvote_count',
            'is_edited', 'is_deleted', 'created_at', 'updated_at',
            'depth', 'parent', 'replies',
            'user_has_voted', 'can_edit', 'can_delete'
        ]
        read_only_fields = fields

    def get_replies(self, obj):
        """Recursively serialize replies."""
        replies = obj.replies.filter(is_deleted=False).order_by('-upvote_count', '-created_at')
        return CommentSerializer(replies, many=True, context=self.context).data

    def get_user_has_voted(self, obj):
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