"""Passwords set through the HoD / profile forms must pass the configured validators."""
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django_tenants.utils import tenant_context
from PIL import Image

from colleges.tests.base import TwoCollegeTestCase


def png():
    buffer = io.BytesIO()
    Image.new('RGB', (4, 4)).save(buffer, 'PNG')
    return SimpleUploadedFile('p.png', buffer.getvalue(), content_type='image/png')


class PasswordPolicyTests(TwoCollegeTestCase):
    def form(self, password):
        from main_app.forms import StaffForm
        data = {'first_name': 'New', 'last_name': 'Teacher', 'email': 'new@a.in', 'gender': 'F',
                'address': 'somewhere', 'password': password}
        return StaffForm(data, {'profile_pic': png()})

    def test_weak_passwords_are_rejected(self):
        with tenant_context(self.college_a):
            for weak in ('1234', '12345678901', 'password123', 'short1'):
                form = self.form(weak)
                self.assertFalse(form.is_valid(), weak)
                self.assertIn('password', form.errors, weak)

    def test_a_strong_password_is_accepted(self):
        with tenant_context(self.college_a):
            form = self.form('correct-horse-battery-staple')
            form.is_valid()
            self.assertNotIn('password', form.errors)
