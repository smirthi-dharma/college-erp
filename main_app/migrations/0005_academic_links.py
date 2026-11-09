import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Step 1 of 3: add the new links next to the old ones (old data is converted in 0006)."""

    dependencies = [
        ('academics', '0001_initial'),
        ('main_app', '0004_drop_library_models'),
    ]

    operations = [
        migrations.AddField(
            model_name='attendance', name='offering',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, to='academics.subjectoffering'),
        ),
        migrations.AddField(
            model_name='staff', name='department',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='academics.department'),
        ),
        migrations.AddField(
            model_name='student', name='section',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='academics.section'),
        ),
        migrations.AddField(
            model_name='studentresult', name='offering',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, to='academics.subjectoffering'),
        ),
    ]
