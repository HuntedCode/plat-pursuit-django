from django.db import models
from django.utils import timezone
from users.models import CustomUser
from django.core.validators import RegexValidator
from trophies.utils import count_unique_game_groups, TITLE_STATS_SUPPORTED_PLATFORMS, NA_REGION_CODES, EU_REGION_CODES, JP_REGION_CODES, AS_REGION_CODES, SHOVELWARE_THRESHOLD


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
        validators=[
            RegexValidator(
                regex=r"^[a-zA-Z0-9_-]{3,16}$",
                message="PSN username must be 3-16 characters, using letters, numbers, hyphens or underscores.",
            )
        ],
    )
    account_id = models.CharField(max_length=20, unique=True, blank=True, null=True)
    np_id = models.CharField(max_length=50, blank=True, null=True)
    avatar_url = models.URLField(blank=True, null=True)
    is_plus = models.BooleanField(default=False)
    about_me = models.TextField(blank=True)
    languages_used = models.JSONField(default=list, blank=True)
    trophy_level = models.IntegerField(default=0)
    progress = models.IntegerField(default=0)
    tier = models.IntegerField(default=0)
    earned_trophy_summary = models.JSONField(default=dict, blank=True)
    country = models.CharField(max_length=250, blank=True, null=True)
    country_code = models.CharField(max_length=5, blank=True, null=True)
    flag = models.CharField(max_length=5, blank=True, null=True)
    extra_data = models.JSONField(default=dict, blank=True)
    last_synced = models.DateTimeField(default=timezone.now)
    sync_tier = models.CharField(
        max_length=10,
        choices=[("basic", "Basic"), ("preferred", "Preferred")],
        default="basic",
    )
    is_linked = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["psn_username"], name="psn_username_idx"),
            models.Index(fields=["account_id"], name="account_id_idx"),
        ]

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

class FeaturedProfile(models.Model):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE)
    priority = models.IntegerField(default=0, help_text='Higher = preferred for display')
    start_date = models.DateField(null=True, blank=True, help_text="Feature from this date")
    end_date = models.DateField(null=True, blank=True, help_text="Feature until this date")
    reason = models.CharField(max_length=100, blank=True, choices=[('admin_pick', 'Admin Pick'), ('top_week', 'Top of Week'), ('event', 'Event')])

    class Meta:
        ordering = ['-priority']


class Game(models.Model):
    np_communication_id = models.CharField(
        max_length=50, unique=True, blank=True, null=True
    )
    np_service_name = models.CharField(max_length=50, blank=True)
    trophy_set_version = models.CharField(max_length=10, blank=True)
    title_name = models.CharField(max_length=255)
    title_detail = models.TextField(blank=True, null=True)
    title_image = models.URLField(blank=True, null=True)
    title_icon_url = models.URLField(blank=True, null=True)
    title_platform = models.JSONField(default=list, blank=True)
    has_trophy_groups = models.BooleanField(default=False)
    defined_trophies = models.JSONField(default=dict, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    concept = models.ForeignKey('Concept', null=True, blank=True, on_delete=models.CASCADE, related_name='games')
    region = models.JSONField(default=list, blank=True)
    title_ids = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    played_count = models.PositiveIntegerField(default=0, help_text="Denormalized count of profiles that have played the game (PP-specific).")
    is_regional = models.BooleanField(default=False)
    is_shovelware = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["np_communication_id", "title_name"], name="game_idx"),
            models.Index(fields=['played_count'], name='game_played_count_idx'),
            models.Index(fields=['title_name'], name='game_title_idx'),
            models.Index(fields=['title_platform'], name='game_platform_idx'),
            models.Index(fields=['created_at'], name='game_created_idx'),
        ]

    def add_concept(self, concept):
        if concept and not self.concept:
            self.concept = concept
            self.save(update_fields=['concept'])
    
    def add_region(self, region: str):
        if region and region not in self.region:
            if region in NA_REGION_CODES:
                region = 'NA'
            elif region in EU_REGION_CODES:
                region = 'EU'
            elif region in JP_REGION_CODES:
                region = 'JP'
            elif region in AS_REGION_CODES:
                region = 'AS'
            else:
                return
            self.region.append(region)
            self.save(update_fields=['region'])
    
    def add_title_id(self, title_id: str):
        if title_id and title_id not in self.title_ids:
            self.title_ids.append(title_id)
            self.save(update_fields=['title_ids'])
    
    def update_is_shovelware(self, platinum_earn_rate: str):
        self.is_shovelware = float(platinum_earn_rate) >= SHOVELWARE_THRESHOLD
        self.save(update_fields=['is_shovelware'])


    def __str__(self):
        return self.title_name

class Concept(models.Model):
    concept_id = models.CharField(max_length=50, unique=True)
    unified_title = models.CharField(max_length=255, blank=True)
    title_ids = models.JSONField(default=list, blank=True)
    publisher_name = models.CharField(max_length=255, blank=True)
    genres = models.JSONField(default=list, blank=True)
    subgenres = models.JSONField(default=list, blank=True)
    descriptions = models.JSONField(default=dict, blank=True)
    content_rating = models.JSONField(default=dict, blank=True)
    media = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['concept_id'], name='concept_id_idx'),
            models.Index(fields=['unified_title'], name='concept_title_idx'),
            models.Index(fields=['publisher_name'], name='content_publisher_idx'),
        ]
    
    def add_title_id(self, title_id: str):
        if title_id and title_id not in self.title_ids:
            self.title_ids.append(title_id)
            self.save(update_fields=['title_ids'])

    def check_and_mark_regional(self):
        """Check if this concept has multiple games for the same platform. If so, mark as regional. Run post sync."""
        for platform in TITLE_STATS_SUPPORTED_PLATFORMS:
                games = self.games.filter(title_platform__contains=platform)
                if count_unique_game_groups(games) > 1:
                    for game in games:
                        game.is_regional = True
                        game.save(update_fields=['is_regional'])

    def __str__(self):
        return self.unified_title or self.concept_id

class TitleID(models.Model):
    title_id = models.CharField(max_length=50, unique=True)
    platform = models.CharField(max_length=10)
    region = models.CharField(max_length=10, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['title_id'], name='title_id_idx'),
            models.Index(fields=['region'], name='title_region_idx'),
        ]

    def __str__(self):
        return self.title_id

class ProfileGame(models.Model):
    profile = models.ForeignKey(
        Profile, on_delete=models.CASCADE, related_name="played_games"
    )
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="played_by")
    play_count = models.IntegerField(default=0)
    first_played_date_time = models.DateTimeField(blank=True, null=True)
    last_played_date_time = models.DateTimeField(blank=True, null=True)
    play_duration = models.DurationField(blank=True, null=True)
    progress = models.IntegerField(default=0)
    hidden_flag = models.BooleanField(default=False)
    earned_trophies = models.JSONField(default=dict, blank=True)
    last_updated_datetime = models.DateTimeField(blank=True, null=True)
    last_sync = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["profile", "game"]
        indexes = [
            models.Index(fields=["last_updated_datetime"], name="profilegame_updated_idx"),
            models.Index(fields=["progress"], name="profilegame_progress_idx"),
        ]


class FeaturedGame(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE)
    priority = models.IntegerField(default=0, help_text="Higher = shown first")
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    reason = models.CharField(max_length=100, blank=True, choices=[('staff_pick', 'Staff Pick'), ('event', 'Event'), ('trending', 'Trending')])

    class Meta:
        ordering = ['-priority']


class Trophy(models.Model):
    trophy_set_version = models.CharField(max_length=10, blank=True)
    trophy_id = models.IntegerField()
    trophy_type = models.CharField(max_length=20)
    trophy_name = models.CharField(max_length=255)
    trophy_detail = models.TextField(blank=True, null=True)
    trophy_icon_url = models.URLField(blank=True, null=True)
    trophy_group_id = models.CharField(max_length=10, default="default")
    progress_target_value = models.CharField(max_length=50, blank=True, null=True)
    reward_name = models.CharField(max_length=255, blank=True, null=True)
    reward_img_url = models.URLField(blank=True, null=True)
    trophy_rarity = models.IntegerField(blank=True, null=True, help_text='Common=3, Rare=2, Very_Rare=1, Ultra_Rare=0')  # PSN global
    trophy_earn_rate = models.FloatField(default=0.0)  # PSN global
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="trophies")
    earned_by = models.ManyToManyField(
        Profile, through="EarnedTrophy", related_name="earned_trophies"
    )
    earned_count = models.PositiveIntegerField(default=0, help_text="Denormalized count of profiles that have earned this trophy (PP-specific).")
    earn_rate = models.FloatField(default=0.0)

    class Meta:
        unique_together = ["trophy_id", "game"]
        indexes = [
            models.Index(fields=["trophy_name", "trophy_type"], name="trophy_idx"),
            models.Index(fields=["earned_count"], name="trophy_earned_count_idx"),
            models.Index(fields=['trophy_earn_rate'], name="trophy_psn_rate_idx"),
            models.Index(fields=['earn_rate'], name='trophy_pp_rate_idx'),
        ]
    
    def get_pp_rarity_tier(self):
        """Compute Plat Pursuit specific rarity tier based on earn_rate.
        
        PLACEHOLDER THRESHOLDS - Refine based on discussion.
        
        Returns a string tier (e.g., 'Common', 'Rare') for display/filtering.
        """
        rate = self.earn_rate
        # EXAMPLE THRESHOLDS - ADJUST LATER
        if rate > 0.5:
            return 'Common'
        elif rate > 0.2:
            return 'Uncommon'
        elif rate > 0.05:
            return 'Rare'
        elif rate > 0.01:
            return 'Very Rare'
        else:
            return 'Ultra Rare'

    def get_most_recent_earner(self):
        recent_entry = self.earned_trophy_entries.filter(earned=True).order_by('-earned_date_time').first()
        if recent_entry:
            return recent_entry.profile.psn_username
        return None

    def increment_earned_count(self):
        earned_count = self.earned_count
        earned_count = earned_count + 1
        earn_rate = earned_count / self.game.played_count if self.game.played_count > 0 else 0.0
        self.earned_count = earned_count
        self.earn_rate = earn_rate
        self.save(update_fields=['earned_count', 'earn_rate'])

    def __str__(self):
        return f"{self.trophy_name} ({self.game.title_name})"


class EarnedTrophy(models.Model):
    profile = models.ForeignKey(
        Profile, on_delete=models.CASCADE, related_name="earned_trophy_entries"
    )
    trophy = models.ForeignKey(
        Trophy, on_delete=models.CASCADE, related_name="earned_trophy_entries"
    )
    earned = models.BooleanField(default=False)
    trophy_hidden = models.BooleanField(default=False)
    progress = models.IntegerField(default=0, blank=True, null=True)
    progress_rate = models.FloatField(default=0, blank=True, null=True)
    progressed_date_time = models.DateTimeField(blank=True, null=True)
    earned_date_time = models.DateTimeField(null=True, blank=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["profile", "trophy"]
        indexes = [
            models.Index(fields=["last_updated"], name="earned_trophy_updated_idx")
        ]


class Event(models.Model):
    title = models.CharField(max_length=255)
    date = models.DateField(help_text="Start date")
    end_date = models.DateField(null=True, blank=True, help_text="End date if multi-day")
    color = models.CharField(max_length=20, choices=[('primary', 'Primary'), ('secondary', 'Secondary'), ('accent', 'Accent')], default='primary', help_text='For badge/styling')
    description = models.TextField(blank=True)
    time = models.CharField(max_length=100, blank=True, help_text="e.g., 'All Day' or '12pm - 4pm EDT'")
    slug = models.SlugField(blank=True, help_text="For event page URL")

    class Meta:
        ordering = ['date']
        indexes = [
            models.Index(fields=['date'], name='event_date_idx'),
        ]
    
    def __str__(self):
        return self.title
    
    def save(self, *args, **kwargs):
        if self.end_date and self.end_date < self.date:
            raise ValueError("End date must be after or on start date")
        super().save(*args, **kwargs)
    
    @property
    def is_upcoming(self):
        return self.date >= timezone.now().date()

class APIAuditLog(models.Model):
    timestamp = models.DateTimeField(auto_now_add=True)
    token_id = models.CharField(max_length=64)
    ip_used = models.CharField(max_length=45, blank=True)
    endpoint = models.CharField(max_length=100)
    profile = models.ForeignKey(
        Profile, on_delete=models.SET_NULL, null=True, blank=True
    )
    status_code = models.IntegerField()
    response_time = models.FloatField()
    error_message = models.TextField(blank=True)
    calls_remaining = models.IntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=["timestamp", "status_code"])]

    def __str__(self):
        return f"{self.endpoint} at {self.timestamp}"
