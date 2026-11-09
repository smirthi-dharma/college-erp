from django.db import migrations, models


class Migration(migrations.Migration):
    """Step 3 of 3: drop the boolean, keep the converted status, add the uniqueness rules."""

    dependencies = [('main_app', '0011_convert_attendance_data')]

    operations = [
        migrations.RemoveField(model_name='attendancereport', name='status'),
        migrations.RenameField(model_name='attendancereport', old_name='state', new_name='status'),
        migrations.AlterField(
            model_name='attendancereport', name='status',
            field=models.CharField(choices=[('present', 'Present'), ('absent', 'Absent'), ('on_duty', 'On duty')],
                                   default='present', max_length=10)),
        migrations.AddConstraint(
            model_name='attendance',
            constraint=models.UniqueConstraint(fields=('offering', 'date', 'period'), name='uniq_class_per_period')),
        migrations.AddConstraint(
            model_name='attendancereport',
            constraint=models.UniqueConstraint(fields=('attendance', 'student'), name='uniq_report_per_student')),
    ]
