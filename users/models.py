from django.db import models
from django.contrib.auth.models import AbstractUser
from django.utils.translation import gettext_lazy as _
import pytz


# Create your models here.
class CustomUser(AbstractUser):
    email = models.EmailField(_("email address"), unique=True, blank=False, null=False)
    user_timezone = models.CharField(max_length=63, choices=[(tz, tz) for tz in pytz.common_timezones], default='UTC', help_text="User's preferred timezone. UTC default.")

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        indexes = [
            models.Index(fields=["email"])   
        ]