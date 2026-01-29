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
        fields = ["first_name", "last_name", "email", "age", "gender", "ethnicity", "dataset", "round", "description", "street", "profile_picture"]


class DataSetForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = DataSet
        fields = ["DisplayName", "Description", "AdminNotes", "PublicView", "ProgramId", "Active", "IsLive"]

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
            "HighlightBefore",
            "RoundId",
        ]
        
        
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