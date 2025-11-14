from django import forms

class GameSearchForm(forms.Form):
    query = forms.CharField(required=False, label='Search by name')
    platform = forms.ChoiceField(choices=[('', 'All'), ('PS5', 'PS5'), ('PS4', 'PS4'), ('PS3', 'PS3'), ('PSVITA', 'PSVita'), ('PSVR', 'PSVR')], required=False)
    letter = forms.ChoiceField(
        choices=[('', 'All'), ('0-9', '0-9')] + [(letter, letter) for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'],
        required=False,
        label=''
    )
    show_legacy = forms.BooleanField(required=False, label='Display "Legacy" games')
    show_only_platinum = forms.BooleanField(required=False, label='Show only games with platinum')
    sort = forms.ChoiceField(
        choices=[
            ('alpha', 'Alphabetical'),
            ('played', 'Most Played'),
            ('played_inv', 'Least Played'),
            ('plat_earned', 'Most Platinums Earned'),
            ('plat_earned_inv', 'Least Platinums Earned'),
        ],
        required=False,
        label='Sort By'
    )