from rest_framework import serializers
from trophies.models import Profile
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
            raise serializers.ValidationError("This Discord is already linked to a PSN account. Use /verify or contact an admin.")
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

    class Meta:
        model = Profile
        fields = [
            'display_psn_username', 'avatar_url', 'is_plus', 'trophy_level', 'progress', 'tier',
            'earned_trophy_summary', 'total_trophies', 'last_synced', 'country', 'flag'
        ]
        read_only_fields = fields