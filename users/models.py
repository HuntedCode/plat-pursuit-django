from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils.translation import gettext_lazy as _
import pytz


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
    user_timezone = models.CharField(max_length=63, choices=[(tz, tz) for tz in pytz.common_timezones], default='UTC', help_text="User's preferred timezone. UTC default.")

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["email"]

    def save(self, *args, **kwargs):
        self.username = self.username.lower()
        super().save(*args, **kwargs)

    class Meta:
        indexes = [
            models.Index(fields=["username"])   
        ]