# applicants/forms.py
from django import forms
from .models import Applicant, Score
from .models import DataSet
from .models import Batch
from .models import Comment
from django.contrib.auth.models import User
from .models import Profile



class BootstrapFormMixin:
    """Automatically add Bootstrap 5 classes to all form fields."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            widget = field.widget

            # Bootstrap form-control class for most inputs
            if isinstance(widget, (forms.CheckboxInput, forms.CheckboxSelectMultiple)):
                css_class = "form-check-input"
            elif isinstance(widget, (forms.Select, forms.SelectMultiple)):
                css_class = "form-select"
            else:
                css_class = "form-control"

            existing = widget.attrs.get("class", "")
            widget.attrs["class"] = f"{existing} {css_class}".strip()


# --- NEW: truly multi-file widget + form ---
class MultiFileInput(forms.ClearableFileInput):
    # This is the key: tell Django this input can select multiple files
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
        fields = ["first_name", "last_name", "email", "age", "gender", "dataset", "round", "description", "street", "profile_picture", "external_id"]


class DataSetForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = DataSet
        fields = ["DisplayName", "application_system", "program_type", "Description", "AdminNotes", "PublicView", "ProgramId", "Active", "IsLive", "target_class_size"]

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
            "review_group",
        ]
        widgets = {
            'VoteExpire': forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M'),
        }
        
class ScoreForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Score
        # Add any other criteria you defined in the model
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
    reviewers = forms.ModelMultipleChoiceField(
        queryset=User.objects.filter(profile__role=Profile.Role.COMMITTEE_MEMBER).order_by('username'),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Assign Committee Member(s) to this batch:"
    )
        
    def __init__(self, *args, **kwargs):
        instance = kwargs.pop('instance', None)
        
        super().__init__(*args, **kwargs)
        
        if instance and instance.pk:
            self.fields['reviewers'].initial = instance.assigned_reviewers.all()
            
class BulkUploadForm(forms.Form):
    folder_files = forms.FileField(
        # Use the MultiFileInput class you already defined
        widget=MultiFileInput(attrs={
            'multiple': True, 
            'webkitdirectory': True, 
            'directory': True
        }),
        required=True,
        help_text="Select a folder containing subdirectories for each candidate."
    )
    
class ReviewerGroupForm(forms.Form):
    """
    Dynamic form that renders one select per committee member.
    Field names are like 'user_5' where 5 is the user PK.
    """
    def __init__(self, *args, **kwargs):
        members = kwargs.pop('members', [])
        super().__init__(*args, **kwargs)
 
        GROUP_CHOICES = [
            ('', 'Unassigned'),
            ('A', 'Group A'),
            ('B', 'Group B'),
            ('C', 'Group C'),
        ]
 
        for user in members:
            self.fields[f'user_{user.pk}'] = forms.ChoiceField(
                choices=GROUP_CHOICES,
                required=False,
                initial=user.profile.review_group,
                label=user.username,
                widget=forms.Select(attrs={'class': 'form-select form-select-sm'}),
            )
            
class SendNotificationForm(BootstrapFormMixin, forms.Form):
    RECIPIENT_TYPE_CHOICES = [
        ('', '— Select recipient type —'),
        ('dataset', 'All reviewers in a Dataset'),
        ('group', 'All reviewers in a Group'),
        ('batch', 'All reviewers in a Batch'),
        ('individual', 'Specific reviewers'),
    ]
 
    GROUP_CHOICES = [
        ('', '— Select group —'),
        ('A', 'Group A'),
        ('B', 'Group B'),
        ('C', 'Group C'),
    ]
 
    recipient_type = forms.ChoiceField(choices=RECIPIENT_TYPE_CHOICES)
    dataset = forms.ModelChoiceField(
        queryset=DataSet.objects.filter(Active=True).order_by('DisplayName'),
        required=False,
        empty_label='— Select dataset —',
    )
    group = forms.ChoiceField(choices=GROUP_CHOICES, required=False)
    batch = forms.ModelChoiceField(
        queryset=Batch.objects.filter(Active=True).order_by('DisplayName'),
        required=False,
        empty_label='— Select batch —',
    )
    individual_reviewers = forms.ModelMultipleChoiceField(
        queryset=User.objects.filter(profile__role='COMMITTEE_MEMBER').order_by('username'),
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
