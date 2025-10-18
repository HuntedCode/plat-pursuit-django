from celery import shared_task
from django.utils import timezone


@shared_task
def test_task(profile_id):
    print(f"Test task for profile {profile_id} at {timezone.now()}")
    return f"Test completed for {profile_id}"
