"""Role-based access control for every endpoint.

Every view must declare its policy explicitly:

* ``@role_required(HOD, STAFF)``  - only logged-in users with one of these roles
* ``@public_view``               - reachable without logging in (login page, etc.)

A view with neither is refused by ``LoginCheckMiddleWare`` (deny by default), and a
test fails if any URL lacks a policy. Object-level checks (a teacher may only act on
their own subjects) live in the helpers below.
"""
from functools import wraps

from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse

from .models import CustomUser, Staff

HOD, STAFF, STUDENT = CustomUser.HOD, CustomUser.STAFF, CustomUser.STUDENT
ALL_ROLES = (HOD, STAFF, STUDENT)

_HOME = {HOD: 'admin_home', STAFF: 'staff_home', STUDENT: 'student_home'}


def home_url_for(user):
    return reverse(_HOME.get(user.user_type, 'login_page'))


def role_required(*roles):
    """Allow only authenticated users whose role is in ``roles``."""
    allowed = frozenset(roles)

    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            user = request.user
            if not user.is_authenticated:
                return redirect(reverse('login_page'))
            if user.user_type not in allowed:
                return redirect(home_url_for(user))
            return view(request, *args, **kwargs)

        wrapper.allowed_roles = allowed
        return wrapper

    return decorator


def public_view(view):
    """Mark a view as intentionally reachable without logging in."""
    view.is_public = True
    return view


# --- object-level helpers ---------------------------------------------------------

def current_staff(request):
    return get_object_or_404(Staff, admin=request.user)


def offering_for_staff(request, offering_id):
    """The offering, if the logged-in teacher teaches it; 404 if missing, 403 if someone else's."""
    from academics.models import SubjectOffering
    offering = get_object_or_404(SubjectOffering, id=offering_id)
    if offering.faculty_id != current_staff(request).pk:
        raise PermissionDenied('This offering is taught by another teacher.')
    return offering


def offering_for_user(request, offering_id):
    """HoD may use any offering; staff only their own."""
    from academics.models import SubjectOffering
    if request.user.user_type == HOD:
        return get_object_or_404(SubjectOffering, id=offering_id)
    return offering_for_staff(request, offering_id)


def students_of_offering(offering):
    """Students a teacher may handle for an offering: its active enrolments (any section)."""
    from academics.models import Enrolment

    from .models import Student
    ids = Enrolment.objects.filter(offering=offering, status=Enrolment.ACTIVE).values('student_id')
    return Student.objects.filter(pk__in=ids)
