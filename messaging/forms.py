from django import forms

from .models import DigestSettings


class SettingsForm(forms.ModelForm):
    access_token = forms.CharField(
        required=False, widget=forms.PasswordInput(render_value=False, attrs={'autocomplete': 'off'}),
        help_text='Only needed if this college uses its own WhatsApp number. Leave blank to keep the saved one.')
    clear_attention = forms.BooleanField(required=False, label='The problem is fixed: let the digest run again')

    class Meta:
        model = DigestSettings
        fields = ['enabled', 'dry_run', 'send_time', 'default_language', 'primary_guardian_only', 'phone_number_id']
        widgets = {'send_time': forms.TimeInput(format='%H:%M', attrs={'type': 'time'})}

    def clean(self):
        data = super().clean()
        has_token = bool(data.get('access_token'))
        if data.get('phone_number_id') and not has_token:
            from .models import CollegeSecret
            if not CollegeSecret.objects.filter(name='whatsapp_access_token').exists():
                self.add_error('access_token', 'A phone number id needs its access token.')
        return data
