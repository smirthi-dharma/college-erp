from django import forms

from academics.models import SubjectOffering
from main_app.models import Staff, Student

from .models import AttendanceRule


class Styled:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.visible_fields():
            field.field.widget.attrs.setdefault('class', 'form-control')


class RuleForm(Styled, forms.ModelForm):
    class Meta:
        model = AttendanceRule
        fields = ['required_percent', 'warning_percent', 'condonable_percent', 'basis', 'on_duty_counts_as_present', 'edit_window_days']


class CondonationForm(Styled, forms.Form):
    student = forms.ModelChoiceField(queryset=Student.objects.none())
    offering = forms.ModelChoiceField(queryset=SubjectOffering.objects.none(), required=False,
                                      help_text='Leave empty when attendance is counted overall.')
    reason = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['student'].queryset = Student.objects.select_related('admin').order_by('roll_number', 'admin__last_name')
        self.fields['offering'].queryset = SubjectOffering.objects.select_related('subject', 'section', 'term')


class SubstituteForm(Styled, forms.Form):
    offering = forms.ModelChoiceField(queryset=SubjectOffering.objects.none())
    date = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    period = forms.IntegerField(min_value=1, max_value=8, initial=1)
    substitute = forms.ModelChoiceField(queryset=Staff.objects.none())

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['offering'].queryset = SubjectOffering.objects.select_related('subject', 'section', 'term')
        self.fields['substitute'].queryset = Staff.objects.select_related('admin').order_by('admin__first_name')
