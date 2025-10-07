# applicants/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from .models import Applicant, ApplicantFile, Vote, DataSet, Batch
from .forms import ApplicantForm, UploadManyFilesForm, EmailLoginForm,DataSetForm, BatchForm

def email_login(request):
    if request.method == "POST":
        form = EmailLoginForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"]
            user, _ = User.objects.get_or_create(username=email, defaults={"email": email})
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            return redirect("applicant_list")
    else:
        form = EmailLoginForm()
    return render(request, "login.html", {"form": form})

@login_required
def applicant_list(request):
    applicants = Applicant.objects.all().order_by("-created_at")
    return render(request, "applicant_list.html", {"applicants": applicants})

@login_required
def applicant_detail(request, pk):
    applicant = get_object_or_404(Applicant, pk=pk)
    return render(request, "applicant_detail.html", {"applicant": applicant})

@login_required
def applicant_create(request):
    if request.method == "POST":
        a_form = ApplicantForm(request.POST)
        f_form = UploadManyFilesForm(request.POST, request.FILES)
        if a_form.is_valid() and f_form.is_valid():
            applicant = a_form.save()
            for f in request.FILES.getlist("files"):      # <— IMPORTANT: 'files' matches the form field name
                ApplicantFile.objects.create(applicant=applicant, file=f)
            return redirect("applicant_detail", pk=applicant.pk)
    else:
        a_form = ApplicantForm()
        f_form = UploadManyFilesForm()
    return render(request, "applicant_create.html", {"form": a_form, "file_form": f_form})


@login_required
def applicant_edit(request, pk):
    applicant = get_object_or_404(Applicant, pk=pk)
    if request.method == "POST":
        form = ApplicantForm(request.POST, instance=applicant)
        if form.is_valid():
            form.save()
            return redirect("applicant_detail", pk=applicant.pk)
    else:
        form = ApplicantForm(instance=applicant)
    return render(request, "applicant_edit.html", {"form": form, "applicant": applicant})


@login_required
def vote(request, pk, value):
    applicant = get_object_or_404(Applicant, pk=pk)
    if value not in ["1", "-1", "0"]:   # 1=Accept, -1=Deny, 0=Waitlist
        return redirect("applicant_detail", pk=pk)
    Vote.objects.update_or_create(
        applicant=applicant, voter=request.user, defaults={"value": int(value)}
    )
    return redirect("applicant_detail", pk=pk)

@login_required
def add_files(request, pk):
    applicant = get_object_or_404(Applicant, pk=pk)
    if request.method == "POST":
        f_form = UploadManyFilesForm(request.POST, request.FILES)
        if f_form.is_valid():
            for f in request.FILES.getlist("files"):
                ApplicantFile.objects.create(applicant=applicant, file=f)
    return redirect("applicant_detail", pk=pk)

@login_required
def add_files(request, pk):
    applicant = get_object_or_404(Applicant, pk=pk)
    if request.method == "POST":
        for f in request.FILES.getlist("files"):   # 👈 matches input name in template
            ApplicantFile.objects.create(applicant=applicant, file=f)
    return redirect("applicant_detail", pk=pk)


@login_required
def dataset_list(request):
    datasets = DataSet.objects.all()
    return render(request, "dataset_list.html", {"datasets": datasets})

@login_required
def dataset_create(request):
    if request.method == "POST":
        form = DataSetForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("dataset_list")
    else:
        form = DataSetForm()
    return render(request, "dataset_create.html", {"form": form})

@login_required
def dataset_detail(request, pk):
    dataset = get_object_or_404(DataSet, pk=pk)
    return render(request, "dataset_detail.html", {"dataset": dataset})

@login_required
def dataset_edit(request, pk):
    dataset = get_object_or_404(DataSet, pk=pk)
    if request.method == "POST":
        form = DataSetForm(request.POST, instance=dataset)
        if form.is_valid():
            form.save()
            return redirect("dataset_detail", pk=dataset.pk)
    else:
        form = DataSetForm(instance=dataset)
    return render(request, "dataset_edit.html", {"form": form, "dataset": dataset})


@login_required
def batch_list(request):
    batches = Batch.objects.select_related("DataSet").all()
    return render(request, "batch_list.html", {"batches": batches})

@login_required
def batch_create(request):
    if request.method == "POST":
        form = BatchForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect("batch_list")
    else:
        form = BatchForm()
    return render(request, "batch_create.html", {"form": form})

@login_required
def batch_detail(request, pk):
    batch = get_object_or_404(Batch, pk=pk)
    return render(request, "batch_detail.html", {"batch": batch})

@login_required
def batch_edit(request, pk):
    batch = get_object_or_404(Batch, pk=pk)
    if request.method == "POST":
        form = BatchForm(request.POST, instance=batch)
        if form.is_valid():
            form.save()
            return redirect("batch_detail", pk=batch.pk)
    else:
        form = BatchForm(instance=batch)
    return render(request, "batch_edit.html", {"form": form, "batch": batch})

