from django.contrib import messages
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View

from .access import current_staff, students_of_offering
from .forms import EditResultForm
from .models import Student, StudentResult


class EditResultView(View):
    """Edit an existing result. Wrapped with @role_required(STAFF) in urls.py."""

    template = "staff_template/edit_student_result.html"

    def _form(self, request, data=None):
        from academics.models import Enrolment, SubjectOffering
        staff = current_staff(request)
        form = EditResultForm(data)
        # A teacher can only pick their own offerings and the students enrolled in them.
        offerings = SubjectOffering.objects.filter(faculty=staff)
        form.fields['offering'].queryset = offerings
        form.fields['student'].queryset = Student.objects.filter(
            pk__in=Enrolment.objects.filter(offering__in=offerings).values('student_id'))
        return form

    def get(self, request, *args, **kwargs):
        context = {'form': self._form(request), 'page_title': "Edit Student's Result"}
        return render(request, self.template, context)

    def post(self, request, *args, **kwargs):
        form = self._form(request, request.POST)
        context = {'form': form, 'page_title': "Edit Student's Result"}
        if form.is_valid():
            student = form.cleaned_data['student']
            offering = form.cleaned_data['offering']
            if not students_of_offering(offering).filter(pk=student.pk).exists():
                messages.warning(request, "Result Could Not Be Updated")
                return render(request, self.template, context)
            # save() (not queryset.update) so the change is written to the audit log
            result = StudentResult.objects.filter(student=student, offering=offering).first()
            if result is not None:
                result.test = form.cleaned_data['test']
                result.exam = form.cleaned_data['exam']
                result.save()
                messages.success(request, "Result Updated")
                return redirect(reverse('edit_student_result'))
        messages.warning(request, "Result Could Not Be Updated")
        return render(request, self.template, context)
