import json
import math
from datetime import datetime

from django.contrib import messages
from django.core.files.storage import default_storage
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .access import STUDENT, role_required
from .forms import *
from .models import *
from .utils import js_json


def _own_offerings(student, active_only=False):
    """Offerings the student is (or, for history, was) enrolled in."""
    from academics.models import Enrolment, SubjectOffering
    enrolments = Enrolment.objects.filter(student=student)
    if active_only:
        enrolments = enrolments.filter(status=Enrolment.ACTIVE)
    return SubjectOffering.objects.filter(pk__in=enrolments.values('offering_id')).select_related('subject')


@role_required(STUDENT)
def student_home(request):
    student = get_object_or_404(Student, admin=request.user)
    offerings = _own_offerings(student, active_only=True)
    total_subject = offerings.count()
    total_attendance = AttendanceReport.objects.filter(student=student).count()
    total_present = AttendanceReport.objects.filter(student=student, status__in=['present', 'on_duty']).count()
    if total_attendance == 0:  # Don't divide. DivisionByZero
        percent_absent = percent_present = 0
    else:
        percent_present = math.floor((total_present/total_attendance) * 100)
        percent_absent = math.ceil(100 - percent_present)
    subject_name = []
    data_present = []
    data_absent = []
    for offering in offerings:
        reports = AttendanceReport.objects.filter(attendance__offering=offering, student=student)
        subject_name.append(offering.subject.name)
        data_present.append(reports.filter(status__in=['present', 'on_duty']).count())
        data_absent.append(reports.filter(status='absent').count())
    from attendance.services import summarize
    attendance_summary = summarize([student])[student.pk]
    context = {
        'attendance_summary': attendance_summary,
        'total_attendance': total_attendance,
        'percent_present': percent_present,
        'percent_absent': percent_absent,
        'total_subject': total_subject,
        'subjects': offerings,
        'data_present': js_json(data_present),
        'data_absent': js_json(data_absent),
        'data_name': js_json(subject_name),
        'page_title': 'Student Homepage'

    }
    return render(request, 'student_template/home_content.html', context)


@role_required(STUDENT)
def student_view_attendance(request):
    student = get_object_or_404(Student, admin=request.user)
    if request.method != 'POST':
        context = {
            'offerings': _own_offerings(student),
            'page_title': 'View Attendance'
        }
        return render(request, 'student_template/student_view_attendance.html', context)
    else:
        start = request.POST.get('start_date')
        end = request.POST.get('end_date')
        offering = get_object_or_404(_own_offerings(student), id=request.POST.get('offering'))
        try:
            start_date = datetime.strptime(start, "%Y-%m-%d")
            end_date = datetime.strptime(end, "%Y-%m-%d")
        except (TypeError, ValueError):
            return JsonResponse({'error': 'Invalid date'}, status=400)
        attendance = Attendance.objects.filter(date__range=(start_date, end_date), offering=offering)
        attendance_reports = AttendanceReport.objects.filter(attendance__in=attendance, student=student)
        json_data = [{"date": str(report.attendance.date), "status": report.status != "absent"}
                     for report in attendance_reports]
        return JsonResponse(json.dumps(json_data), safe=False)






@role_required(STUDENT)
def student_view_profile(request):
    student = get_object_or_404(Student, admin=request.user)
    form = StudentEditForm(request.POST or None, request.FILES or None,
                           instance=student)
    context = {'form': form,
               'page_title': 'View/Edit Profile'
               }
    if request.method == 'POST':
        try:
            if form.is_valid():
                first_name = form.cleaned_data.get('first_name')
                last_name = form.cleaned_data.get('last_name')
                password = form.cleaned_data.get('password') or None
                address = form.cleaned_data.get('address')
                gender = form.cleaned_data.get('gender')
                passport = request.FILES.get('profile_pic') or None
                admin = student.admin
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
                student.save()
                messages.success(request, "Profile Updated!")
                return redirect(reverse('student_view_profile'))
            else:
                messages.error(request, "Invalid Data Provided")
        except Exception:
            messages.error(request, "Could not update the profile.")

    return render(request, "student_template/student_view_profile.html", context)






@role_required(STUDENT)
def student_view_result(request):
    student = get_object_or_404(Student, admin=request.user)
    results = StudentResult.objects.filter(student=student)
    context = {
        'results': results,
        'page_title': "View Results"
    }
    return render(request, "student_template/student_view_result.html", context)
