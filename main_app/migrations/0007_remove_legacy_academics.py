import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Step 3 of 3: drop Course / Session / Subject and the old links, make the new ones mandatory."""

    dependencies = [
        ('main_app', '0006_migrate_academic_data'),
    ]

    operations = [
        migrations.RemoveField(model_name='staff', name='course'),
        migrations.RemoveField(model_name='subject', name='course'),
        migrations.RemoveField(model_name='student', name='course'),
        migrations.RemoveField(model_name='attendance', name='session'),
        migrations.RemoveField(model_name='student', name='session'),
        migrations.RemoveField(model_name='subject', name='staff'),
        migrations.RemoveField(model_name='studentresult', name='subject'),
        migrations.RemoveField(model_name='attendance', name='subject'),
        migrations.DeleteModel(name='Course'),
        migrations.DeleteModel(name='Session'),
        migrations.DeleteModel(name='Subject'),
        migrations.AlterField(
            model_name='attendance', name='offering',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='academics.subjectoffering'),
        ),
        migrations.AlterField(
            model_name='studentresult', name='offering',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='academics.subjectoffering'),
        ),
    ]
