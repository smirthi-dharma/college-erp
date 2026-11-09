"""Step 2 of 3: present/absent booleans become statuses; duplicate classes and reports are resolved.

- status True -> 'present', False -> 'absent'.
- Two Attendance rows for the same offering and date (the old code allowed it) get the next free period, so no class is lost.
- Two reports for the same student in one class keep the newest; their number is printed.
"""
from django.db import migrations


def forwards(apps, schema_editor):
    Attendance = apps.get_model('main_app', 'Attendance')
    Report = apps.get_model('main_app', 'AttendanceReport')

    for report in Report.objects.all().only('id', 'status'):
        Report.objects.filter(pk=report.pk).update(state='present' if report.status else 'absent')

    seen = {}
    for att in Attendance.objects.order_by('offering_id', 'date', 'id'):
        key = (att.offering_id, att.date)
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            Attendance.objects.filter(pk=att.pk).update(period=seen[key])

    dropped = 0
    keep = {}
    for report in Report.objects.order_by('attendance_id', 'student_id', 'id'):
        key = (report.attendance_id, report.student_id)
        if key in keep:
            Report.objects.filter(pk=keep[key]).delete()  # the earlier duplicate
            dropped += 1
        keep[key] = report.pk
    if dropped:
        print(f'  attendance migration: removed {dropped} duplicate student report(s), newest kept')


class Migration(migrations.Migration):

    dependencies = [('main_app', '0010_attendance_fields')]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
