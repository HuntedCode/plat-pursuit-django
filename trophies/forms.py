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