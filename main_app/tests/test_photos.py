"""Profile pictures are private: served only by an authenticated view, role- and college-checked, never from /media/."""
import importlib
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django_tenants.utils import tenant_context
from PIL import Image

from colleges.tests.base import HOST_A, HOST_B

from .helpers import STRONG, UploadCleanupMixin, picture
from .world import WorldTestCase


def png_bytes(size=(4, 4)):
    buffer = io.BytesIO()
    Image.new('RGB', size, 'blue').save(buffer, 'PNG')
    return buffer.getvalue()


class PhotoTestCase(UploadCleanupMixin, WorldTestCase):
    def give_picture(self, user, name='face.png', content=None):
        """Store a picture for `user` the way the edit pages do (storage name in profile_pic)."""
        from django.core.files.storage import default_storage

        from main_app.models import CustomUser
        with tenant_context(self.college_a):
            stored = default_storage.save(name, SimpleUploadedFile(name, content or png_bytes()))
            fresh = CustomUser.objects.get(pk=user.pk)
            fresh.profile_pic = stored
            fresh.save()
        return stored

    def photo(self, viewer, target):
        return self.as_user(viewer).get('/photo/%d/' % target.pk)


class WhoMaySeeWhatTests(PhotoTestCase):
    def setUp(self):
        super().setUp()
        for user in (self.admin, self.staff1, self.staff2, self.stud1, self.stud2, self.stud3):
            self.give_picture(user, name=user.email.split('@')[0] + '.png')

    def test_everyone_sees_their_own_picture(self):
        for user in (self.admin, self.staff1, self.stud1):
            response = self.photo(user, user)
            self.assertEqual(response.status_code, 200, user.email)
            self.assertEqual(response['Content-Type'], 'image/png')
            self.assertEqual(b''.join(response.streaming_content), png_bytes())

    def test_the_picture_is_not_cached_by_shared_caches_and_cannot_be_sniffed(self):
        response = self.photo(self.admin, self.stud1)
        self.assertEqual(response['Cache-Control'], 'private, max-age=300')
        self.assertEqual(response['X-Content-Type-Options'], 'nosniff')

    def test_the_college_admin_sees_everyone_in_the_college(self):
        for user in (self.staff1, self.staff2, self.stud1, self.stud3):
            self.assertEqual(self.photo(self.admin, user).status_code, 200, user.email)

    def test_a_teacher_sees_only_students_they_teach(self):
        self.assertEqual(self.photo(self.staff1, self.stud1).status_code, 200)  # in staff1's class
        self.assertEqual(self.photo(self.staff1, self.stud3).status_code, 404)  # another section, another teacher
        self.assertEqual(self.photo(self.staff1, self.staff2).status_code, 404)
        self.assertEqual(self.photo(self.staff1, self.admin).status_code, 404)

    def test_a_student_sees_nobody_elses_picture(self):
        for other in (self.stud2, self.stud3, self.staff1, self.admin):
            self.assertEqual(self.photo(self.stud1, other).status_code, 404, other.email)

    def test_a_visitor_who_is_not_logged_in_is_sent_to_the_login_page(self):
        response = Client(HTTP_HOST=HOST_A).get('/photo/%d/' % self.stud1.pk)
        self.assertEqual((response.status_code, response['Location']), (302, '/'))

    def test_a_teacher_loses_access_when_the_student_leaves_their_class(self):
        from academics.models import Enrolment
        with tenant_context(self.college_a):
            Enrolment.objects.filter(student=self.stud1.student, offering_id=self.sub1).update(status=Enrolment.WITHDRAWN)
        self.assertEqual(self.photo(self.staff1, self.stud1).status_code, 404)


class EdgeCaseTests(PhotoTestCase):
    def test_no_picture_or_unknown_user_is_404(self):
        self.assertEqual(self.photo(self.admin, self.stud1).status_code, 404)
        self.assertEqual(self.as_user(self.admin).get('/photo/999999/').status_code, 404)

    def test_a_file_that_is_not_a_safe_image_type_is_never_sent(self):
        self.give_picture(self.stud1, name='evil.html', content=b'<script>alert(1)</script>')
        self.assertEqual(self.photo(self.admin, self.stud1).status_code, 404)

    def test_a_missing_file_is_404_not_a_crash(self):
        from main_app.models import CustomUser
        with tenant_context(self.college_a):
            user = CustomUser.objects.get(pk=self.stud1.pk)
            user.profile_pic = 'never-uploaded.png'
            user.save()
        self.assertEqual(self.photo(self.admin, self.stud1).status_code, 404)

    def test_the_media_folder_is_not_served_on_any_college(self):
        stored = self.give_picture(self.stud1)
        for host in (HOST_A, HOST_B):
            for path in ('/media/college_a/' + stored, '/media/' + stored, '/media/college_b/' + stored):
                self.assertEqual(Client(HTTP_HOST=host).get(path).status_code, 404, (host, path))
        self.assertEqual(self.as_user(self.admin).get('/media/college_a/' + stored).status_code, 404)

    def test_the_url_configuration_never_serves_the_media_folder(self):
        """Django's static() helper adds the media route only when DEBUG is on, which the tests are not: so check the source."""
        from pathlib import Path
        urls = Path(__file__).resolve().parents[2] / 'college_management_system' / 'urls.py'
        self.assertNotIn('MEDIA', urls.read_text().replace('(see main_app/photos.py)', ''))

    def test_another_colleges_picture_cannot_be_reached_through_this_colleges_host(self):
        from django.core.files.storage import default_storage

        from main_app.models import CustomUser
        other = self.make_user(self.college_b, 'bpic@b.in', '3', 'Bee', 'Pic')
        with tenant_context(self.college_b):
            stored = default_storage.save('bee.png', SimpleUploadedFile('bee.png', png_bytes((9, 9))))
            fresh = CustomUser.objects.get(pk=other.pk)
            fresh.profile_pic = stored
            fresh.save()
        try:
            for pk in (other.pk, other.pk + 1000):  # that id on college A's host is a different person or nobody
                response = self.as_user(self.admin).get('/photo/%d/' % pk)
                if response.status_code == 200:
                    self.assertNotEqual(b''.join(response.streaming_content), png_bytes((9, 9)))
        finally:
            with tenant_context(self.college_b):
                default_storage.delete(stored)


class PagesUseThePhotoViewTests(PhotoTestCase):
    def test_new_pictures_are_stored_as_names_and_pages_link_to_the_photo_view(self):
        from main_app.models import CustomUser
        data = {'first_name': 'Pic', 'last_name': 'Pupil', 'email': 'pic@a.in', 'gender': 'M', 'password': STRONG, 'address': 'x',
                'phone': '', 'roll_number': 'P-1', 'date_of_birth': '2006-01-01', 'section': self.sec1, 'profile_pic': picture()}
        admin = self.as_user(self.admin)
        self.assertEqual(admin.post('/student/add/', data).status_code, 302)
        with tenant_context(self.college_a):
            user = CustomUser.objects.get(email='pic@a.in')
            self.assertTrue(user.profile_pic.name)
            self.assertNotIn('/', user.profile_pic.name.replace('\\', '/').strip('/'), 'a name, not a /media/ URL')
            self.assertEqual(user.photo_url, '/photo/%d/' % user.pk)
        listing = admin.get('/student/manage/').content.decode()
        self.assertIn('src="/photo/%d/"' % user.pk, listing)
        self.assertNotIn('/media/', listing)
        self.assertEqual(admin.get(user.photo_url).status_code, 200)

    def test_the_sidebar_picture_is_the_photo_view(self):
        self.give_picture(self.staff1)
        page = self.as_user(self.staff1).get('/staff/home/').content.decode()
        self.assertIn('id="sidebar-avatar"', page)
        self.assertIn('src="/photo/%d/"' % self.staff1.pk, page)
        self.assertNotIn('/media/', page)

    def test_a_user_without_a_picture_has_no_photo_url(self):
        self.assertEqual(self.stud1.photo_url, '')


class UploadRulesTests(PhotoTestCase):
    def data(self, upload):
        return {'first_name': 'Up', 'last_name': 'Load', 'email': 'up@a.in', 'gender': 'M', 'password': STRONG, 'address': 'x',
                'phone': '', 'roll_number': 'U-1', 'date_of_birth': '2006-01-01', 'section': self.sec1, 'profile_pic': upload}

    def refused(self, upload):
        from main_app.models import CustomUser
        response = self.as_user(self.admin).post('/student/add/', self.data(upload))
        self.assertEqual(response.status_code, 200)
        with tenant_context(self.college_a):
            self.assertFalse(CustomUser.objects.filter(email='up@a.in').exists())
        return response

    def test_a_picture_over_2_mb_is_refused(self):
        padded = png_bytes() + b'\0' * (2 * 1024 * 1024 + 10)
        self.refused(SimpleUploadedFile('big.png', padded, content_type='image/png'))

    def test_only_jpeg_png_and_webp_are_accepted(self):
        buffer = io.BytesIO()
        Image.new('RGB', (4, 4)).save(buffer, 'GIF')
        self.refused(SimpleUploadedFile('anim.gif', buffer.getvalue(), content_type='image/gif'))
        self.refused(SimpleUploadedFile('page.png', b'<html><script>1</script></html>', content_type='image/png'))


class MigrationTests(WorldTestCase):
    def test_old_media_urls_become_storage_names(self):
        migration = importlib.import_module('main_app.migrations.0016_profile_pic_names')
        convert = migration.url_to_name
        self.assertEqual(convert('/media/college_a/me.png', 'college_a'), 'me.png')
        self.assertEqual(convert('/media/me.png', 'college_a'), 'me.png')
        self.assertEqual(convert('me.png', 'college_a'), 'me.png')
        self.assertEqual(convert('', 'college_a'), '')
