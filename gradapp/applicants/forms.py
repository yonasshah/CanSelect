# applicants/forms.py
from django import forms
from .models import Applicant
from .models import DataSet
from .models import Batch
from .models import Comment



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
        fields = ["first_name", "last_name", "email", "age", "gender", "ethnicity", "dataset", "round", "description", "street"]


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
        
class CommentForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Comment
        fields = ['text']
        widgets = {
            'text': forms.Textarea(attrs={'rows': 3, 'placeholder': 'Leave a comment...'}),
        }