from django import forms
from django.contrib.auth import password_validation

from bulk_import.parsing import normalize_phone
from main_app.models import CustomUser


class ManagementUserForm(forms.Form):
    first_name = forms.CharField(max_length=150)
    last_name = forms.CharField(max_length=150)
    email = forms.EmailField()
    phone = forms.CharField(required=False, help_text='10-digit Indian mobile number (optional)')
    password = forms.CharField(widget=forms.PasswordInput, required=False)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user  # the Management user being edited, or None when adding
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-control'
        if user is None:
            self.fields['password'].required = True
        else:
            self.fields['password'].help_text = 'Leave empty to keep the current password.'

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        taken = CustomUser.objects.filter(email__iexact=email)
        if self.user is not None:
            taken = taken.exclude(pk=self.user.pk)
        if taken.exists():
            raise forms.ValidationError('That e-mail address is already registered.')
        return email

    def clean_phone(self):
        value = (self.cleaned_data.get('phone') or '').strip()
        if not value:
            return ''
        phone = normalize_phone(value)
        if phone is None:
            raise forms.ValidationError('Enter a 10-digit Indian mobile number.')
        return phone

    def clean_password(self):
        password = self.cleaned_data.get('password')
        if password:
            password_validation.validate_password(password)
        return password
