import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Step 1 of 3: add the new attendance fields next to the old boolean (converted in 0011, swapped in 0012)."""

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('main_app', '0009_guardian_studentguardian_customuser_phone_and_more'),
    ]

    operations = [
        migrations.AddField(model_name='attendance', name='period', field=models.PositiveSmallIntegerField(default=1)),
        migrations.AddField(
            model_name='attendance', name='taken_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='+', to=settings.AUTH_USER_MODEL)),
        migrations.AddField(model_name='attendance', name='submission_key',
                            field=models.CharField(blank=True, default='', max_length=40)),
        migrations.AddField(model_name='attendancereport', name='state',
                            field=models.CharField(default='present', max_length=10)),
        migrations.AddField(model_name='attendancereport', name='needs_review', field=models.BooleanField(default=False)),
    ]
