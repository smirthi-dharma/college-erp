from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from .access import current_staff
from .forms import EditResultForm
from .models import Student, StudentResult, Subject


class EditResultView(View):
    """Edit an existing result. Wrapped with @role_required(STAFF) in urls.py."""

    template = "staff_template/edit_student_result.html"

    def _form(self, request, data=None):
        staff = current_staff(request)
        form = EditResultForm(data)
        # A teacher can only pick their own subjects and the students of those subjects' courses.
        subjects = Subject.objects.filter(staff=staff)
        form.fields['subject'].queryset = subjects
        form.fields['student'].queryset = Student.objects.filter(course__in=subjects.values('course'))
        return form

    def get(self, request, *args, **kwargs):
        context = {'form': self._form(request), 'page_title': "Edit Student's Result"}
        return render(request, self.template, context)

    def post(self, request, *args, **kwargs):
        form = self._form(request, request.POST)
        context = {'form': form, 'page_title': "Edit Student's Result"}
        if form.is_valid():
            student = form.cleaned_data['student']
            subject = form.cleaned_data['subject']
            if student.course_id != subject.course_id:
                messages.warning(request, "Result Could Not Be Updated")
                return render(request, self.template, context)
            # save() (not queryset.update) so the change is written to the audit log
            result = StudentResult.objects.filter(student=student, subject=subject).first()
            if result is not None:
                result.test = form.cleaned_data['test']
                result.exam = form.cleaned_data['exam']
                result.save()
                messages.success(request, "Result Updated")
                return redirect(reverse('edit_student_result'))
        messages.warning(request, "Result Could Not Be Updated")
        return render(request, self.template, context)
