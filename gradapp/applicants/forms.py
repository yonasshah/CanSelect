# applicants/forms.py
from django import forms
from .models import Applicant, Score
from .models import DataSet
from .models import Batch
from .models import Comment
from django.contrib.auth.models import User
from django.db.models import Q
from .models import Profile
from .models import ReviewPanel


class BootstrapFormMixin:
    """Automatically add Bootstrap 5 classes to all form fields."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            widget = field.widget
            if isinstance(widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                css_class = "form-check-input"
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                css_class = "form-select"
            else:
                css_class = "form-control"
            existing = widget.attrs.get("class", "")
            widget.attrs["class"] = f"{existing} {css_class}".strip()


class MultiFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class UploadManyFilesForm(forms.Form):
    files = forms.FileField(
        widget=MultiFileInput(attrs={"multiple": True}),
        required=False,
        help_text="You can select several files at once."
    )


class EmailLoginForm(forms.Form):
    email = forms.EmailField()


class ApplicantForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Applicant
        fields = ["first_name", "last_name", "email", "date_of_birth", "gender",
                  "dataset", "round", "description", "street", "profile_picture", "external_id"]
        widgets = {
            'date_of_birth': forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'),
        }


class DataSetForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = DataSet
        fields = ["DisplayName", "application_system", "program_type", "Description",
                  "AdminNotes", "PublicView", "ProgramId", "Active", "IsLive", "target_class_size"]


class BatchForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Batch
        fields = [
            "DataSet",
            "DisplayName",
            "Description",
            "VoteExpire",
            "AdminNotes",
            "PublicView",
            "Active",
            "RoundId",
        ]
        widgets = {
            'VoteExpire': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
        }


class ScoreForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Score
        fields = ['research_score', 'statement_score', 'overall_score']
        widgets = {
            'research_score': forms.Select(attrs={'class': 'form-select mb-3'}),
            'statement_score': forms.Select(attrs={'class': 'form-select mb-3'}),
            'overall_score': forms.Select(attrs={'class': 'form-select mb-3'}),
        }


class ApplicantStatusForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Applicant
        fields = ['status']


class CommentForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Comment
        fields = ['text']
        widgets = {
            'text': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Leave a comment...'}),
        }


class BatchAssignmentForm(BootstrapFormMixin, forms.Form):
    """
    Reviewer assignment for a batch.
    Includes committee members and admin users marked as sitting reviewers.
    """
    reviewers = forms.ModelMultipleChoiceField(
        queryset=User.objects.filter(
            Q(profile__role=Profile.Role.COMMITTEE_MEMBER) |
            Q(profile__role=Profile.Role.ADMIN, profile__is_reviewer=True)
        ).order_by('username'),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Assign Reviewer(s) to this batch:"
    )

    def __init__(self, *args, **kwargs):
        instance = kwargs.pop('instance', None)
        super().__init__(*args, **kwargs)
        if instance and instance.pk:
            self.fields['reviewers'].initial = instance.assigned_reviewers.all()


class BulkUploadForm(forms.Form):
    folder_files = forms.FileField(
        widget=MultiFileInput(attrs={
            'multiple': True,
            'webkitdirectory': True,
            'directory': True
        }),
        required=True,
        help_text="Select a folder containing subdirectories for each candidate."
    )


class SendNotificationForm(BootstrapFormMixin, forms.Form):
    RECIPIENT_TYPE_CHOICES = [
        ('', '— Select recipient type —'),
        ('dataset', 'All reviewers in a Dataset'),
        ('panel', 'All reviewers in a Panel'),
        ('batch', 'All reviewers in a Batch'),
        ('individual', 'Specific reviewers'),
    ]

    recipient_type = forms.ChoiceField(choices=RECIPIENT_TYPE_CHOICES)
    dataset = forms.ModelChoiceField(
        queryset=DataSet.objects.filter(Active=True).order_by('DisplayName'),
        required=False,
        empty_label='— Select dataset —',
    )
    panel = forms.ModelChoiceField(
        queryset=ReviewPanel.objects.all().order_by('name'),
        required=False,
        empty_label='— Select panel —',
    )
    batch = forms.ModelChoiceField(
        queryset=Batch.objects.filter(Active=True).order_by('DisplayName'),
        required=False,
        empty_label='— Select batch —',
    )
    individual_reviewers = forms.ModelMultipleChoiceField(
        queryset=User.objects.filter(
            Q(profile__role='COMMITTEE_MEMBER') |
            Q(profile__role='ADMIN', profile__is_reviewer=True)
        ).order_by('username'),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    subject = forms.CharField(max_length=255)
    message = forms.CharField(widget=forms.Textarea(attrs={'rows': 5, 'placeholder': 'Write your message here...'}))
    deadline = forms.DateTimeField(
        required=False,
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local', 'class': 'form-control'}),
        help_text='Optional. Reminders with deadlines stay pinned at the top.',
    )


class ReviewPanelForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = ReviewPanel
        fields = ['name', 'description']
        widgets = {
            'description': forms.Textarea(attrs={'rows': 2, 'placeholder': 'Optional description...'}),
        }