"""Profile pictures used to be stored as a URL ("/media/<college>/me.png"); they are now stored as the storage name
("me.png") and served by an authenticated view. Existing rows are converted; a value that is not a media URL is left alone."""
from django.db import migrations


def url_to_name(value, schema_name, media_url='/media/'):
    if not value or not value.startswith(media_url):
        return value
    name = value[len(media_url):]
    prefix = schema_name + '/'
    return name[len(prefix):] if name.startswith(prefix) else name


def convert(apps, schema_editor):
    CustomUser = apps.get_model('main_app', 'CustomUser')
    schema = schema_editor.connection.schema_name
    for user in CustomUser.objects.filter(profile_pic__startswith='/media/'):
        CustomUser.objects.filter(pk=user.pk).update(profile_pic=url_to_name(str(user.profile_pic), schema))


class Migration(migrations.Migration):
    dependencies = [('main_app', '0015_remove_leave_feedback_notifications')]
    operations = [migrations.RunPython(convert, migrations.RunPython.noop)]
