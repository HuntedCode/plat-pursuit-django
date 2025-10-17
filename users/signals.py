from django.db.models.signals import post_save
from django.dispatch import receiver
from trophies.models import Profile
from .models import CustomUser


@receiver(post_save, sender=CustomUser)
def create_profile(sender, instance, created, **kwargs):
    if created:
        Profile.objects.create(user=instance, is_linked=True)
