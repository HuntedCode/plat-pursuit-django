from django import forms

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