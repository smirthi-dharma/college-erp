"""Profile pictures are private: served only through this view, never from a public media URL.

Who may see whose picture: everyone their own; the college admin everyone in their college; a teacher the students
actively enrolled in their own classes (the same rule as Student 360); a student nobody else's. The college is the
database schema the request is routed to, and the storage folder is that college's own, so another college's file
cannot be reached. A picture the viewer may not see answers 404, not 403, so ids cannot be probed.
"""
import mimetypes

from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404

from student360.services import visible_students

from .access import ALL_ROLES, COLLEGE_ADMIN, STAFF, role_required
from .models import CustomUser

SAFE_TYPES = {'image/jpeg', 'image/png', 'image/webp'}  # what the upload form accepts; anything else is never sent


def may_see_photo(viewer, target):
    if viewer.pk == target.pk or viewer.user_type == COLLEGE_ADMIN:
        return True
    if viewer.user_type == STAFF and target.user_type == CustomUser.STUDENT:
        return visible_students(viewer).filter(admin_id=target.pk).exists()
    return False


@role_required(*ALL_ROLES)
def profile_photo(request, user_id):
    target = get_object_or_404(CustomUser, pk=user_id)
    if not may_see_photo(request.user, target) or not target.profile_pic:
        raise Http404('No picture.')
    content_type = mimetypes.guess_type(target.profile_pic.name)[0]
    if content_type not in SAFE_TYPES:
        raise Http404('No picture.')
    try:
        handle = target.profile_pic.open('rb')
    except (FileNotFoundError, ValueError, OSError):
        raise Http404('No picture.') from None
    response = FileResponse(handle, content_type=content_type)
    response['Cache-Control'] = 'private, max-age=300'  # the browser may reuse it for five minutes; shared caches never
    response['Content-Disposition'] = 'inline; filename="photo"'
    return response
