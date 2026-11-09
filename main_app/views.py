import json

import requests
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render, reverse
from django.views.decorators.http import require_POST

from .access import ALL_ROLES, COLLEGE_ADMIN, STAFF, offering_for_user, public_view, role_required
from .models import Attendance

# Create your views here.


@public_view
def login_page(request):
    if request.user.is_authenticated:
        if request.user.user_type == '1':
            return redirect(reverse("admin_home"))
        elif request.user.user_type == '2':
            return redirect(reverse("staff_home"))
        else:
            return redirect(reverse("student_home"))
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


@role_required(COLLEGE_ADMIN, STAFF)
@require_POST
def get_attendance(request):
    """Attendance dates of an offering. HoD: any offering; staff: only their own."""
    offering = offering_for_user(request, request.POST.get('offering'))
    attendance = Attendance.objects.filter(offering=offering)
    attendance_list = [{"id": attd.id, "attendance_date": str(attd.date)} for attd in attendance]
    return JsonResponse(json.dumps(attendance_list), safe=False)


@public_view
def showFirebaseJS(request):
    data = """
    // Give the service worker access to Firebase Messaging.
// Note that you can only use Firebase Messaging here, other Firebase libraries
// are not available in the service worker.
importScripts('https://www.gstatic.com/firebasejs/7.22.1/firebase-app.js');
importScripts('https://www.gstatic.com/firebasejs/7.22.1/firebase-messaging.js');

// Initialize the Firebase app in the service worker by passing in
// your app's Firebase config object.
// https://firebase.google.com/docs/web/setup#config-object
firebase.initializeApp(__FIREBASE_CONFIG__);

// Retrieve an instance of Firebase Messaging so that it can handle background
// messages.
const messaging = firebase.messaging();
messaging.setBackgroundMessageHandler(function (payload) {
    const notification = JSON.parse(payload);
    const notificationOption = {
        body: notification.body,
        icon: notification.icon
    }
    return self.registration.showNotification(payload.notification.title, notificationOption);
});
    """
    if not settings.FIREBASE_WEB_CONFIG:
        raise Http404('Push notifications are not configured.')
    data = data.replace('__FIREBASE_CONFIG__', json.dumps(settings.FIREBASE_WEB_CONFIG))
    return HttpResponse(data, content_type='application/javascript')

