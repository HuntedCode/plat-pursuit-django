from django.db import models
from django.utils import timezone
from users.models import CustomUser


# Create your models here.
class Profile(models.Model):
    user = models.OneToOneField(
        CustomUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="profile",
    )
    psn_username = models.CharField(
        max_length=16,
        unique=True,
        help_text="PSN Online ID (case-sensitive as per PSN)",
    )
    avatar_url = models.URLField(blank=True, null=True)
    last_synced = models.DateTimeField(default=timezone.now)
    sync_tier = models.CharField(
        max_length=10,
        choices=[("basic", "Basic"), ("preferred", "Preferred")],
        default="basic",
    )
    is_linked = models.BooleanField(default=False)

    class Meta:
        indexes = [models.Index(fields=["psn_username"])]

        def __str__(self):
            return self.psn_username

        def link_to_user(self, user):
            if not self.user:
                self.user = user
                self.is_linked = True
                self.save()

        def unlink_user(self):
            if self.user:
                self.user = models.SET_NULL
                self.is_linked = False
                self.save()


class Game(models.Model):
    psn_id = models.CharField(max_length=50, unique=True)
    title = models.CharField(max_length=255)
    platform = models.CharField(max_length=50)
    icon_url = models.URLField(blank=True, null=True)
    total_trophies = models.IntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["psn_id", "title"])]

    def __str__(self):
        return self.title


class Trophy(models.Model):
    trophy_id = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    description = models.TextField()
    type = models.CharField(
        max_length=20,
        choices=[
            ("bronze", "Bronze"),
            ("silver", "Silver"),
            ("gold", "Gold"),
            ("platinum", "Platinum"),
        ],
    )
    earn_rate = models.FloatField(default=0.0)
    icon_url = models.URLField(blank=True, null=True)
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="trophies")
    earned_by = models.ManyToManyField(
        Profile, through="EarnedTrophy", related_name="earned_trophies"
    )

    class Meta:
        unique_together = ["trophy_id", "game"]
        indexes = [models.Index(fields=["name", "rarity"])]

    def __str__(self):
        return f"{self.name} ({self.game.title})"


class EarnedTrophy(models.Model):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE)
    trophy = models.ForeignKey(Trophy, on_delete=models.CASCADE)
    earned_date = models.DateTimeField(null=True, blank=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["profile", "trophy"]
        indexes = [models.Index(fields=["last_updated"])]
