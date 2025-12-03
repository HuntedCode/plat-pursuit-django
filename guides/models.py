from django.db import models
from django.core.exceptions import ValidationError
from django.conf import settings
from django_ckeditor_5.fields import CKEditor5Field
from trophies.models import Concept, Profile

# Create your models here.
class GuideCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['name']
        verbose_name_plural = 'Guide Categories'

    def __str__(self):
        return self.name
    
class Guide(models.Model):
    title = models.CharField(max_length=255)
    content = CKEditor5Field(config_name='default')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name='authored_guides')
    concept = models.ForeignKey(Concept, on_delete=models.CASCADE, related_name='guides')
    categories = models.ManyToManyField(GuideCategory, related_name='guides', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    vote_score = models.IntegerField(default=0)

    class Meta:
        ordering = ['-vote_score', '-updated_at']
        indexes = [
            models.Index(fields=['concept', 'vote_score'], name='guide_concept_vote_idx'),
            models.Index(fields=['created_at'], name='guide_created_at_idx'),
        ]

    def __str__(self):
        return self.title
    
    def save(self, *args, **kwargs):
        if not self.author.profile.is_verified:
            raise ValidationError("Author must have a verified profile to create guides.")
        if not self.author.profile.played_games.filter(game__concept=self.concept).exists():
            raise ValidationError("Author must own at least one version of the game (in the Concept) to create a guide.")
        super().save(*args, **kwargs)

class Vote(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='guide_votes')
    guide = models.ForeignKey(Guide, on_delete=models.CASCADE, related_name='votes')
    value = models.IntegerField(choices=[(1, 'Upvote'), (-1, 'Downvote')])

    class Meta:
        unique_together = ['user', 'guide']
        indexes = [models.Index(fields=['guide', 'user'], name='vote_guide_user_idx')]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        self.guide.vote_score = self.guide.votes.aggregate(models.Sum('value'))['value__sum'] or 0
        self.guide.save(update_fields=['vote_score'])