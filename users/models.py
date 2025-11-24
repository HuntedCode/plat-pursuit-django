from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils.translation import gettext_lazy as _
from django.utils import timezone


# Create your models here.
class CustomUser(AbstractUser):
    email = models.EmailField(_("email address"), unique=True, blank=False, null=False)
    username = models.CharField(
        _("username"),
        max_length=150,
        unique=True,
        help_text=(
            "Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only."
        ),
        validators=[AbstractUser.username_validator],
        error_messages={"unique": _("A user with that username already exists.")},
    )
    discord_id = models.BigIntegerField(unique=True, blank=True, null=True, help_text='Unique Discord user ID. Set upon linking via bot or site.')
    discord_linked_at = models.DateTimeField(blank=True, null=True, help_text='Timestamp when Discord was linked.')

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["email"]

    def save(self, *args, **kwargs):
        self.username = self.username.lower()
        super().save(*args, **kwargs)

    class Meta:
        indexes = [
            models.Index(fields=["username"]),
            models.Index(fields=['discord_id']),    
        ]

    def __str__(self):
        return self.username

    def link_discord(self, discord_id: int):
        if self.discord_id:
            raise ValueError("Discord alread linked to this user.")
        self.discord_id = discord_id
        self.discord_linked_at = timezone.now()
        self.save(update_fields=['discord_id', 'discord_linked_at'])
    
    def unlink_discord(self):
        self.discord_id = None
        self.discord_linked_at = None
        self.save(update_fields=['discord_id', 'discord_linked_at'])