import json
import logging

from django.contrib import messages
from django.core.files.storage import default_storage
from django.http import JsonResponse
from django.shortcuts import HttpResponse, get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from .access import COLLEGE_ADMIN, role_required
from .forms import *
from .models import *
from .notifications import send_push
from .utils import js_json

logger = logging.getLogger(__name__)


@role_required(COLLEGE_ADMIN)
def admin_home(request):
    from academics.models import Enrolment, Programme, Subject, SubjectOffering
    total_staff = Staff.objects.all().count()
    total_students = Student.objects.all().count()
    total_subject = Subject.objects.count()
    total_course = Programme.objects.count()

    offering_list, attendance_list, student_count_list_in_subject = [], [], []
    for offering in SubjectOffering.objects.select_related('subject', 'section__batch')[:30]:
        offering_list.append(offering.subject.code[:7])
        attendance_list.append(Attendance.objects.filter(offering=offering).count())
        student_count_list_in_subject.append(
            Enrolment.objects.filter(offering=offering, status=Enrolment.ACTIVE).count())

    programme_name_list, subject_count_list, student_count_list_in_course = [], [], []
    for programme in Programme.objects.all():
        programme_name_list.append(programme.name)
        subject_count_list.append(Subject.objects.filter(programme=programme).count())
        student_count_list_in_course.append(Student.objects.filter(section__batch__programme=programme).count())

    # For Students
    student_attendance_present_list = []
    student_attendance_leave_list = []
    student_name_list = []
    for student in Student.objects.select_related('admin'):
        attendance = AttendanceReport.objects.filter(student_id=student.id, status__in=['present', 'on_duty']).count()
        absent = AttendanceReport.objects.filter(student_id=student.id, status='absent').count()
        leave = LeaveReportStudent.objects.filter(student_id=student.id, status=1).count()
        student_attendance_present_list.append(attendance)
        student_attendance_leave_list.append(leave + absent)
        student_name_list.append(student.admin.first_name)

    context = {
        'page_title': "Administrative Dashboard",
        'total_students': total_students,
        'total_staff': total_staff,
        'total_course': total_course,
        'total_subject': total_subject,
        'subject_list': js_json(offering_list),
        'attendance_list': js_json(attendance_list),
        'student_attendance_present_list': js_json(student_attendance_present_list),
        'student_attendance_leave_list': js_json(student_attendance_leave_list),
        "student_name_list": js_json(student_name_list),
        "student_count_list_in_subject": js_json(student_count_list_in_subject),
        "student_count_list_in_course": js_json(student_count_list_in_course),
        "course_name_list": js_json(programme_name_list),
    }
    return render(request, 'hod_template/home_content.html', context)


@role_required(COLLEGE_ADMIN)
def add_staff(request):
    form = StaffForm(request.POST or None, request.FILES or None)
    context = {'form': form, 'page_title': 'Add Staff'}
    if request.method == 'POST':
        if form.is_valid():
            first_name = form.cleaned_data.get('first_name')
            last_name = form.cleaned_data.get('last_name')
            address = form.cleaned_data.get('address')
            email = form.cleaned_data.get('email')
            gender = form.cleaned_data.get('gender')
            password = form.cleaned_data.get('password')
            department = form.cleaned_data.get('department')
            passport = request.FILES.get('profile_pic')
            fs = default_storage
            filename = fs.save(passport.name, passport)
            passport_url = fs.url(filename)
            try:
                user = CustomUser.objects.create_user(
                    email=email, password=password, user_type=2, first_name=first_name, last_name=last_name, profile_pic=passport_url)
                user.gender = gender
                user.address = address
                user.phone = form.cleaned_data.get('phone', '')
                user.staff.department = department
                user.staff.employee_id = form.cleaned_data.get('employee_id')
                user.save()
                user.staff.save()
                messages.success(request, "Successfully Added")
                return redirect(reverse('add_staff'))

            except Exception:
                logger.exception('HoD action failed')
                messages.error(request, "Could Not Add ")
        else:
            messages.error(request, "Please fulfil all requirements")

    return render(request, 'hod_template/add_staff_template.html', context)


@role_required(COLLEGE_ADMIN)
def add_student(request):
    student_form = StudentForm(request.POST or None, request.FILES or None)
    context = {'form': student_form, 'page_title': 'Add Student'}
    if request.method == 'POST':
        if student_form.is_valid():
            first_name = student_form.cleaned_data.get('first_name')
            last_name = student_form.cleaned_data.get('last_name')
            address = student_form.cleaned_data.get('address')
            email = student_form.cleaned_data.get('email')
            gender = student_form.cleaned_data.get('gender')
            password = student_form.cleaned_data.get('password')
            section = student_form.cleaned_data.get('section')
            passport = request.FILES['profile_pic']
            fs = default_storage
            filename = fs.save(passport.name, passport)
            passport_url = fs.url(filename)
            try:
                user = CustomUser.objects.create_user(
                    email=email, password=password, user_type=3, first_name=first_name, last_name=last_name, profile_pic=passport_url)
                user.gender = gender
                user.address = address
                user.phone = student_form.cleaned_data.get('phone', '')
                user.student.section = section
                user.student.roll_number = student_form.cleaned_data.get('roll_number')
                user.student.date_of_birth = student_form.cleaned_data.get('date_of_birth')
                user.save()
                user.student.save()
                messages.success(request, "Successfully Added")
                return redirect(reverse('add_student'))
            except Exception:
                logger.exception('HoD action failed')
                messages.error(request, "Could Not Add: ")
        else:
            messages.error(request, "Could Not Add: ")
    return render(request, 'hod_template/add_student_template.html', context)


@role_required(COLLEGE_ADMIN)
def manage_staff(request):
    allStaff = CustomUser.objects.filter(user_type=2)
    context = {
        'allStaff': allStaff,
        'page_title': 'Manage Staff'
    }
    return render(request, "hod_template/manage_staff.html", context)


@role_required(COLLEGE_ADMIN)
def manage_student(request):
    students = CustomUser.objects.filter(user_type=3)
    context = {
        'students': students,
        'page_title': 'Manage Students'
    }
    return render(request, "hod_template/manage_student.html", context)


@role_required(COLLEGE_ADMIN)
def edit_staff(request, staff_id):
    staff = get_object_or_404(Staff, id=staff_id)
    form = StaffForm(request.POST or None, instance=staff)
    context = {
        'form': form,
        'staff_id': staff_id,
        'page_title': 'Edit Staff'
    }
    if request.method == 'POST':
        if form.is_valid():
            first_name = form.cleaned_data.get('first_name')
            last_name = form.cleaned_data.get('last_name')
            address = form.cleaned_data.get('address')
            username = form.cleaned_data.get('username')
            email = form.cleaned_data.get('email')
            gender = form.cleaned_data.get('gender')
            password = form.cleaned_data.get('password') or None
            department = form.cleaned_data.get('department')
            passport = request.FILES.get('profile_pic') or None
            try:
                user = CustomUser.objects.get(id=staff.admin.id)
                user.username = username
                user.email = email
                if password is not None:
                    user.set_password(password)
                if passport is not None:
                    fs = default_storage
                    filename = fs.save(passport.name, passport)
                    passport_url = fs.url(filename)
                    user.profile_pic = passport_url
                user.first_name = first_name
                user.last_name = last_name
                user.gender = gender
                user.address = address
                user.phone = form.cleaned_data.get('phone', '')
                staff.department = department
                staff.employee_id = form.cleaned_data.get('employee_id')
                user.save()
                staff.save()
                messages.success(request, "Successfully Updated")
                return redirect(reverse('edit_staff', args=[staff_id]))
            except Exception:
                logger.exception('HoD action failed')
                messages.error(request, "Could Not Update ")
        else:
            messages.error(request, "Please fil form properly")
    return render(request, "hod_template/edit_staff_template.html", context)


@role_required(COLLEGE_ADMIN)
def edit_student(request, student_id):
    student = get_object_or_404(Student, id=student_id)
    form = StudentForm(request.POST or None, instance=student)
    context = {
        'form': form,
        'student_id': student_id,
        'page_title': 'Edit Student'
    }
    if request.method == 'POST':
        if form.is_valid():
            first_name = form.cleaned_data.get('first_name')
            last_name = form.cleaned_data.get('last_name')
            address = form.cleaned_data.get('address')
            username = form.cleaned_data.get('username')
            email = form.cleaned_data.get('email')
            gender = form.cleaned_data.get('gender')
            password = form.cleaned_data.get('password') or None
            section = form.cleaned_data.get('section')
            passport = request.FILES.get('profile_pic') or None
            try:
                user = CustomUser.objects.get(id=student.admin.id)
                if passport is not None:
                    fs = default_storage
                    filename = fs.save(passport.name, passport)
                    passport_url = fs.url(filename)
                    user.profile_pic = passport_url
                user.username = username
                user.email = email
                if password is not None:
                    user.set_password(password)
                user.first_name = first_name
                user.last_name = last_name
                user.gender = gender
                user.address = address
                user.phone = form.cleaned_data.get('phone', '')
                student.roll_number = form.cleaned_data.get('roll_number')
                student.date_of_birth = form.cleaned_data.get('date_of_birth')
                student.section = section
                user.save()
                student.save()
                messages.success(request, "Successfully Updated")
                return redirect(reverse('edit_student', args=[student_id]))
            except Exception:
                logger.exception('HoD action failed')
                messages.error(request, "Could Not Update ")
        else:
            messages.error(request, "Please Fill Form Properly!")
    return render(request, "hod_template/edit_student_template.html", context)


@role_required(COLLEGE_ADMIN)
@require_POST
def check_email_availability(request):
    email = request.POST.get("email")
    try:
        user = CustomUser.objects.filter(email=email).exists()
        if user:
            return HttpResponse(True)
        return HttpResponse(False)
    except Exception:
        return HttpResponse(False)


@role_required(COLLEGE_ADMIN)
def student_feedback_message(request):
    if request.method != 'POST':
        feedbacks = FeedbackStudent.objects.all()
        context = {
            'feedbacks': feedbacks,
            'page_title': 'Student Feedback Messages'
        }
        return render(request, 'hod_template/student_feedback_template.html', context)
    else:
        feedback_id = request.POST.get('id')
        try:
            feedback = get_object_or_404(FeedbackStudent, id=feedback_id)
            reply = request.POST.get('reply')
            feedback.reply = reply
            feedback.save()
            return HttpResponse(True)
        except Exception:
            return HttpResponse(False)


@role_required(COLLEGE_ADMIN)
def staff_feedback_message(request):
    if request.method != 'POST':
        feedbacks = FeedbackStaff.objects.all()
        context = {
            'feedbacks': feedbacks,
            'page_title': 'Staff Feedback Messages'
        }
        return render(request, 'hod_template/staff_feedback_template.html', context)
    else:
        feedback_id = request.POST.get('id')
        try:
            feedback = get_object_or_404(FeedbackStaff, id=feedback_id)
            reply = request.POST.get('reply')
            feedback.reply = reply
            feedback.save()
            return HttpResponse(True)
        except Exception:
            return HttpResponse(False)


@role_required(COLLEGE_ADMIN)
def view_staff_leave(request):
    if request.method != 'POST':
        allLeave = LeaveReportStaff.objects.all()
        context = {
            'allLeave': allLeave,
            'page_title': 'Leave Applications From Staff'
        }
        return render(request, "hod_template/staff_leave_view.html", context)
    else:
        id = request.POST.get('id')
        status = request.POST.get('status')
        if (status == '1'):
            status = 1
        else:
            status = -1
        try:
            leave = get_object_or_404(LeaveReportStaff, id=id)
            leave.status = status
            leave.save()
            return HttpResponse(True)
        except Exception:
            return HttpResponse(False)


@role_required(COLLEGE_ADMIN)
def view_student_leave(request):
    if request.method != 'POST':
        allLeave = LeaveReportStudent.objects.all()
        context = {
            'allLeave': allLeave,
            'page_title': 'Leave Applications From Students'
        }
        return render(request, "hod_template/student_leave_view.html", context)
    else:
        id = request.POST.get('id')
        status = request.POST.get('status')
        if (status == '1'):
            status = 1
        else:
            status = -1
        try:
            leave = get_object_or_404(LeaveReportStudent, id=id)
            leave.status = status
            leave.save()
            return HttpResponse(True)
        except Exception:
            return HttpResponse(False)


@role_required(COLLEGE_ADMIN)
def admin_view_attendance(request):
    from academics.models import SubjectOffering
    context = {
        'offerings': SubjectOffering.objects.select_related('subject', 'section__batch', 'term'),
        'page_title': 'View Attendance'
    }
    return render(request, "hod_template/admin_view_attendance.html", context)


@role_required(COLLEGE_ADMIN)
@require_POST
def get_admin_attendance(request):
    attendance = get_object_or_404(
        Attendance, id=request.POST.get('attendance_date_id'), offering_id=request.POST.get('offering'))
    json_data = [
        {"status": str(report.status), "name": str(report.student)}
        for report in AttendanceReport.objects.filter(attendance=attendance)
    ]
    return JsonResponse(json.dumps(json_data), safe=False)


@role_required(COLLEGE_ADMIN)
def admin_view_profile(request):
    admin = get_object_or_404(Admin, admin=request.user)
    form = AdminForm(request.POST or None, request.FILES or None,
                     instance=admin)
    context = {'form': form,
               'page_title': 'View/Edit Profile'
               }
    if request.method == 'POST':
        try:
            if form.is_valid():
                first_name = form.cleaned_data.get('first_name')
                last_name = form.cleaned_data.get('last_name')
                password = form.cleaned_data.get('password') or None
                passport = request.FILES.get('profile_pic') or None
                custom_user = admin.admin
                if password is not None:
                    custom_user.set_password(password)
                if passport is not None:
                    fs = default_storage
                    filename = fs.save(passport.name, passport)
                    passport_url = fs.url(filename)
                    custom_user.profile_pic = passport_url
                custom_user.first_name = first_name
                custom_user.last_name = last_name
                custom_user.save()
                messages.success(request, "Profile Updated!")
                return redirect(reverse('admin_view_profile'))
            else:
                messages.error(request, "Invalid Data Provided")
        except Exception:
            logger.exception('HoD action failed')
            messages.error(
                request, "Error Occured While Updating Profile ")
    return render(request, "hod_template/admin_view_profile.html", context)


@role_required(COLLEGE_ADMIN)
def admin_notify_staff(request):
    staff = CustomUser.objects.filter(user_type=2)
    context = {
        'page_title': "Send Notifications To Staff",
        'allStaff': staff
    }
    return render(request, "hod_template/staff_notification.html", context)


@role_required(COLLEGE_ADMIN)
def admin_notify_student(request):
    student = CustomUser.objects.filter(user_type=3)
    context = {
        'page_title': "Send Notifications To Students",
        'students': student
    }
    return render(request, "hod_template/student_notification.html", context)


@role_required(COLLEGE_ADMIN)
@require_POST
def send_student_notification(request):
    student = get_object_or_404(Student, admin_id=request.POST.get('id'))
    message = (request.POST.get('message') or '').strip()
    if not message:
        return HttpResponse("False")
    send_push(student.admin.fcm_token, message, 'student_view_notification')
    NotificationStudent.objects.create(student=student, message=message)
    return HttpResponse("True")


@role_required(COLLEGE_ADMIN)
@require_POST
def send_staff_notification(request):
    staff = get_object_or_404(Staff, admin_id=request.POST.get('id'))
    message = (request.POST.get('message') or '').strip()
    if not message:
        return HttpResponse("False")
    send_push(staff.admin.fcm_token, message, 'staff_view_notification')
    NotificationStaff.objects.create(staff=staff, message=message)
    return HttpResponse("True")


@role_required(COLLEGE_ADMIN)
@require_POST
def delete_staff(request, staff_id):
    staff = get_object_or_404(CustomUser, staff__id=staff_id)
    staff.delete()
    messages.success(request, "Staff deleted successfully!")
    return redirect(reverse('manage_staff'))


@role_required(COLLEGE_ADMIN)
@require_POST
def delete_student(request, student_id):
    student = get_object_or_404(CustomUser, student__id=student_id)
    student.delete()
    messages.success(request, "Student deleted successfully!")
    return redirect(reverse('manage_student'))


