import logging
from django import forms
from django.db.models import Q
from trophies.models import Profile, UserConceptRating, Concept, ProfileGame

logger = logging.getLogger('psn_api')

class GameSearchForm(forms.Form):
    query = forms.CharField(required=False, label='Search by name')
    platform = forms.MultipleChoiceField(choices=[('PS5', 'PS5'), ('PS4', 'PS4'), ('PS3', 'PS3'), ('PSVITA', 'PSVita'), ('PSVR', 'PSVR'), ('PSVR2', 'PSVR2')], required=False, initial=['PS5', 'PS4'], label='Platforms')
    regions = forms.MultipleChoiceField(choices=[('global', 'Global'), ('NA', 'NA'), ('EU', 'EU'), ('JP', 'JP'), ('AS', 'AS'), ('KR', 'KR'), ('CN', 'CN')], required=False, label='Regions')
    letter = forms.ChoiceField(
        choices=[('', 'All'), ('0-9', '0-9')] + [(letter, letter) for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'],
        required=False,
        label=''
    )
    show_only_platinum = forms.BooleanField(required=False, label='Show only games with platinum')
    filter_shovelware = forms.BooleanField(required=False, label='Filter out shovelware')
    in_badge = forms.BooleanField(required=False, label='In a badge series')
    badge_series = forms.ChoiceField(choices=[('', 'Any Badge')], required=False, label='Badge Series')

    # Community flag filters
    show_delisted = forms.BooleanField(required=False, label='Delisted')
    show_unobtainable = forms.BooleanField(required=False, label='Unobtainable')
    show_online = forms.BooleanField(required=False, label='Online Trophies')
    show_buggy = forms.BooleanField(required=False, label='Buggy Trophies')

    # Community rating filters (dual-range sliders)
    rating_min = forms.FloatField(required=False, min_value=0, max_value=5)
    rating_max = forms.FloatField(required=False, min_value=0, max_value=5)
    difficulty_min = forms.IntegerField(required=False, min_value=1, max_value=10)
    difficulty_max = forms.IntegerField(required=False, min_value=1, max_value=10)
    fun_min = forms.IntegerField(required=False, min_value=1, max_value=10)
    fun_max = forms.IntegerField(required=False, min_value=1, max_value=10)

    # Time-to-beat filters (dual-range sliders, in hours)
    igdb_time_min = forms.IntegerField(required=False, min_value=0, max_value=1000)
    igdb_time_max = forms.IntegerField(required=False, min_value=0, max_value=1000)
    community_time_min = forms.IntegerField(required=False, min_value=0, max_value=1000)
    community_time_max = forms.IntegerField(required=False, min_value=0, max_value=1000)

    # Genre / Theme / Engine filters
    genres = forms.MultipleChoiceField(required=False, label='Genres')
    themes = forms.MultipleChoiceField(required=False, label='Themes')
    engine = forms.ChoiceField(choices=[('', 'Any Engine')], required=False, label='Game Engine')

    SORT_CHOICES = [
        ('alpha', 'Alphabetical'),
        ('played', 'Most Played'),
        ('played_inv', 'Least Played'),
        ('trending', 'Trending'),
        ('plat_earned', 'Most Platinums Earned'),
        ('plat_earned_inv', 'Least Platinums Earned'),
        ('plat_rate', 'Highest Plat Earn Rate'),
        ('plat_rate_inv', 'Lowest Plat Earn Rate'),
        ('trophy_count', 'Most Trophies'),
        ('trophy_count_inv', 'Fewest Trophies'),
        ('rating', 'Highest Rated'),
        ('rating_inv', 'Lowest Rated'),
        ('difficulty', 'Hardest'),
        ('difficulty_inv', 'Easiest'),
        ('fun', 'Most Fun'),
        ('fun_inv', 'Least Fun'),
        ('time_to_beat', 'Shortest Time-to-Beat'),
        ('time_to_beat_inv', 'Longest Time-to-Beat'),
        ('release_date', 'Newest Release'),
        ('release_date_inv', 'Oldest Release'),
        ('newest', 'Recently Added'),
        ('oldest', 'First Added'),
    ]

    # Grouped choices for template <optgroup> rendering
    SORT_GROUPS = [
        ('Popularity', [
            ('alpha', 'Alphabetical'),
            ('played', 'Most Played'),
            ('played_inv', 'Least Played'),
            ('trending', 'Trending'),
        ]),
        ('Trophies', [
            ('plat_earned', 'Most Platinums Earned'),
            ('plat_earned_inv', 'Least Platinums Earned'),
            ('plat_rate', 'Highest Plat Earn Rate'),
            ('plat_rate_inv', 'Lowest Plat Earn Rate'),
            ('trophy_count', 'Most Trophies'),
            ('trophy_count_inv', 'Fewest Trophies'),
        ]),
        ('Ratings', [
            ('rating', 'Highest Rated'),
            ('rating_inv', 'Lowest Rated'),
            ('difficulty', 'Hardest'),
            ('difficulty_inv', 'Easiest'),
            ('fun', 'Most Fun'),
            ('fun_inv', 'Least Fun'),
        ]),
        ('Time', [
            ('time_to_beat', 'Shortest Time-to-Beat'),
            ('time_to_beat_inv', 'Longest Time-to-Beat'),
            ('release_date', 'Newest Release'),
            ('release_date_inv', 'Oldest Release'),
            ('newest', 'Recently Added'),
            ('oldest', 'First Added'),
        ]),
    ]

    sort = forms.ChoiceField(
        choices=SORT_CHOICES,
        required=False,
        label='Sort By'
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from trophies.models import Genre, Theme, GameEngine, Badge
        try:
            self.fields['genres'].choices = list(
                Genre.objects.values_list('id', 'name').order_by('name')
            )
            self.fields['themes'].choices = list(
                Theme.objects.values_list('id', 'name').order_by('name')
            )
            self.fields['engine'].choices = [('', 'Any Engine')] + list(
                GameEngine.objects.values_list('id', 'name').order_by('name')
            )
            badge_qs = Badge.objects.filter(
                is_live=True, tier=1, series_slug__isnull=False,
            ).exclude(series_slug='').order_by('display_series', 'name')
            self.fields['badge_series'].choices = [('', 'Any Badge')] + [
                (b.series_slug, b.display_series or b.name)
                for b in badge_qs
            ]
        except Exception:
            pass

class CompanySearchForm(forms.Form):
    query = forms.CharField(required=False, label='Search by name')
    role = forms.MultipleChoiceField(
        choices=[
            ('developer', 'Developer'),
            ('publisher', 'Publisher'),
            ('porting', 'Porting'),
            ('supporting', 'Supporting'),
        ],
        required=False, label='Roles',
    )
    country = forms.CharField(required=False, label='Country')
    platform = forms.MultipleChoiceField(
        choices=[('PS5', 'PS5'), ('PS4', 'PS4'), ('PS3', 'PS3'), ('PSVITA', 'PSVita'), ('PSVR', 'PSVR'), ('PSVR2', 'PSVR2')],
        required=False, label='Platforms',
    )
    genres = forms.MultipleChoiceField(required=False, label='Genres')
    badge_series = forms.ChoiceField(choices=[('', 'Any Badge')], required=False, label='Badge Series')
    sort = forms.ChoiceField(
        choices=[
            ('alpha', 'Alphabetical'),
            ('games', 'Most Games'),
            ('games_inv', 'Fewest Games'),
            ('avg_rating', 'Highest Avg Rating'),
            ('total_players', 'Most Popular'),
            ('plats_earned', 'Most Platinums Earned'),
        ],
        required=False,
        label='Sort By',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from trophies.models import Genre, Badge
        try:
            self.fields['genres'].choices = list(
                Genre.objects.values_list('id', 'name').order_by('name')
            )
            badge_qs = Badge.objects.filter(
                is_live=True, tier=1, series_slug__isnull=False,
            ).exclude(series_slug='').order_by('display_series', 'name')
            self.fields['badge_series'].choices = [('', 'Any Badge')] + [
                (b.series_slug, b.display_series or b.name)
                for b in badge_qs
            ]
        except Exception:
            pass


class TrophySearchForm(forms.Form):
    query = forms.CharField(required=False, label='Search by name')
    platform = forms.MultipleChoiceField(choices=[('PS5', 'PS5'), ('PS4', 'PS4'), ('PS3', 'PS3'), ('PSVITA', 'PSVita'), ('PSVR', 'PSVR'), ('PSVR2', 'PSVR2')], required=False, label='Platforms')
    type = forms.MultipleChoiceField(choices=[('bronze', 'Bronze'), ('silver', 'Silver'), ('gold', 'Gold'), ('platinum', 'Platinum')], required=False, label='Types')
    region = forms.MultipleChoiceField(choices=[('global', 'Global'), ('NA', 'NA'), ('EU', 'EU'), ('JP', 'JP'), ('AS', 'AS'), ('KR', 'KR'), ('CN', 'CN')], required=False, label='Regions')
    psn_rarity = forms.MultipleChoiceField(choices=[('0', 'Ultra Rare'), ('1', 'Very Rare'), ('2', 'Rare'), ('3', 'Common')], required=False, label='PSN Rarity')
    show_only_platinum = forms.BooleanField(required=False, label='Show only games with platinum')
    filter_shovelware = forms.BooleanField(required=False, label='Filter out shovelware')
    sort = forms.ChoiceField(
        choices=[
            ('alpha', 'Alphabetical'),
            ('earned', 'Most Earned'),
            ('earned_inv', 'Least Earned'),
            ('rate', 'Highest Earn Rate'),
            ('rate_inv', 'Lowest Earn Rate'),
            ('psn_rate', 'Highest Earn Rate (PSN)'),
            ('psn_rate_inv', 'Lowest Earn Rate (PSN)'),
        ],
        required=False,
        label='Sort By'
    )

class ProfileSearchForm(forms.Form):
    query = forms.CharField(required=False, label='Search by name')
    country = forms.ChoiceField(choices=[('', 'All Countries')], required=False, label='Country')
    sort = forms.ChoiceField(
        choices=[
            ('alpha', 'Alphabetical'),
            ('trophies', 'Total Trophies'),
            ('plats', 'Total Plats'),
            ('games', 'Most Games Played'),
            ('completes', 'Most 100% Completions'),
            ('avg_progress', 'Highest Avg. Progress'),
            ('recently_active', 'Recently Active'),
            ('badges_earned', 'Most Badges Earned'),
            ('badge_xp', 'Highest Badge XP'),
            ('rarest_avg_plat', 'Rarest Avg Platinum'),
            ('recently_joined', 'Recently Joined'),
        ],
        required=False,
        label='Sort By',
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            countries = Profile.objects.exclude(country__isnull=True).exclude(country='').values_list('country', 'country_code').distinct().order_by('country')
            self.fields['country'].choices = [('', 'All Countries')] + [(code, country) for country, code in countries]
        except Exception as e:
            logger.error(f"Error populating country choices: {str(e)}")
            self.fields['country'].choices = [('', 'All Countries')]

class ProfileGamesForm(forms.Form):
    query = forms.CharField(required=False, label='Search by name')
    platform = forms.MultipleChoiceField(choices=[('PS5', 'PS5'), ('PS4', 'PS4'), ('PS3', 'PS3'), ('PSVITA', 'PSVita'), ('PSVR', 'PSVR'), ('PSVR2', 'PSVR2')], required=False, label='Platforms')
    plat_status = forms.ChoiceField(
        choices=[
            ('all', 'All Games'),
            ('plats', 'Platinum Earned'),
            ('no_plats', 'Platinum Not Earned'),
            ('100s', '100% Complete'),
            ('no_100s', 'Not 100%'),
            ('plats_100s', 'Platinum Earned + 100%'),
            ('no_plats_100s', 'No Platinum, Not 100%'),
            ('plats_no_100s', 'Platinum Earned, Not 100%'),
        ],
        required=False,
        label='Filter',
    )
    sort = forms.ChoiceField(
        choices=[
            ('recent', 'Recently Played'),
            ('oldest', 'Oldest Played'),
            ('alpha', 'Alphabetical'),
            ('completion', 'Highest Completion'),
            ('completion_inv', 'Lowest Completion'),
            ('trophies', 'Most Trophies'),
            ('earned', 'Most Earned'),
            ('unearned', 'Most Unearned'),
            ('rating', 'Highest Rated'),
            ('rating_inv', 'Lowest Rated'),
            ('time_to_beat', 'Shortest Time-to-Beat'),
            ('time_to_beat_inv', 'Longest Time-to-Beat'),
            ('plat_rarest', 'Rarest Platinum'),
            ('plat_common', 'Most Common Platinum'),
            ('trophy_count', 'Most Trophies (Defined)'),
            ('trophy_count_inv', 'Fewest Trophies (Defined)'),
        ],
        required=False,
        label='Sort By',
    )

    # New filter fields
    genres = forms.MultipleChoiceField(required=False, label='Genres')
    themes = forms.MultipleChoiceField(required=False, label='Themes')
    completion_min = forms.IntegerField(required=False, min_value=0, max_value=100)
    completion_max = forms.IntegerField(required=False, min_value=0, max_value=100)
    rating_min = forms.FloatField(required=False, min_value=0, max_value=5)
    rating_max = forms.FloatField(required=False, min_value=0, max_value=5)
    difficulty_min = forms.IntegerField(required=False, min_value=1, max_value=10)
    difficulty_max = forms.IntegerField(required=False, min_value=1, max_value=10)
    fun_min = forms.IntegerField(required=False, min_value=1, max_value=10)
    fun_max = forms.IntegerField(required=False, min_value=1, max_value=10)
    igdb_time_min = forms.IntegerField(required=False, min_value=0, max_value=1000)
    igdb_time_max = forms.IntegerField(required=False, min_value=0, max_value=1000)
    show_delisted = forms.BooleanField(required=False, label='Delisted')
    show_unobtainable = forms.BooleanField(required=False, label='Unobtainable')
    show_online = forms.BooleanField(required=False, label='Online Trophies')
    show_buggy = forms.BooleanField(required=False, label='Buggy Trophies')
    filter_shovelware = forms.BooleanField(required=False, label='Hide Shovelware')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from trophies.models import Genre, Theme
        try:
            self.fields['genres'].choices = list(
                Genre.objects.values_list('id', 'name').order_by('name')
            )
            self.fields['themes'].choices = list(
                Theme.objects.values_list('id', 'name').order_by('name')
            )
        except Exception:
            pass

class ProfileTrophiesForm(forms.Form):
    query = forms.CharField(required=False, label='Search by name')
    platform = forms.MultipleChoiceField(choices=[('PS5', 'PS5'), ('PS4', 'PS4'), ('PS3', 'PS3'), ('PSVITA', 'PSVita'), ('PSVR', 'PSVR'), ('PSVR2', 'PSVR2')], required=False, label='Platforms')
    type = forms.ChoiceField(choices=[('', 'All'), ('bronze', 'Bronze'), ('silver', 'Silver'), ('gold', 'Gold'), ('platinum', 'Platinum')], required=False, label='Type')
    sort = forms.ChoiceField(
        choices=[
            ('recent', 'Recently Earned'),
            ('oldest', 'Oldest Earned'),
            ('alpha', 'Alphabetical'),
            ('rarest_psn', 'Rarest (PSN)'),
            ('common_psn', 'Most Common (PSN)'),
            ('rarest_pp', 'Rarest (PP)'),
            ('common_pp', 'Most Common (PP)'),
            ('type', 'Trophy Type'),
        ],
        required=False,
        label='Sort By',
    )
    rarity_min = forms.FloatField(required=False, min_value=0, max_value=100)
    rarity_max = forms.FloatField(required=False, min_value=0, max_value=100)

class ProfileBadgesForm(forms.Form):
    sort = forms.ChoiceField(
        choices=[
            ('series', 'Series'),
            ('name', 'Alphabetical'),
            ('tier', 'Tier Ascending'),
            ('tier_desc', 'Tier Descending'),
            ('stages', 'Most Stages'),
            ('stages_inv', 'Fewest Stages'),
            ('xp', 'Most XP'),
            ('xp_inv', 'Least XP'),
            ('recent', 'Recently Earned'),
        ],
        required=False,
        label='Sort By',
    )
    badge_type = forms.MultipleChoiceField(
        choices=[
            ('series', 'Series'),
            ('collection', 'Collection'),
            ('developer', 'Developer'),
            ('user', 'User'),
            ('genre', 'Genre'),
            ('megamix', 'Megamix'),
        ],
        required=False,
        label='Badge Type',
    )
    tier = forms.MultipleChoiceField(
        choices=[
            ('1', 'Bronze'),
            ('2', 'Silver'),
            ('3', 'Gold'),
            ('4', 'Platinum'),
        ],
        required=False,
        label='Tier',
    )

class TrophyCaseForm(forms.Form):
    query = forms.CharField(required=False, label='Search by game name')
    sort = forms.ChoiceField(
        choices=[
            ('recent', 'Recently Earned'),
            ('oldest', 'Oldest Earned'),
            ('rarest_psn', 'Rarest (PSN)'),
            ('rarest_pp', 'Rarest (PP)'),
            ('alpha', 'Alphabetical'),
            ('rating', 'Highest Rated'),
            ('rating_inv', 'Lowest Rated'),
            ('played', 'Most Played'),
            ('played_inv', 'Least Played'),
            ('time_to_beat', 'Shortest Time-to-Beat'),
            ('time_to_beat_inv', 'Longest Time-to-Beat'),
        ],
        required=False,
        label='Sort By',
    )
    filter = forms.ChoiceField(
        choices=[('', 'All Platinums'), ('selected', 'Selected Only')],
        required=False,
        label='Filter',
    )
    platform = forms.MultipleChoiceField(
        choices=[('PS5', 'PS5'), ('PS4', 'PS4'), ('PS3', 'PS3'), ('PSVITA', 'PSVita'), ('PSVR', 'PSVR'), ('PSVR2', 'PSVR2')],
        required=False,
        label='Platforms',
    )
    genres = forms.MultipleChoiceField(required=False, label='Genres')
    themes = forms.MultipleChoiceField(required=False, label='Themes')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from trophies.models import Genre, Theme
        try:
            self.fields['genres'].choices = list(
                Genre.objects.values_list('id', 'name').order_by('name')
            )
            self.fields['themes'].choices = list(
                Theme.objects.values_list('id', 'name').order_by('name')
            )
        except Exception:
            pass


class UserConceptRatingForm(forms.ModelForm):
    class Meta:
        model = UserConceptRating
        fields = ['difficulty', 'grindiness', 'hours_to_platinum', 'fun_ranking', 'overall_rating']
        widgets = {
            'difficulty': forms.NumberInput(attrs={'type': 'range', 'min': 1, 'max': 10, 'class': 'range range-primary'}),
            'grindiness': forms.NumberInput(attrs={'type': 'range', 'min': 1, 'max': 10, 'class': 'range range-success'}),
            'hours_to_platinum': forms.NumberInput(attrs={'type': 'number', 'min': 1, 'class': 'input'}),
            'fun_ranking': forms.NumberInput(attrs={'type': 'range', 'min': 1, 'max': 10, 'class': 'range range-secondary'}),
            'overall_rating': forms.NumberInput(attrs={'type': 'range', 'min': 0.5, 'max': 5.0, 'step': 0.5, 'class': 'range range-accent'}),
        }
        labels = {
            'difficulty': 'Platinum Difficulty',
            'grindiness': 'Platinum Grindiness',
            'hours_to_platinum': 'Hours To Platinum',
            'fun_ranking': 'Platinum "Fun" Ranking',
            'overall_rating': 'Overall Game Rating',
        }

    def clean_hours_to_platinum(self):
        value = self.cleaned_data.get('hours_to_platinum')
        if not value or value <= 0:
            raise forms.ValidationError('Hours to platinum must be greater than zero.')
        return value


class BadgeSearchForm(forms.Form):
    series_slug = forms.CharField(required=False, label='Search by Series')
    sort = forms.ChoiceField(
        choices=[
            ('name', 'Alphabetical'),
            ('earned', 'Most Earned (Tier 1)'),
            ('earned_inv', 'Least Earned (Tier 1)'),
            ('my_tier', 'My Progress Ascending'),
            ('my_tier_desc', 'My Progress Descending'),
            ('stages', 'Most Stages'),
            ('stages_inv', 'Fewest Stages'),
            ('newest', 'Newest Added'),
            ('oldest_added', 'Oldest Added'),
            ('xp', 'Most XP'),
            ('xp_inv', 'Least XP'),
            ('closest', 'Closest to Next Tier'),
            ('games_owned', 'Most Games Owned'),
            ('games_owned_inv', 'Fewest Games Owned'),
            ('recently_progressed', 'Recently Progressed'),
        ],
        required=False,
        label='Sort By',
    )
    badge_type = forms.ChoiceField(
        choices=[
            ('', 'All Types'),
            ('series', 'Series'),
            ('collection', 'Collection'),
            ('developer', 'Developer'),
            ('user', 'User'),
            ('genre', 'Genre'),
            ('megamix', 'Megamix'),
        ],
        required=False,
        label='Badge Type',
    )
    completion_status = forms.ChoiceField(
        choices=[
            ('', 'All'),
            ('not_started', 'Not Started'),
            ('in_progress', 'In Progress'),
            ('completed', 'Completed'),
        ],
        required=False,
        label='Status',
    )

class GuideSearchForm(forms.Form):
    query = forms.CharField(required=False, label='Search by title')
    sort = forms.ChoiceField(
        choices=[
            ('title', 'Alphabetical'),
            ('release_asc', 'Release Date Ascending'),
            ('release_desc', 'Release Date Descending'),
        ],
        required=False,
        label='Sort By'
    )

class LinkPSNForm(forms.Form):
    psn_username = forms.CharField(
        max_length=16,
        validators=Profile._meta.get_field('psn_username').validators,
        help_text="Enter your exact PSN Online ID (3-16 characters, letters, numbers, hypens or underscores).",
        widget=forms.TextInput(attrs={'class': 'input w-full', 'placeholder': 'Your PSN Username'}),
    )

class GameDetailForm(forms.Form):
    earned = forms.ChoiceField(
        choices=[
            ('default', 'Show All'),
            ('unearned', 'Show Only Unearned'),
            ('earned', 'Show Only Earned'),
        ],
        required=False,
        label="Show only unearned trophies.",
    )
    sort = forms.ChoiceField(
        choices=[
            ('default', 'PSN Default'),
            ('earned_date', 'Date'),
            ('psn_rarity', 'PSN Rarity'),
            ('pp_rarity', 'PP Rarity'),
            ('alpha', 'Alphabetical'),
        ],
        required=False,
        label='Sort By',
    )

class PremiumSettingsForm(forms.ModelForm):
    """Premium-only settings: background and site theme."""
    # selected_background is handled by GameBackgroundPicker JS widget + hidden input
    selected_theme = forms.ChoiceField(
        choices=[],  # Populated in __init__
        label='Site Theme',
        required=False,
        widget=forms.Select(attrs={'class': 'select w-full', 'id': 'selected-theme-select'})
    )

    class Meta:
        model = Profile
        fields = ['selected_theme']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        from trophies.themes import THEME_CHOICES
        self.fields['selected_theme'].choices = THEME_CHOICES

        if self.instance and not self.instance.user_is_premium:
            for field in self.fields:
                self.fields[field].widget.attrs['disabled'] = 'disabled'
                self.fields[field].help_text = 'Premium feature.'

    def clean(self):
        cleaned_data = super().clean()
        if not self.instance.user_is_premium:
            for field in self.fields:
                cleaned_data[field] = self.initial.get(field)
        return cleaned_data

class ProfileSettingsForm(forms.ModelForm):
    hide_hiddens = forms.BooleanField(
        label='Hide Hidden/Deleted Games',
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'toggle toggle-primary'})
    )
    hide_zeros = forms.BooleanField(
        label='Hide Zero Progress Games',
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'toggle toggle-primary'})
    )

    class Meta:
        model = Profile
        fields = ['hide_hiddens', 'hide_zeros']

# Admin Forms

class BadgeCreationForm(forms.Form):
    name = forms.CharField(max_length=255, required=True, label="Name", widget=forms.TextInput(attrs={'class': 'input w-full'}))
    series_slug = forms.SlugField(max_length=100, required=False, label="Series Slug", widget=forms.TextInput(attrs={'class': 'input w-full'}))
    badge_type = forms.ChoiceField(choices=[('series', 'Series'), ('collection', 'Collection'), ('megamix', 'Megamix'), ('developer', 'Developer'), ('user', 'User'), ('genre', 'Genre')], required=True, label="Badge Type", widget=forms.Select(attrs={'class': 'select w-full'}))
    submitted_by = forms.CharField(max_length=100, required=False, label="Submitted By (PSN Username)", widget=forms.TextInput(attrs={'class': 'input w-full', 'placeholder': 'PSN username of submitter'}))

    def get_badge_data(self):
        if self.is_valid():
            return {
                'name': self.cleaned_data['name'],
                'series_slug': self.cleaned_data['series_slug'],
                'badge_type': self.cleaned_data['badge_type'],
                'submitted_by_username': self.cleaned_data.get('submitted_by', ''),
            }
        return {}