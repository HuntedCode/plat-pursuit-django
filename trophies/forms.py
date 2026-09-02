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

    # Community flag filters (3-state: any / show-only / hide).
    # If both show_X and hide_X are submitted for the same flag, hide wins.
    show_delisted = forms.BooleanField(required=False, label='Delisted')
    show_unobtainable = forms.BooleanField(required=False, label='Unobtainable')
    show_online = forms.BooleanField(required=False, label='Online Trophies')
    show_buggy = forms.BooleanField(required=False, label='Buggy Trophies')
    hide_delisted = forms.BooleanField(required=False, label='Hide Delisted')
    hide_unobtainable = forms.BooleanField(required=False, label='Hide Unobtainable')
    hide_online = forms.BooleanField(required=False, label='Hide Online Trophies')
    hide_buggy = forms.BooleanField(required=False, label='Hide Buggy Trophies')

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

    # Contract filters (the game's home Job-Board contract): in a contract at all, and/or its contract
    # levels one of the selected jobs (a discipline picks all its jobs client-side -> a set of job slugs).
    in_contract = forms.BooleanField(required=False, label='In a contract')
    contract_jobs = forms.MultipleChoiceField(required=False, label='Contract jobs')

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
        from trophies.models import Genre, Theme, GameEngine, Job
        try:
            self.fields['contract_jobs'].choices = list(
                Job.objects.exclude(is_fallback=True)
                .values_list('slug', 'name').order_by('discipline', 'display_order', 'name')
            )
            self.fields['genres'].choices = list(
                Genre.objects.values_list('id', 'name').order_by('name')
            )
            self.fields['themes'].choices = list(
                Theme.objects.values_list('id', 'name').order_by('name')
            )
            self.fields['engine'].choices = [('', 'Any Engine')] + list(
                GameEngine.objects.values_list('id', 'name').order_by('name')
            )
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
    country = forms.ChoiceField(
        choices=[('', 'All Countries')], required=False, label='Country',
    )
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
        from trophies.models import Genre, Badge, Company
        from trophies.util_modules.countries import country_info
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

            # Country choices: only countries that actually have a company,
            # rendered as "🇺🇸 United States" via the ISO numeric→name util.
            # Unknown numeric codes (gaps in our ISO table) are dropped.
            #
            # ``.order_by()`` clears Company.Meta.ordering — without it Django
            # leaves ORDER BY name in the SQL alongside SELECT DISTINCT
            # country, which breaks DISTINCT on Postgres. Then we dedupe in
            # Python by display name as a final safety net (covers any
            # historical numeric codes that resolve to the same country).
            country_codes = (
                Company.objects.exclude(country__isnull=True)
                .order_by()
                .values_list('country', flat=True)
                .distinct()
            )
            seen_names: set[str] = set()
            country_options: list[tuple[str, str, str]] = []
            for code in country_codes:
                info = country_info(code)
                if not info:
                    continue
                flag, name = info
                if name in seen_names:
                    continue
                seen_names.add(name)
                country_options.append((str(code), f'{flag} {name}', name))
            country_options.sort(key=lambda opt: opt[2])
            self.fields['country'].choices = (
                [('', 'All Countries')]
                + [(value, label) for value, label, _name in country_options]
            )
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
    # Discovery sorts only -- the order matches `ProfilesListView.SORTS`, and the keys MUST stay in step
    # with it (the view resolves the raw param against that map, so a choice with no entry there silently
    # falls back to the default order). Ranking sorts moved to /leaderboards/ or were dropped when this
    # page stopped being a second scoreboard; see the view's docstring for which and why.
    sort = forms.ChoiceField(
        choices=[
            ('alpha', 'Alphabetical'),
            ('recently_active', 'Recently Active'),
            ('recently_joined', 'Recently Joined'),
            ('trophies', 'Total Trophies'),
            ('plats', 'Total Plats'),
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
    """Controls for a hunter's Games tab -- a RECORD of what they played, not a browse page.

    Reduced from ~30 fields and 17 sorts in 2026-08. What came off was the discovery apparatus this
    tab had inherited wholesale from Browse Games: genre/theme pickers, community rating / difficulty
    / fun ranges, time-to-beat ranges, shovelware filtering, and eight `show_*`/`hide_*` community
    flags that no template ever rendered. Those answer "what should I play next," which is a question
    about a catalogue, not about somebody's history.

    Four controls remain, and `status` is the one that did the work: `game_has_plat`, `plat_earned`,
    `is_100` and `completion_min/max` were five fields asking one question -- how far did they get --
    which the card already answers with its five-state completion bar. The options below ARE those
    states, so the filter and the card speak the same language.
    """

    DEFAULT_SORT = 'recent'

    SORT_CHOICES = [
        ('recent', 'Recently Played'),
        ('oldest', 'Oldest Played'),
        ('alpha', 'Alphabetical'),
        ('completion', 'Highest Completion'),
        ('completion_inv', 'Lowest Completion'),
        ('earned', 'Most Earned'),
    ]

    STATUS_CHOICES = [
        ('', 'All Games'),
        ('plat', 'Platinum Earned'),
        ('full', '100% Complete'),
        ('chase', 'Still to Plat'),
        ('unfinished', 'Unfinished'),
    ]

    query = forms.CharField(required=False, label='Search by name')
    platform = forms.MultipleChoiceField(choices=[('PS5', 'PS5'), ('PS4', 'PS4'), ('PS3', 'PS3'), ('PSVITA', 'PSVita'), ('PSVR', 'PSVR'), ('PSVR2', 'PSVR2')], required=False, label='Platforms')

    # CharField, NOT ChoiceField, so an unrecognised value can be coerced to the default instead of
    # failing validation. A ChoiceField rejects anything outside `choices` in `validate()`, which runs
    # BEFORE `clean_<field>` -- so the coercion below would never get a chance to run, the whole form
    # would be invalid, and the view answers an invalid form with an empty game list. Eleven sorts were
    # removed in the 2026-08 reduction, so a bookmarked `?sort=rating` would have rendered an EMPTY
    # Games tab, silently, for exactly the people who used the tab enough to bookmark a sort.
    # The options are rendered from SORT_CHOICES / STATUS_CHOICES via context, as the Badges and
    # Ratings tabs already do with their own `sort_options`.
    status = forms.CharField(required=False, label='Status')
    sort = forms.CharField(required=False, label='Sort By')

    def clean_sort(self):
        return self._coerce(self.cleaned_data.get('sort'), self.SORT_CHOICES, self.DEFAULT_SORT)

    def clean_status(self):
        return self._coerce(self.cleaned_data.get('status'), self.STATUS_CHOICES, '')

    @staticmethod
    def _coerce(value, choices, default):
        return value if value in {key for key, _ in choices} else default

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
    """The ONE server-side gate on a rating. Both the API endpoint and any future writer go through it, so
    a field added to `fields` becomes required everywhere at once."""

    class Meta:
        model = UserConceptRating
        fields = ['recommendation', 'difficulty', 'grindiness', 'hours_to_platinum', 'fun_ranking',
                  'overall_rating', 'blurb']
        widgets = {
            'recommendation': forms.RadioSelect,
            'difficulty': forms.NumberInput(attrs={'type': 'range', 'min': 1, 'max': 10, 'class': 'range range-primary'}),
            'grindiness': forms.NumberInput(attrs={'type': 'range', 'min': 1, 'max': 10, 'class': 'range range-success'}),
            'hours_to_platinum': forms.NumberInput(attrs={'type': 'number', 'min': 1, 'class': 'input'}),
            'fun_ranking': forms.NumberInput(attrs={'type': 'range', 'min': 1, 'max': 10, 'class': 'range range-secondary'}),
            'overall_rating': forms.NumberInput(attrs={'type': 'range', 'min': 0.5, 'max': 5.0, 'step': 0.5, 'class': 'range range-accent'}),
            'blurb': forms.Textarea(attrs={'maxlength': 140, 'rows': 2, 'class': 'textarea'}),
        }
        labels = {
            'recommendation': 'Would you recommend it?',
            'difficulty': 'Platinum Difficulty',
            'grindiness': 'Platinum Grindiness',
            'hours_to_platinum': 'Hours To Platinum',
            'fun_ranking': 'Platinum "Fun" Ranking',
            'overall_rating': 'Overall Game Rating',
            'blurb': 'Quick take (optional)',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The model is deliberately permissive (`blank=True`) so pre-existing rows stay valid and
        # `recommendation=''` can mean "the wizard still owes me this question". A ModelForm inherits that
        # permissiveness, so without this line the field would be optional on every write path and the
        # whole point -- every rating from here on carries one -- would quietly not happen.
        self.fields['recommendation'].required = True
        # `blank=True` also makes Django prepend an empty choice, which under RadioSelect renders as a
        # blank fifth radio. Reassign to the model's own list so the control offers exactly four.
        self.fields['recommendation'].choices = UserConceptRating.RECOMMENDATIONS

    def clean_hours_to_platinum(self):
        value = self.cleaned_data.get('hours_to_platinum')
        if not value or value <= 0:
            raise forms.ValidationError('Hours to platinum must be greater than zero.')
        return value

    def clean_blurb(self):
        """Optional. Sanitize (XSS strip) + reject banned words; enforce the 140-char cap. Reuses the shared
        comment moderation so the banned-word blocklist is one list across all UGC."""
        from trophies.services.comment_service import CommentService

        value = (self.cleaned_data.get('blurb') or '').strip()
        if not value:
            return ''   # blank is fine -- the blurb never gates a rating
        value = CommentService.sanitize_text(value)
        if len(value) > 140:
            raise forms.ValidationError('Keep your quick take to 140 characters or fewer.')
        has_banned, _word = CommentService.check_banned_words(value)
        if has_banned:
            raise forms.ValidationError('Please remove inappropriate language from your quick take.')
        return value


class BadgeSearchForm(forms.Form):
    """Filter form for the Browse Badges list (grouping-badge system). Only carries the fields the rebuilt
    toolbar renders: the name search + the badge-type chip choices. Sort + personal state are handled by the
    view directly (series_sorts / gallery_sorts + the binary-hold `state` chips), not by this form."""
    series_slug = forms.CharField(required=False, label='Search by Series')
    badge_type = forms.ChoiceField(
        required=False,
        label='Badge Type',  # choices sourced from the model in __init__
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from trophies.models import BadgeSeries
        self.fields['badge_type'].choices = [('', 'All Types')] + list(BadgeSeries.BADGE_TYPES)


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
            ('earned_count', 'Most Earned'),
            ('earned_count_inv', 'Least Earned'),
            ('type', 'Trophy Type'),
        ],
        required=False,
        label='Sort By',
    )
    trophy_type = forms.MultipleChoiceField(
        choices=[
            ('bronze', 'Bronze'),
            ('silver', 'Silver'),
            ('gold', 'Gold'),
            ('platinum', 'Platinum'),
        ],
        required=False,
        label='Trophy Type',
    )
    rarity_bracket = forms.MultipleChoiceField(
        choices=[
            ('ultra_rare', 'Ultra Rare'),
            ('very_rare', 'Very Rare'),
            ('rare', 'Rare'),
            ('common', 'Common'),
        ],
        required=False,
        label='Rarity',
    )
    dlc_filter = forms.ChoiceField(
        choices=[
            ('', 'All Trophies'),
            ('base', 'Base Game Only'),
            ('dlc', 'DLC Only'),
        ],
        required=False,
        label='DLC Filter',
    )


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


class BadgeSeriesCreationForm(forms.Form):
    """Staff tool: author a whole badge series and its editions in one submit.

    Replaces the pre-2026-08 form that created four legacy tier `Badge` rows. The shape of the job
    changed with the model: a series is now one `BadgeSeries` plus one `GroupBadge` per platform edition,
    so this form's real work is the `editions` multi-select.

    Django admin can do all of this, which is why the old page was deleted in cutover 5b -- but "possible
    in admin" turned out not to mean "usable": authoring one series there is a seven-page-load click-path
    with three raw-ID popup lookups. This is one form.

    Stages are deliberately NOT here. They are the bulk of authoring, they need the concept autocomplete
    and the bundle-overlap validation `StageAdminForm` already implements, and duplicating that badly
    would be worse than the extra trip to admin.
    """

    name = forms.CharField(
        max_length=255, required=True, label='Series Name',
        widget=forms.TextInput(attrs={'class': 'input input-bordered w-full',
                                      'placeholder': 'Soulsborne'}),
    )
    series_slug = forms.SlugField(
        max_length=100, required=False, label='Series Slug',
        help_text='Left blank, this is generated from the name. Stages join to a series by this string, '
                  'so it is the one field worth getting right first.',
        widget=forms.TextInput(attrs={'class': 'input input-bordered w-full',
                                      'placeholder': 'soulsborne'}),
    )
    badge_type = forms.ChoiceField(
        required=True, label='Badge Type',  # choices sourced from the model in __init__
        widget=forms.Select(attrs={'class': 'select select-bordered w-full'}),
    )
    completion_policy = forms.ChoiceField(
        required=True, label='Completion Policy',  # choices sourced from the model in __init__
        widget=forms.Select(attrs={'class': 'select select-bordered w-full'}),
    )
    min_required = forms.IntegerField(
        required=False, min_value=0, initial=0, label='Stages Required',
        help_text='Megamix only: how many gating stages earn the badge.',
        widget=forms.NumberInput(attrs={'class': 'input input-bordered w-full'}),
    )
    description = forms.CharField(
        required=False, label='Description',
        widget=forms.Textarea(attrs={'class': 'textarea textarea-bordered w-full', 'rows': 3}),
    )
    display_series = forms.CharField(
        max_length=100, required=False, label='Display Series',
        help_text='Overrides the name where the series is labelled on a medallion.',
        widget=forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
    )
    submitted_by = forms.CharField(
        max_length=100, required=False, label='Submitted By (PSN Username)',
        widget=forms.TextInput(attrs={'class': 'input input-bordered w-full'}),
    )
    editions = forms.ModelMultipleChoiceField(
        queryset=None,  # set in __init__ so the form picks up group changes without a reload
        required=True, label='Editions',
        widget=forms.CheckboxSelectMultiple,
        help_text='One GroupBadge per checked edition. A series with no editions is unearnable.',
    )
    start_live = forms.BooleanField(
        required=False, initial=False, label='Release immediately',
        help_text='Off by default: a badge is normally authored, given stages, then released.',
        widget=forms.CheckboxInput(attrs={'class': 'toggle toggle-primary'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from trophies.models import BadgeSeries, PlatformGroup

        self.fields['badge_type'].choices = list(BadgeSeries.BADGE_TYPES)
        self.fields['completion_policy'].choices = list(BadgeSeries.COMPLETION_POLICIES)

        active = PlatformGroup.objects.filter(is_active=True).order_by('sort_order', 'name')
        self.fields['editions'].queryset = active
        # Pre-check everything: the common case is a series that ships in every edition it can, and an
        # unchecked box is a badge nobody can earn.
        self.fields['editions'].initial = list(active.values_list('pk', flat=True))

    def clean_series_slug(self):
        """Canonicalize the slug, fill it from the name if blank, and reject one already taken.

        ALWAYS re-slugifies, even a hand-entered value. Two reasons, both of which produce a slug that
        looks right and silently fails to match:

        - `SlugField`'s validator is `^[-a-zA-Z0-9_]+$`, so UPPERCASE passes. `Elden-Ring` and
          `elden-ring` would be two distinct series, and the uniqueness check below is case-sensitive so
          it would not even notice.
        - The page's JS mirrors this into the box as you type, and no client-side slugify matches
          Django's exactly (it NFKD-normalizes, so `Pokemon` survives an accented source; a naive regex
          drops the character entirely). Whatever arrives, this is the value that gets stored.

        That matters more than tidiness: `Stage.series_slug` joins to `BadgeSeries.series_slug` by STRING,
        and stages are authored separately. A slug that differs by one character from what the author
        typed on the stages orphans them silently -- which is the very thing the orphan-slug panel on
        this page exists to surface.
        """
        from django.utils.text import slugify
        from trophies.models import BadgeSeries

        raw = (self.cleaned_data.get('series_slug') or '').strip()
        if not raw:
            # `name` is declared before this field, so it has already been cleaned.
            raw = self.cleaned_data.get('name', '')
        slug = slugify(raw)[:100]
        if not slug:
            raise forms.ValidationError('Could not derive a slug from that name; enter one directly.')
        if BadgeSeries.objects.filter(series_slug=slug).exists():
            raise forms.ValidationError(f'A series already uses the slug "{slug}".')
        return slug

    def clean_submitted_by(self):
        """Resolve the PSN username to a Profile. A typo here silently drops the credit otherwise."""
        from trophies.models import Profile

        username = (self.cleaned_data.get('submitted_by') or '').strip()
        if not username:
            return None
        profile = Profile.objects.filter(psn_username__iexact=username).first()
        if not profile:
            raise forms.ValidationError(f'No profile found for "{username}".')
        return profile

    def clean(self):
        """The `min_count` / `min_required` pairing, which the model does not enforce.

        A megamix series with `min_required=0` is earned by clearing zero stages -- it would be granted to
        everyone on the next evaluation. Worth catching at the one place a human types it.
        """
        cleaned = super().clean()
        policy = cleaned.get('completion_policy')
        minimum = cleaned.get('min_required') or 0

        if policy == 'min_count' and minimum < 1:
            self.add_error('min_required', 'A megamix series needs at least one required stage.')
        elif policy != 'min_count':
            cleaned['min_required'] = 0     # meaningless outside megamix; do not store a stray number
        return cleaned
