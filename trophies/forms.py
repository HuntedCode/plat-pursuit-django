import logging
from django import forms
from trophies.models import Profile, UserConceptRating, Badge

logger = logging.getLogger('psn_api')

class GameSearchForm(forms.Form):
    query = forms.CharField(required=False, label='Search by name')
    platform = forms.MultipleChoiceField(choices=[('PS5', 'PS5'), ('PS4', 'PS4'), ('PS3', 'PS3'), ('PSVITA', 'PSVita'), ('PSVR', 'PSVR')], required=False, label='Platforms')
    regions = forms.MultipleChoiceField(choices=[('global', 'Global'), ('NA', 'NA'), ('EU', 'EU'), ('JP', 'JP'), ('AS', 'AS')], required=False, label='Regions')
    letter = forms.ChoiceField(
        choices=[('', 'All'), ('0-9', '0-9')] + [(letter, letter) for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'],
        required=False,
        label=''
    )
    show_only_platinum = forms.BooleanField(required=False, label='Show only games with platinum')
    filter_shovelware = forms.BooleanField(required=False, label='Filter out shovelware')
    sort = forms.ChoiceField(
        choices=[
            ('alpha', 'Alphabetical'),
            ('played', 'Most Played'),
            ('played_inv', 'Least Played'),
            ('plat_earned', 'Most Platinums Earned'),
            ('plat_earned_inv', 'Least Platinums Earned'),
            ('plat_rate', 'Highest Plat Earn Rate'),
            ('plat_rate_inv', 'Lowest Plat Earn Rate'),
        ],
        required=False,
        label='Sort By'
    )

class TrophySearchForm(forms.Form):
    query = forms.CharField(required=False, label='Search by name')
    platform = forms.MultipleChoiceField(choices=[('PS5', 'PS5'), ('PS4', 'PS4'), ('PS3', 'PS3'), ('PSVITA', 'PSVita'), ('PSVR', 'PSVR')], required=False, label='Platforms')
    type = forms.MultipleChoiceField(choices=[('bronze', 'Bronze'), ('silver', 'Silver'), ('gold', 'Gold'), ('platinum', 'Platinum')], required=False, label='Types')
    region = forms.MultipleChoiceField(choices=[('global', 'Global'), ('NA', 'NA'), ('EU', 'EU'), ('JP', 'JP'), ('AS', 'AS')], required=False, label='Regions')
    psn_rarity = forms.MultipleChoiceField(choices=[('0', 'Ultra Rare'), ('1', 'Rare'), ('2', 'Uncommon'), ('3', 'Common')], required=False, label='PSN Rarity')
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
    filter_shovelware = forms.BooleanField(required=False, label='Filter out shovelware')
    sort = forms.ChoiceField(
        choices=[
            ('alpha', 'Alphabetical'),
            ('trophies', 'Total Trophies'),
            ('plats', 'Total Plats'),
            ('games', 'Most Games Played'),
            ('completes', 'Most 100% Completions'),
            ('avg_progress', 'Highest Avg. Progress'),
        ],
        required=False,
        label='Sort By'
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
    platform = forms.MultipleChoiceField(choices=[('PS5', 'PS5'), ('PS4', 'PS4'), ('PS3', 'PS3'), ('PSVITA', 'PSVita'), ('PSVR', 'PSVR')], required=False, label='Platforms')
    plat_status = forms.ChoiceField(
        choices=[
            ('all', 'Show All'),
            ('plats', 'Show Only Plats'),
            ('no_plats', 'Show Only Non Plats'),
            ('100s', 'Show Only 100%'),
            ('no_100s', 'Show Only Non 100%'),
            ('plats_100s', 'Show Only Plats & 100%'),
            ('no_plats_100s', 'Show Only Non Plats Nor 100%'),
            ('plats_no_100s', 'Show Only Plats & Non 100%'),
        ],
        required=False,
        label='Plat Status'
    )
    sort = forms.ChoiceField(
        choices=[
            ('recent', 'Recently Played'),
            ('oldest', 'Oldest Played'),
            ('alpha', 'Alphabetical'),
            ('completion', 'Highest Completion %'),
            ('completion_inv', 'Lowest Completion %'),
            ('trophies', 'Total Trophies'),
            ('earned', 'Earned Trophies'),
            ('unearned', 'Unearned Trophies'),
        ],
        required=False,
        label='Sort By'
    )

class ProfileTrophiesForm(forms.Form):
    query = forms.CharField(required=False, label='Search by name')
    platform = forms.MultipleChoiceField(choices=[('PS5', 'PS5'), ('PS4', 'PS4'), ('PS3', 'PS3'), ('PSVITA', 'PSVita'), ('PSVR', 'PSVR')], required=False, label='Platforms')
    type = forms.ChoiceField(choices=[('', 'All'),('bronze', 'Bronze'), ('silver', 'Silver'), ('gold', 'Gold'), ('platinum', 'Platinum')], required=False, label='Type')

class ProfileBadgesForm(forms.Form):
    sort = forms.ChoiceField(
        choices=[
            ('series', 'Series'),
            ('name', 'Alphabetical'),
            ('tier', 'Tier Ascending'),
            ('tier_desc', 'Tier Descending'),
        ],
        required=False,
        label='Sort By'
    )

class UserConceptRatingForm(forms.ModelForm):
    class Meta:
        model = UserConceptRating
        fields = ['difficulty', 'hours_to_platinum', 'fun_ranking', 'overall_rating']
        widgets = {
            'difficulty': forms.NumberInput(attrs={'type': 'range', 'min': 1, 'max': 10, 'class': 'range range-primary'}),
            'hours_to_platinum': forms.NumberInput(attrs={'type': 'number', 'min': 0, 'class': 'input'}),
            'fun_ranking': forms.NumberInput(attrs={'type': 'range', 'min': 1, 'max': 10, 'class': 'range range-secondary'}),
            'overall_rating': forms.NumberInput(attrs={'type': 'range', 'min': 0.5, 'max': 5.0, 'step': 0.5, 'class': 'range range-accent'}),
        }
        labels = {
            'difficulty': 'Platinum Difficulty',
            'hours_to_platinum': 'Hours To Platinum',
            'fun_ranking': 'Platinum "Fun" Ranking',
            'overall_rating': 'Overall Game Rating',
        }

class BadgeSearchForm(forms.Form):
    series_slug = forms.CharField(required=False, label='Search by Series')
    sort = forms.ChoiceField(
        choices=[
            ('series', 'Series'),
            ('name', 'Alphabetical'),
            ('tier', 'Tier Ascending'),
            ('tier_desc', 'Tier Descending'),
            ('earned', 'Most Earned (Tier 1)'),
            ('earned_inv', 'Least Earned (Tier 1)'),
        ],
        required=False,
        label='Sort By'
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
