# applicants/views.py
import csv
import random
from django.db.models import Count, Q, Avg, Exists, OuterRef
import json
from itertools import cycle
from django.utils.safestring import mark_safe
from django.http import HttpResponse
from django.core.paginator import Paginator
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login
from django.contrib import messages
from .decorators import admin_required
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from .models import Activity, Applicant, ApplicantFile, Score, Vote, DataSet, Batch
from .forms import ApplicantForm, ApplicantStatusForm, BatchAssignmentForm, UploadManyFilesForm, EmailLoginForm,DataSetForm, BatchForm, CommentForm, ScoreForm

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

def applicant_list(request):
    datasets = DataSet.objects.all()
    selected_dataset_id = request.GET.get('dataset')
    search_query = request.GET.get('q', '')
    
    user_has_voted_subquery = Vote.objects.filter(
    applicant=OuterRef('pk'),
    voter=request.user
)

    applicants_list = Applicant.objects.select_related('dataset', 'round').annotate(
        avg_score=Avg('scores__overall_score'),
        user_has_voted=Exists(user_has_voted_subquery)
    ).order_by("-created_at")
    

    if selected_dataset_id:
        applicants_list = applicants_list.filter(dataset_id=selected_dataset_id)

    if search_query:
        applicants_list = applicants_list.filter(
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query)
        )
        
    paginator = Paginator(applicants_list, 25) # Show 25 applicants per page
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj, 
        'datasets': datasets,
        'selected_dataset_id': selected_dataset_id,
        'search_query': search_query,
    }
    return render(request, "applicant_list.html", context)

@login_required
def applicant_detail(request, pk):
    applicant = get_object_or_404(Applicant, pk=pk)
    user_score, _ = Score.objects.get_or_create(applicant=applicant, voter=request.user)
    score_form = ScoreForm(instance=user_score)
    status_form = ApplicantStatusForm(instance=applicant)
    
    avg_scores = Score.objects.filter(applicant=applicant).aggregate(
        avg_research=Avg('research_score'),
        avg_statement=Avg('statement_score'),
        avg_overall=Avg('overall_score')
    )
    
    comment_form = CommentForm()
    
    current_user_vote = Vote.objects.filter(applicant=applicant, voter=request.user).first()

    # Fetches comments based on user role
    if request.user.profile.role == 'ADMIN':
        comments = applicant.comments.select_related('author').all()
    else:
        comments = applicant.comments.filter(author=request.user).select_related('author')

    # Handles new comment submissions
    if request.method == 'POST':
        comment_form = CommentForm(request.POST)
        if comment_form.is_valid():
            new_comment = comment_form.save(commit=False)
            new_comment.applicant = applicant
            new_comment.author = request.user
            new_comment.save()
            messages.success(request, "Your comment has been posted.")
            return redirect('applicant_detail', pk=applicant.pk)

    all_files = applicant.files.all()
    video_files = [f for f in all_files if f.is_video]
    other_files = [f for f in all_files if not f.is_video]

    context = {
        'applicant': applicant,
        'comments': comments,
        'comment_form': comment_form,
        'current_user_vote': current_user_vote,
        'video_files': video_files,
        'other_files': other_files,
        'score_form': score_form,
        'status_form': status_form,
        'avg_scores': avg_scores,
    }
    return render(request, "applicant_detail.html", context)

@login_required
@admin_required
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
@admin_required
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
    if request.method == "POST":
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
def dataset_list(request):
    datasets_list = DataSet.objects.all()

    paginator = Paginator(datasets_list, 25) # Show 25 datasets per page
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
    }
    return render(request, "dataset_list.html", context)

@login_required
@admin_required
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

    applicants = Applicant.objects.filter(
        Q(dataset=dataset) | Q(round__DataSet=dataset)
    ).distinct()

    batches = dataset.batches.all()
    context = {
        "dataset": dataset,
        "applicants": applicants,
        "batches": batches,
    }
    return render(request, "dataset_detail.html", context)

@login_required
@admin_required
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
    datasets = DataSet.objects.all()
    selected_dataset_id = request.GET.get('dataset')

    if selected_dataset_id:
        batches_list = Batch.objects.filter(DataSet_id=selected_dataset_id).select_related("DataSet")
    else:
        batches_list = Batch.objects.select_related("DataSet").all()

    paginator = Paginator(batches_list, 25) # Show 25 batches per page
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,  
        'datasets': datasets,
        'selected_dataset_id': selected_dataset_id
    }
    return render(request, "batch_list.html", context)

@login_required
@admin_required
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
@admin_required
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

@login_required
def dashboard(request):
    # --- 1. Get filter ID and all datasets for the dropdown ---
    selected_dataset_id = request.GET.get('dataset')
    all_datasets = DataSet.objects.all().order_by('DisplayName')
    selected_dataset = None

    # --- 2. Create base querysets that can be filtered ---
    applicants_qs = Applicant.objects.all()
    
    if selected_dataset_id:
        try:
            selected_dataset = all_datasets.get(pk=selected_dataset_id)
            # Filter the base applicant query
            applicants_qs = applicants_qs.filter(dataset_id=selected_dataset_id)
        except DataSet.DoesNotExist:
            pass # Ignore if the ID is invalid

    # --- 3. Get overall counts (these can stay unfiltered) ---
    applicant_count = Applicant.objects.count()
    dataset_count = all_datasets.count()
    batch_count = Batch.objects.count()

    # Get the 5 most recent applicants (also unfiltered)
    recent_applicants = Applicant.objects.order_by('-created_at')[:5]

    # Get recent activities, but only if the user is an Admin
    recent_activities = []
    if request.user.profile.role == 'ADMIN':
        recent_activities = Activity.objects.filter(
            action_type__in=[Activity.VOTE_CAST, Activity.COMMENT_ADDED]
        ).select_related('actor', 'target_applicant')[:7]

    # --- 4. Run queries for charts *using the filtered applicants_qs* ---
    
    # Data for Vote Summary Chart
    vote_summary = Vote.objects.filter(applicant__in=applicants_qs).aggregate(
        accept_count=Count('pk', filter=Q(value=1)),
        deny_count=Count('pk', filter=Q(value=-1)),
        waitlist_count=Count('pk', filter=Q(value=0))
    )
    
    # Data for Demographics (Gender)
    gender_distribution = applicants_qs.values('gender').annotate(
        count=Count('id')
    ).order_by()

    # Prepare data for Chart.js
    gender_labels = json.dumps([item['gender'] or 'Not Specified' for item in gender_distribution])
    gender_data = json.dumps([item['count'] for item in gender_distribution])

    context = {
        'applicant_count': applicant_count,
        'dataset_count': dataset_count,
        'batch_count': batch_count,
        'recent_applicants': recent_applicants,
        'recent_activities': recent_activities,
        
        'vote_summary': vote_summary,
        'gender_labels': mark_safe(gender_labels),
        'gender_data': mark_safe(gender_data),

        # --- 5. Add new context for the filter dropdown ---
        'all_datasets': all_datasets,
        'selected_dataset': selected_dataset,
    }
    return render(request, "dashboard.html", context)

@login_required
def applicant_profile_partial(request, pk):
    applicant = get_object_or_404(Applicant, pk=pk)

    # Fetch votes
    all_votes = Vote.objects.filter(applicant=applicant).select_related('voter')
    current_user_vote = all_votes.filter(voter=request.user).first()

    # Fetch comments based on user role
    if request.user.profile.role == 'ADMIN':
        # Admins see all comments
        comments = applicant.comments.select_related('author').all()
    else:
        # Committee Members only see their own comments
        comments = applicant.comments.filter(author=request.user).select_related('author')
        
    all_files = applicant.files.all()
    video_files = [f for f in all_files if f.is_video]
    other_files = [f for f in all_files if not f.is_video]
            
    context = {
        'applicant': applicant,
        'all_votes': all_votes,
        'current_user_vote': current_user_vote,
        'comments': comments,
        'video_files': video_files,
        'other_files': other_files,
    }
    return render(request, "partials/applicant_profile_partial.html", context)

@login_required
def compare_applicants(request):
    applicant_ids = request.GET.getlist('ids')
    if not applicant_ids:
        messages.warning(request, "You must select at least two applicants to compare.")
        return redirect("applicant_list")

    applicants = Applicant.objects.filter(pk__in=applicant_ids)
    
    # Determine column size for Bootstrap grid
    column_count = len(applicants)
    col_class = f"col-md-{12 // column_count if column_count > 0 else 12}"


    context = {
        'applicants': applicants,
        'col_class': col_class
    }
    return render(request, "compare_applicants.html", context)

@login_required
def export_applicants_csv(request):
    # Get the same queryset as the applicant list page, including filters
    selected_dataset_id = request.GET.get('dataset')
    if selected_dataset_id:
        applicants = Applicant.objects.filter(dataset_id=selected_dataset_id).order_by("-created_at")
    else:
        applicants = Applicant.objects.all().order_by("-created_at")

    # Create the HttpResponse object with the appropriate CSV header.
    response = HttpResponse(
        content_type='text/csv',
        headers={'Content-Disposition': 'attachment; filename="applicants.csv"'},
    )

    writer = csv.writer(response)
    # Write the header row
    writer.writerow(['First Name', 'Last Name', 'Email', 'Age', 'Gender', 'Ethnicity', 'DataSet', 'Round'])

    # Write data rows
    for applicant in applicants:
        writer.writerow([
            applicant.first_name,
            applicant.last_name,
            applicant.email,
            applicant.age,
            applicant.gender,
            applicant.ethnicity,
            applicant.dataset.DisplayName if applicant.dataset else '',
            applicant.round.DisplayName if applicant.round else ''
        ])

    return response

@login_required
@admin_required
def activity_feed(request):
    activities = Activity.objects.filter(
        action_type__in=[Activity.VOTE_CAST, Activity.COMMENT_ADDED]
    ).select_related('actor', 'target_applicant')[:50]
    context = {
        'activities': activities
    }
    return render(request, "activity_feed.html", context)

@login_required
@admin_required
def update_status(request, pk):
    applicant = get_object_or_404(Applicant, pk=pk)
    if request.method == 'POST':
        form = ApplicantStatusForm(request.POST, instance=applicant)
        if form.is_valid():
            form.save()
            messages.success(request, f"Status for {applicant} updated to {applicant.get_status_display()}.")
    return redirect('applicant_detail', pk=pk)

@login_required
def update_score(request, pk):
    applicant = get_object_or_404(Applicant, pk=pk)
    score, _ = Score.objects.get_or_create(applicant=applicant, voter=request.user)
    if request.method == 'POST':
        form = ScoreForm(request.POST, instance=score)
        if form.is_valid():
            form.save()
            messages.success(request, "Your score has been saved.")
    return redirect('applicant_detail', pk=pk)

@login_required
def applicant_queue(request):
    datasets = DataSet.objects.all()
    selected_dataset_id = request.GET.get('dataset')
    search_query = request.GET.get('q', '')

    user_has_voted_subquery = Vote.objects.filter(
    applicant=OuterRef('pk'),
    voter=request.user
)

    user_batches = request.user.assigned_batches.all()
    
    applicants_list = Applicant.objects.filter(
        round__in=user_batches
    ).exclude( 
        votes__voter=request.user
    ).select_related('dataset', 'round').annotate( 
        avg_score=Avg('scores__overall_score'),
        user_has_voted=Exists(user_has_voted_subquery)
    ).order_by("-created_at")
    
    if selected_dataset_id:
        applicants_list = applicants_list.filter(dataset_id=selected_dataset_id)

    if search_query:
        applicants_list = applicants_list.filter(
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query)
        )
        
    paginator = Paginator(applicants_list, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj, 
        'datasets': datasets,
        'selected_dataset_id': selected_dataset_id,
        'search_query': search_query,
        'is_queue_page': True, 
    }
    return render(request, "applicant_list.html", context)

@login_required
@admin_required
def batch_action(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        applicant_ids = request.POST.getlist('ids')
        
        if not applicant_ids:
            messages.warning(request, "You didn't select any applicants.")
            return redirect('applicant_list')

        queryset = Applicant.objects.filter(pk__in=applicant_ids)
        
        if action == 'set_status_review':
            count = queryset.update(status=Applicant.Status.UNDER_REVIEW)
            messages.success(request, f"Updated {count} applicants to 'Under Review'.")
        elif action == 'set_status_interview':
            count = queryset.update(status=Applicant.Status.INTERVIEW)
            messages.success(request, f"Updated {count} applicants to 'Interview'.")
        elif action == 'set_status_decided':
            count = queryset.update(status=Applicant.Status.DECIDED)
            messages.success(request, f"Updated {count} applicants to 'Decision Made'.")
        
        else:
            messages.error(request, "No valid action selected.")

    return redirect('applicant_list')

@login_required
@admin_required
def batch_assign_reviewers(request, pk):
    batch = get_object_or_404(Batch, pk=pk)
    
    if request.method == 'POST':
        form = BatchAssignmentForm(request.POST, instance=batch)
        if form.is_valid():
            selected_reviewers = form.cleaned_data['reviewers']
            batch.assigned_reviewers.set(selected_reviewers)
            messages.success(request, f"Reviewer assignments updated for {batch.DisplayName}.")
            return redirect('batch_list')
        
        else:
            # If the form is invalid, display errors
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"Error in '{field}': {error}")
                    
    else:
        form = BatchAssignmentForm(instance=batch)

    context = {
        'form': form,
        'batch': batch,
    }
    return render(request, 'batch_assign_reviewers.html', context)