from django import forms

class GameSearchForm(forms.Form):
    query = forms.CharField(required=False, label='Search by name')
    platform = forms.ChoiceField(choices=[('', 'All'), ('PS5', 'PS5'), ('PS4', 'PS4'), ('PS3', 'PS3'), ('PSVita', 'PSVita'), ('PSVR', 'PSVR')], required=False)