from django import forms
from django.forms import modelform_factory
from django.forms.widgets import DateInput

from . import models


class StyledMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.visible_fields():
            field.field.widget.attrs['class'] = 'form-control'


def build_form(model, widgets=None):
    base = type('Base', (StyledMixin, forms.ModelForm), {})
    return modelform_factory(model, form=base, fields='__all__', widgets=widgets or {})


DATE = {'type': 'date'}
FORMS = {
    'departments': build_form(models.Department),
    'programmes': build_form(models.Programme),
    'regulations': build_form(models.Regulation, {'effective_from': DateInput(attrs=DATE)}),
    'batches': build_form(models.Batch),
    'sections': build_form(models.Section),
    'subjects': build_form(models.Subject),
    'terms': build_form(models.AcademicTerm, {'start_date': DateInput(attrs=DATE), 'end_date': DateInput(attrs=DATE)}),
    'offerings': build_form(models.SubjectOffering),
}


class EnrolStudentForm(StyledMixin, forms.Form):
    student = forms.ModelChoiceField(queryset=None)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from main_app.models import Student
        self.fields['student'].queryset = Student.objects.select_related('admin').order_by('admin__first_name')


class ElectiveImportForm(forms.Form):
    file = forms.FileField(help_text="An .xlsx file whose first row has a column named 'email'.")
