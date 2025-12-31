from django.db import models
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.utils.translation import gettext_lazy as _
import pytz
from trophies.utils import REGIONS
from djstripe.models import Subscription


# Create your models here.
class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('The Email field must be set')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self.db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')
        return self.create_user(email, password, **extra_fields)


class CustomUser(AbstractUser):
    email = models.EmailField(_("email address"), unique=True, blank=False, null=False)
    user_timezone = models.CharField(max_length=63, choices=[(tz, tz) for tz in pytz.common_timezones], default='UTC', help_text="User's preferred timezone. UTC default.")
    default_region = models.CharField(max_length=2, choices=[(r, r) for r in REGIONS], null=True, blank=True, default=None, help_text="User's preferred default region filter for games.")
    stripe_customer_id = models.CharField(max_length=255, blank=True, null=True, help_text="Stripe Customer ID for this user.")
    premium_tier = models.CharField(max_length=50, blank=True, null=True, choices=[('ad_free', 'Ad Free'), ('premium_monthly', 'Premium Monthly'), ('premium_yearly', 'Premium Yearly'), ('supporter', 'Supporter')], help_text="User's subscription tier.")

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    class Meta:
        indexes = [
            models.Index(fields=["email"]),
        ]
    
    def is_premium(self):
        if not self.stripe_customer_id:
            return False
        subs = Subscription.objects.filter(customer__id=self.stripe_customer_id)
        return any(sub.status == 'active' for sub in subs)
    
    def update_subscription_status(self):
        if not self.stripe_customer_id:
            self.premium_tier = None
            self.save()
            return
        
        subs = Subscription.objects.filter(customer__id=self.stripe_customer_id)
        active_sub = next((sub for sub in subs if sub.status == 'active'), None)
        if active_sub:
            product_id = active_sub.plan['product']
            if product_id == 'prod_ThqmB1BoJZn7TY':
                self.premium_tier = 'ad_free'
            elif product_id == 'prod_ThqljWr4cvnFFF':
                self.premium_tier = 'premium_monthly'
            elif product_id == 'prod_ThqpPjDyERnoaF':
                self.premium_tier = 'premium_yearly'
            elif product_id == 'prod_ThquYbJOcBn65m':
                self.premium_tier = 'supporter'
            else:
                self.premium_tier = None
        else:
            self.premium_tier = None
        self.save()

class UserSubscription(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='subscription')
    stripe_subscription = models.OneToOneField('djstripe.Subscription', on_delete=models.SET_NULL, null=True, blank=True)
    start_date = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "User Subscription"