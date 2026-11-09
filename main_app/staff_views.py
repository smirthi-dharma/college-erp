import json
import logging
from datetime import datetime

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.core.files.storage import default_storage
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .access import STAFF, current_staff, offering_for_staff, role_required, students_of_offering
from .forms import *
from .models import *
from .utils import js_json

logger = logging.getLogger(__name__)


def _require_student_in_offering(student, offering):
    if not students_of_offering(offering).filter(pk=student.pk).exists():
        raise PermissionDenied('This student is not enrolled in the offering.')


def _own_offerings(staff):
    from academics.models import SubjectOffering
    return SubjectOffering.objects.filter(faculty=staff).select_related('subject', 'section__batch', 'term')


@role_required(STAFF)
def staff_home(request):
    from academics.models import Enrolment
    staff = current_staff(request)
    offerings = _own_offerings(staff)
    total_students = Enrolment.objects.filter(
        offering__in=offerings, status=Enrolment.ACTIVE).values('student').distinct().count()
    total_leave = LeaveReportStaff.objects.filter(staff=staff).count()
    total_subject = offerings.count()
    total_attendance = Attendance.objects.filter(offering__in=offerings).count()
    attendance_list = []
    subject_list = []
    for offering in offerings:
        subject_list.append(offering.subject.name)
        attendance_list.append(Attendance.objects.filter(offering=offering).count())
    context = {
        'page_title': 'Staff Panel - ' + str(staff.admin.first_name) + ' ' + str(staff.admin.last_name[:1]) + ' (' + str(staff.department or 'No department') + ')',
        'total_students': total_students,
        'total_attendance': total_attendance,
        'total_leave': total_leave,
        'total_subject': total_subject,
        'subject_list': js_json(subject_list),
        'attendance_list': js_json(attendance_list)
    }
    return render(request, "staff_template/erpnext_staff_home.html", context)


@role_required(STAFF)
def staff_take_attendance(request):
    context = {
        'offerings': _own_offerings(current_staff(request)),
        'page_title': 'Take Attendance'
    }
    return render(request, 'staff_template/staff_take_attendance.html', context)


@role_required(STAFF)
@require_POST
def get_students(request):
    offering = offering_for_staff(request, request.POST.get('offering'))
    student_data = [{"id": s.id, "name": s.admin.last_name + " " + s.admin.first_name}
                    for s in students_of_offering(offering).select_related('admin')]
    return JsonResponse(json.dumps(student_data), content_type='application/json', safe=False)


@role_required(STAFF)
@require_POST
def save_attendance(request):
    offering = offering_for_staff(request, request.POST.get('offering'))
    try:
        day = datetime.strptime(request.POST.get('date', ''), '%Y-%m-%d').date()
        entries = json.loads(request.POST.get('student_ids', ''))
        if not isinstance(entries, list):
            raise ValueError('student_ids must be a list')
    except (TypeError, ValueError):
        return HttpResponse('Invalid attendance data', status=400)
    with transaction.atomic():
        attendance = Attendance.objects.create(offering=offering, date=day)
        for entry in entries:
            student = get_object_or_404(Student, id=entry.get('id'))
            _require_student_in_offering(student, offering)
            AttendanceReport.objects.create(student=student, attendance=attendance,
                                            status=bool(entry.get('status')))
    return HttpResponse("OK")


@role_required(STAFF)
def staff_update_attendance(request):
    context = {
        'offerings': _own_offerings(current_staff(request)),
        'page_title': 'Update Attendance'
    }
    return render(request, 'staff_template/staff_update_attendance.html', context)


def _own_attendance(request, attendance_id):
    attendance = get_object_or_404(Attendance, id=attendance_id)
    offering_for_staff(request, attendance.offering_id)  # 403 if it is another teacher's offering
    return attendance


@role_required(STAFF)
@require_POST
def get_student_attendance(request):
    attendance = _own_attendance(request, request.POST.get('attendance_date_id'))
    student_data = [
        {"id": r.student.admin.id,
         "name": r.student.admin.last_name + " " + r.student.admin.first_name,
         "status": r.status}
        for r in AttendanceReport.objects.filter(attendance=attendance)
    ]
    return JsonResponse(json.dumps(student_data), content_type='application/json', safe=False)


@role_required(STAFF)
@require_POST
def update_attendance(request):
    attendance = _own_attendance(request, request.POST.get('date'))
    try:
        entries = json.loads(request.POST.get('student_ids', ''))
        if not isinstance(entries, list):
            raise ValueError('student_ids must be a list')
    except (TypeError, ValueError):
        return HttpResponse('Invalid attendance data', status=400)
    with transaction.atomic():
        for entry in entries:
            student = get_object_or_404(Student, admin_id=entry.get('id'))
            report = get_object_or_404(AttendanceReport, student=student, attendance=attendance)
            report.status = bool(entry.get('status'))
            report.save()
    return HttpResponse("OK")


@role_required(STAFF)
def staff_apply_leave(request):
    form = LeaveReportStaffForm(request.POST or None)
    staff = current_staff(request)
    context = {
        'form': form,
        'leave_history': LeaveReportStaff.objects.filter(staff=staff),
        'page_title': 'Apply for Leave'
    }
    if request.method == 'POST':
        if form.is_valid():
            try:
                obj = form.save(commit=False)
                obj.staff = staff
                obj.save()
                messages.success(
                    request, "Application for leave has been submitted for review")
                return redirect(reverse('staff_apply_leave'))
            except Exception:
                messages.error(request, "Could not apply!")
        else:
            messages.error(request, "Form has errors!")
    return render(request, "staff_template/staff_apply_leave.html", context)


@role_required(STAFF)
def staff_feedback(request):
    form = FeedbackStaffForm(request.POST or None)
    staff = current_staff(request)
    context = {
        'form': form,
        'feedbacks': FeedbackStaff.objects.filter(staff=staff),
        'page_title': 'Add Feedback'
    }
    if request.method == 'POST':
        if form.is_valid():
            try:
                obj = form.save(commit=False)
                obj.staff = staff
                obj.save()
                messages.success(request, "Feedback submitted for review")
                return redirect(reverse('staff_feedback'))
            except Exception:
                messages.error(request, "Could not Submit!")
        else:
            messages.error(request, "Form has errors!")
    return render(request, "staff_template/staff_feedback.html", context)


@role_required(STAFF)
def staff_view_profile(request):
    staff = current_staff(request)
    form = StaffEditForm(request.POST or None, request.FILES or None,instance=staff)
    context = {'form': form, 'page_title': 'View/Update Profile'}
    if request.method == 'POST':
        try:
            if form.is_valid():
                first_name = form.cleaned_data.get('first_name')
                last_name = form.cleaned_data.get('last_name')
                password = form.cleaned_data.get('password') or None
                address = form.cleaned_data.get('address')
                gender = form.cleaned_data.get('gender')
                passport = request.FILES.get('profile_pic') or None
                admin = staff.admin
                if password is not None:
                    admin.set_password(password)
                if passport is not None:
                    fs = default_storage
                    filename = fs.save(passport.name, passport)
                    passport_url = fs.url(filename)
                    admin.profile_pic = passport_url
                admin.first_name = first_name
                admin.last_name = last_name
                admin.address = address
                admin.phone = form.cleaned_data.get('phone', '')
                admin.gender = gender
                admin.save()
                staff.save()
                messages.success(request, "Profile Updated!")
                return redirect(reverse('staff_view_profile'))
            else:
                messages.error(request, "Invalid Data Provided")
                return render(request, "staff_template/staff_view_profile.html", context)
        except Exception:
            logger.exception('staff profile update failed')
            messages.error(request, "Could not update the profile.")
            return render(request, "staff_template/staff_view_profile.html", context)

    return render(request, "staff_template/staff_view_profile.html", context)


@role_required(STAFF)
@require_POST
def staff_fcmtoken(request):
    request.user.fcm_token = request.POST.get('token', '')
    request.user.save(update_fields=['fcm_token'])
    return HttpResponse("True")


@role_required(STAFF)
def staff_view_notification(request):
    staff = current_staff(request)
    notifications = NotificationStaff.objects.filter(staff=staff)
    context = {
        'notifications': notifications,
        'page_title': "View Notifications"
    }
    return render(request, "staff_template/staff_view_notification.html", context)


@role_required(STAFF)
def staff_add_result(request):
    staff = current_staff(request)
    context = {
        'page_title': 'Result Upload',
        'offerings': _own_offerings(staff),
    }
    if request.method == 'POST':
        offering = offering_for_staff(request, request.POST.get('offering'))
        student = get_object_or_404(Student, id=request.POST.get('student_list'))
        _require_student_in_offering(student, offering)
        try:
            test = float(request.POST.get('test'))
            exam = float(request.POST.get('exam'))
        except (TypeError, ValueError):
            messages.warning(request, "Marks must be numbers")
            return render(request, "staff_template/staff_add_result.html", context)
        _, created = StudentResult.objects.update_or_create(
            student=student, offering=offering, defaults={'test': test, 'exam': exam})
        messages.success(request, "Scores Saved" if created else "Scores Updated")
    return render(request, "staff_template/staff_add_result.html", context)


@role_required(STAFF)
@require_POST
def fetch_student_result(request):
    offering = offering_for_staff(request, request.POST.get('offering'))
    student = get_object_or_404(Student, id=request.POST.get('student'))
    _require_student_in_offering(student, offering)
    result = StudentResult.objects.filter(student=student, offering=offering).first()
    if result is None:
        return HttpResponse('False')
    return HttpResponse(json.dumps({'exam': result.exam, 'test': result.test}))
