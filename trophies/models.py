from django.db import models
from django.utils import timezone
from users.models import CustomUser
from django.core.validators import RegexValidator, MinValueValidator, MaxValueValidator
from django.db.models import F, Avg, Count, FloatField, Case, When
from django.db.models.functions import Cast
from django.db.transaction import atomic
from datetime import timedelta
from trophies.utils import count_unique_game_groups, calculate_trimmed_mean, TITLE_STATS_SUPPORTED_PLATFORMS, NA_REGION_CODES, EU_REGION_CODES, JP_REGION_CODES, AS_REGION_CODES, SHOVELWARE_THRESHOLD
import secrets


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
    display_psn_username = models.CharField(
        max_length=16,
        blank=True,
        null=True,
        help_text="PSN username with original capitalization (populated from PSN API)"
    )
    verification_code = models.CharField(max_length=32, blank=True, null=True, help_text="Temporary code for PSN About Me verification.")
    verification_expires_at = models.DateTimeField(blank=True, null=True, help_text="Expiration timestamp for verification code.")
    is_discord_verified = models.BooleanField(default=False, help_text="True if Discord ownership verified via code.")
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
    last_profile_health_check = models.DateTimeField(default=timezone.now)
    sync_tier = models.CharField(
        max_length=10,
        choices=[("basic", "Basic"), ("preferred", "Preferred")],
        default="basic",
    )
    sync_status = models.CharField(
        max_length=20,
        choices=[('synced', 'Synced'), ('syncing', 'Syncing'), ('error', 'Error')],
        default='synced',
        help_text='Current sync state of the profile'
    )
    sync_progress_value = models.IntegerField(default=0, help_text='Current sync progress value')
    sync_progress_target = models.IntegerField(default=0, help_text='Current sync progress target')
    is_linked = models.BooleanField(default=False)
    psn_history_public = models.BooleanField(default=True, help_text="Flag indicating if PSN gaming history is public.")
    created_at = models.DateTimeField(auto_now_add=True)
    discord_id = models.BigIntegerField(unique=True, blank=True, null=True, help_text='Unique Discord user ID. Set on bot linking.')
    discord_linked_at = models.DateTimeField(blank=True, null=True, help_text='Timestamp when Discord was linked via bot.')
    total_trophies = models.PositiveIntegerField(default=0)
    total_unearned = models.PositiveIntegerField(default=0)
    total_plats = models.PositiveIntegerField(default=0)
    total_games = models.PositiveIntegerField(default=0)
    total_completes = models.PositiveIntegerField(default=0)
    avg_progress = models.FloatField(default=0.0)

    class Meta:
        indexes = [
            models.Index(fields=["psn_username"], name="psn_username_idx"),
            models.Index(fields=["account_id"], name="account_id_idx"),
            models.Index(fields=['discord_id'], name='discord_id_idx'),
            models.Index(fields=['is_discord_verified', 'last_synced'], name='verified_synced_idx'),
            models.Index(fields=['sync_status'], name='progile_sync_status_idx'),
        ]

    def __str__(self):
        return self.psn_username
    
    def save(self, *args, **kwargs):
        if self.psn_username:
            self.psn_username = self.psn_username.lower()
        super().save(*args, **kwargs)

    def link_to_user(self, user):
        if not self.user:
            self.user = user
            self.is_linked = True
            self.save(update_fields=['user', 'is_linked'])

    def unlink_user(self):
        if self.user:
            self.user = None
            self.is_linked = False
            self.save(update_fields=['user', 'is_linked'])
    
    def get_time_since_last_sync(self) -> timedelta:
        if self.last_synced:
            return timezone.now() - self.last_synced
        return 0
    
    def generate_verification_code(self):
        """Generate and set a secure, time-limited code."""
        self.verification_code = secrets.token_hex(3).upper()
        self.verification_expires_at = timezone.now() + timedelta(hours=1)
        self.save(update_fields=['verification_code', 'verification_expires_at'])
    
    def verify_code(self, fetched_about_me: str) -> bool:
        """Check if code is in About Me and unexpired."""
        if not self.verification_code or not self.verification_expires_at:
            return False
        if timezone.now() > self.verification_expires_at:
            self.clear_verification_code()
            return False
        if self.verification_code in fetched_about_me:
            self.clear_verification_code()
            return True
        return False
    
    def clear_verification_code(self):
        """Reset code fields for security/reuse."""
        self.verification_code = None
        self.verification_expires_at = None
        self.save(update_fields=['verification_code', 'verification_expires_at'])

    def get_total_trophies_from_summary(self):
        if self.earned_trophy_summary:
            return self.earned_trophy_summary.get('bronze', 0) + self.earned_trophy_summary.get('silver', 0) + self.earned_trophy_summary.get('gold', 0) + self.earned_trophy_summary.get('platinum', 0)

    def get_average_progress(self):
        avg = self.played_games.aggregate(avg_progress=Avg('progress'))['avg_progress']
        return avg if avg is not None else 0.0

    def link_discord(self, discord_id: int):
        if self.discord_id:
            raise ValueError("Discord already linked to this profile.")
        self.discord_id = discord_id
        self.discord_linked_at = timezone.now()
        self.is_discord_verified = True
        self.save(update_fields=['discord_id', 'discord_linked_at', 'is_discord_verified'])
    
    def unlink_discord(self):
        self.discord_id = None
        self.discord_linked_at = None
        self.is_discord_verified = False
        self.save(update_fields=['discord_id', 'discord_linked_at', 'is_discord_verified'])
    
    def set_history_public_flag(self, value: bool):
        self.psn_history_public = value
        self.save(update_fields=['psn_history_public'])

    def set_sync_status(self, value: str):
        if value in ['syncing', 'synced', 'error']:
            self.sync_status = value
            self.save(update_fields=['sync_status'])
            self.refresh_from_db(fields=['sync_status'])
    
    def add_to_sync_target(self, value: int):
        print('called...')
        if value:
            print(f"Adding {value} to target for {self.display_psn_username}")
            self.sync_progress_target = F('sync_progress_target') + value
            self.save(update_fields=['sync_progress_target'])
            self.refresh_from_db(fields=['sync_progress_target'])
            print(f"New target: {self.sync_progress_target}")
    
    def increment_sync_progress(self):
        self.sync_progress_value = F('sync_progress_value') + 1
        self.save(update_fields=['sync_progress_value'])
        self.refresh_from_db(fields=['sync_progress_value'])

    def reset_sync_progress(self):
        self.sync_progress_target = 0
        self.sync_progress_value = 0
        self.save(update_fields=['sync_progress_target', 'sync_progress_value'])
        self.refresh_from_db(fields=['sync_progress_target', 'sync_progress_value'])


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
    concept = models.ForeignKey('Concept', null=True, blank=True, on_delete=models.SET_NULL, related_name='games')
    region = models.JSONField(default=list, blank=True)
    title_ids = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    played_count = models.PositiveIntegerField(default=0, help_text="Denormalized count of profiles that have played the game (PP-specific).")
    is_regional = models.BooleanField(default=False)
    is_shovelware = models.BooleanField(default=False)
    is_obtainable= models.BooleanField(default=True)

    class Meta:
        indexes = [
            models.Index(fields=["np_communication_id", "title_name"], name="game_idx"),
            models.Index(fields=['played_count'], name='game_played_count_idx'),
            models.Index(fields=['title_name'], name='game_title_idx'),
            models.Index(fields=['title_platform'], name='game_platform_idx'),
            models.Index(fields=['created_at'], name='game_created_idx'),
            models.Index(fields=['is_obtainable', 'title_platform'], name='game_obtainable_platform_idx'),
        ]

    def add_concept(self, concept):
        if concept and not self.concept:
            self.concept = concept
            self.save(update_fields=['concept'])
    
    def add_region(self, region: str):
        if region:
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
            if region not in self.region:
                self.region.append(region)
                self.save(update_fields=['region'])
    
    def add_title_id(self, title_id: str):
        if title_id and title_id not in self.title_ids:
            self.title_ids.append(title_id)
            self.save(update_fields=['title_ids'])
    
    def update_is_shovelware(self, platinum_earn_rate: str):
        self.is_shovelware = float(platinum_earn_rate) >= SHOVELWARE_THRESHOLD
        self.save(update_fields=['is_shovelware'])

    def get_total_defined_trophies(self):
        return self.defined_trophies['bronze'] + self.defined_trophies['silver'] + self.defined_trophies['gold'] + self.defined_trophies['platinum']


    def __str__(self):
        return self.title_name

class Concept(models.Model):
    concept_id = models.CharField(max_length=50, unique=True)
    unified_title = models.CharField(max_length=255, blank=True)
    title_ids = models.JSONField(default=list, blank=True)
    publisher_name = models.CharField(max_length=255, blank=True)
    release_date = models.DateTimeField(null=True, blank=True)
    genres = models.JSONField(default=list, blank=True)
    subgenres = models.JSONField(default=list, blank=True)
    descriptions = models.JSONField(default=dict, blank=True)
    content_rating = models.JSONField(default=dict, blank=True)
    media = models.JSONField(default=dict, blank=True)
    bg_url = models.URLField(null=True, blank=True)
    concept_icon_url = models.URLField(null=True, blank=True)
    guide_slug = models.CharField(max_length=50, blank=True, null=True)

    class Meta:
        indexes = [
            models.Index(fields=['concept_id'], name='concept_id_idx'),
            models.Index(fields=['unified_title'], name='concept_title_idx'),
            models.Index(fields=['publisher_name'], name='content_publisher_idx'),
            models.Index(fields=['release_date'], name='concept_release_date_idx'),
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

    def update_media(self, media, icon_url, bg_url):
        if media:
            self.media = media
            self.concept_icon_url = icon_url
            self.bg_url = bg_url
            self.save(update_fields=['media', 'concept_icon_url', 'bg_url'])
    
    def update_release_date(self, date):
        if date:
            self.release_date = date
            self.save(update_fields=['release_date'])
    
    def has_user_earned_platinum(self, profile):
        platinum_trophies = Trophy.objects.filter(game__concept=self, trophy_type='platinum')
        return EarnedTrophy.objects.filter(profile=profile, trophy__in=platinum_trophies, earned=True).exists()

    def get_community_averages(self):
        ratings = self.user_ratings.all()
        if not ratings.exists():
            return None
        
        aggregates = ratings.aggregate(
            avg_difficulty=Avg('difficulty'),
            avg_fun=Avg('fun_ranking'),
            avg_rating=Avg('overall_rating'),
            count=Count('id')
        )

        hours_list = list(ratings.values_list('hours_to_platinum', flat=True))
        aggregates['avg_hours'] = calculate_trimmed_mean(hours_list, trim_percent=0.1) if hours_list else None

        return aggregates

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

class UserConceptRating(models.Model):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='concept_ratings')
    concept = models.ForeignKey(Concept, on_delete=models.CASCADE, related_name='user_ratings')
    difficulty = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(10)], help_text='Platinum Difficulty rating (1-10)')
    hours_to_platinum = models.PositiveIntegerField(help_text='Estimated hours to achieve platinum.')
    fun_ranking = models.IntegerField(validators=[MinValueValidator(1), MaxValueValidator(10)], help_text='Fun ranking for the platinum (1-10)')
    overall_rating = models.FloatField(validators=[MinValueValidator(0.5), MaxValueValidator(5.0)], help_text="Overall game rating (1-5 stars)")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['profile', 'concept']
        indexes = [
            models.Index(fields=['concept'], name='user_rating_concept_idx'),
            models.Index(fields=['profile', 'concept'], name='user_rating_unique_idx'),
        ]
        ordering = ['-updated_at']
    
    def __str__(self):
        return f"{self.profile.display_psn_username}'s rating for {self.concept.unified_title}"

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

class FeaturedGuide(models.Model):
    concept = models.ForeignKey('Concept', on_delete=models.CASCADE, related_name='featured_entries')
    start_date = models.DateField(null=True, blank=True, help_text='Start of feature period')
    end_date = models.DateField(null=True, blank=True, help_text='End of featured period')
    priority = models.PositiveIntegerField(default=1, help_text='Higher = preferred if multiple overlap')

    class Meta:
        ordering = ['-priority', '-start_date']
        indexes = [
            models.Index(fields=['start_date', 'end_date'], name='featured_date_idx'),
        ]
    
    def __str__(self):
        return f"Featured: {self.concept.unified_title} ({self.start_date} to {self.end_date})"

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
        recent_entry = self.earned_trophy_entries.filter(earned=True).order_by(F('earned_date_time').desc(nulls_last=True)).first()
        if recent_entry:
            return recent_entry.profile.psn_username
        return None
    
    def get_most_recent_earned_date(self):
        recent_entry = self.earned_trophy_entries.filter(earned=True).order_by(F('earned_date_time').desc(nulls_last=True)).first()
        if recent_entry:
            return recent_entry.earned_date_time
        return None

    def __str__(self):
        return f"{self.trophy_name} ({self.game.title_name})"

class TrophyGroup(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='trophy_groups')
    trophy_group_id = models.CharField(max_length=10)
    trophy_group_name = models.CharField(max_length=255, blank=True)
    trophy_group_detail = models.TextField(blank=True, null=True)
    trophy_group_icon_url = models.URLField(blank=True, null=True)
    defined_trophies = models.JSONField(default=dict, blank=True)

    class Meta:
        unique_together = ['game', 'trophy_group_id']
        indexes = [
            models.Index(fields=['trophy_group_id'], name='trophy_group_id_idx'),
        ]
        ordering = ['trophy_group_id']
    
    def __str__(self):
        return f"{self.trophy_group_name or self.trophy_group_id} ({self.game.title_name})"

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

class UserTrophySelection(models.Model):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='trophy_selections')
    earned_trophy = models.ForeignKey(EarnedTrophy, on_delete=models.CASCADE, related_name='selections')

    class Meta:
        unique_together = ['profile', 'earned_trophy']
    
    def save(self, *args, **kwargs):
        if self.profile.trophy_selections.count() >= 10 and not self.pk:
            raise ValueError("Maximum 10 selections allowed.")
        super().save(*args, **kwargs)

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

class Badge(models.Model):
    TIER_CHOICES = [
        (1, 'Tier 1 (Platinum, Modern)'),
        (2, 'Tier 2 (100%, Modern)'),
        (3, 'Tier 3 (Platinum, All Platforms)'),
        (4, 'Tier 4 (100%, All Platforms)'),
    ]
    BADGE_TYPES = [
        ('series', 'Series (Concept-based)'),
        ('misc', 'Miscellaneous'),
    ]

    name = models.CharField(max_length=255)
    series_slug = models.SlugField(max_length=100, blank=True, null=True, help_text='Groups tiers of the same series')
    description = models.TextField(blank=True)
    icon = models.ImageField(upload_to='badges/', blank=True, null=True)
    base_badge = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='derived_badges', help_text='Reference a base (Tier 1) badge to inherit its icon')
    display_title = models.CharField(max_length=100, blank=True)
    display_series = models.CharField(max_length=100, blank=True)
    discord_role_id = models.BigIntegerField(null=True, blank=True, help_text="Discord role ID to auto assign upon earning the badge (optional).")
    tier = models.IntegerField(choices=TIER_CHOICES, default=1)
    badge_type = models.CharField(max_length=10, choices=BADGE_TYPES, default='series')
    requires_all = models.BooleanField(default=True, help_text="If True, user must complete all qualifying Concepts. If false, only the min_required.")
    min_required = models.PositiveIntegerField(default=0, help_text="For large series (e.g. 10 out of 30)")
    requirements = models.JSONField(default=dict, blank=True, help_text="For misc badges")
    concepts = models.ManyToManyField(Concept, related_name='badges', blank=True, help_text="Required Concepts for series badges")
    created_at = models.DateTimeField(auto_now_add=True)
    earned_count = models.PositiveIntegerField(default=0, help_text="Count of users who have earned this badge tier")

    class Meta:
        ordering = ['tier', 'name']
        indexes = [
            models.Index(fields=['series_slug', 'tier'], name='badge_series_tier_idx'),
            models.Index(fields=['badge_type'], name='badge_type_idx'),
            models.Index(fields=['earned_count'], name='badge_earned_count_idx'),
        ]
    
    @property
    def effective_icon_url(self):
        if self.icon:
            return self.icon.url
        elif self.base_badge and self.base_badge.icon:
            return self.base_badge.icon.url
        return None

    @property
    def effective_display_series(self):
        if self.display_series:
            return self.display_series
        elif self.base_badge and self.base_badge.display_series:
            return self.base_badge.display_series
        return None
    
    @property
    def effective_display_title(self):
        if self.display_title:
            return self.display_title
        elif self.base_badge and self.base_badge.display_title:
            return self.base_badge.display_title
        return None
    
    @property
    def effective_description(self):
        if self.description:
            return self.description
        elif self.base_badge and self.base_badge.description:
            return self.base_badge.description
        return None
    
    def __str__(self):
        return f"{self.name} (Tier {self.tier})"
    
class UserBadge(models.Model):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='badges')
    badge = models.ForeignKey(Badge, on_delete=models.CASCADE, related_name='earned_by')
    earned_at = models.DateTimeField(auto_now_add=True)
    is_displayed = models.BooleanField(default=False, help_text="User's selected display badge.")

    class Meta:
        unique_together = ['profile', 'badge']
        indexes = [
            models.Index(fields=['profile', 'is_displayed'], name='userbadge_display_idx')
        ]

    def __str__(self):
        return f"{self.profile.psn_username} - {self.badge.name}"

class UserBadgeProgress(models.Model):
    profile = models.ForeignKey(Profile, on_delete=models.CASCADE, related_name='badge_progress')
    badge = models.ForeignKey(Badge, on_delete=models.CASCADE, related_name='progress_for')
    completed_concepts = models.PositiveIntegerField(default=0)
    required_concepts = models.PositiveIntegerField(default=0)
    progress_value = models.PositiveIntegerField(default=0)
    required_value = models.PositiveIntegerField(default=0)
    last_checked = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['profile', 'badge']
        indexes = [
            models.Index(fields=['profile', 'badge'], name='userbadgeprogress_idx'),
        ]

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
