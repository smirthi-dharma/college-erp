import json

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render, reverse
from django.views.decorators.http import require_POST

from .access import ALL_ROLES, COLLEGE_ADMIN, home_url_for, offering_for_user, public_view, role_required
from .models import Attendance

# Create your views here.


@public_view
def login_page(request):
    if request.user.is_authenticated:
        home = home_url_for(request.user)
        if home:
            return redirect(home)
        logout(request)  # a role with no pages yet (Head of department): do not loop
        messages.error(request, 'Your role has no pages yet. Please contact your college administrator.')
    return render(request, 'main_app/login.html', {
        'recaptcha_site_key': settings.RECAPTCHA_SITE_KEY if settings.RECAPTCHA_SECRET_KEY else '',
    })


@public_view
def doLogin(request, **kwargs):
    if request.method != 'POST':
        return HttpResponse("<h4>Denied</h4>")
    else:
        # Google reCAPTCHA (keys come from the environment)
        if settings.RECAPTCHA_SITE_KEY and settings.RECAPTCHA_SECRET_KEY:
            try:
                captcha_server = requests.post(
                    "https://www.google.com/recaptcha/api/siteverify",
                    data={
                        'secret': settings.RECAPTCHA_SECRET_KEY,
                        'response': request.POST.get('g-recaptcha-response'),
                    },
                    timeout=10,
                )
                if not captcha_server.json().get('success'):
                    messages.error(request, 'Invalid Captcha. Try Again')
                    return redirect('/')
            except Exception:
                messages.error(request, 'Captcha could not be verified. Try Again')
                return redirect('/')
        elif not settings.DEBUG:
            messages.error(request, 'Login is unavailable: captcha is not configured.')
            return redirect('/')

        #Authenticate
        user = authenticate(request, username=request.POST.get('email'), password=request.POST.get('password'))
        if user is not None and home_url_for(user) is None:
            messages.error(request, 'Your role has no pages yet. Please contact your college administrator.')
            return redirect("/")
        if user is not None:
            login(request, user)
            
            # Handle "Remember Me" functionality
            remember_me = request.POST.get('remember')
            if remember_me:
                # Set session to expire when browser closes = False
                # Session will last for 30 days
                request.session.set_expiry(30 * 24 * 60 * 60)  # 30 days in seconds
            else:
                # Set session to expire when browser closes
                request.session.set_expiry(0)
            
            if user.user_type == '1':
                return redirect(reverse("admin_home"))
            elif user.user_type == '2':
                return redirect(reverse("staff_home"))
            else:
                return redirect(reverse("student_home"))
        else:
            # authenticate() has already sent user_login_failed: recorded in the audit log (email only).
            messages.error(request, "Invalid details")
            return redirect("/")



@role_required(*ALL_ROLES)
@require_POST
def logout_user(request):
    logout(request)
    return redirect("/")


@role_required(COLLEGE_ADMIN)
@require_POST
def get_attendance(request):
    """Attendance dates of an offering. HoD: any offering; staff: only their own."""
    offering = offering_for_user(request, request.POST.get('offering'))
    attendance = Attendance.objects.filter(offering=offering)
    attendance_list = [{"id": attd.id, "attendance_date": str(attd.date)} for attd in attendance]
    return JsonResponse(json.dumps(attendance_list), safe=False)



