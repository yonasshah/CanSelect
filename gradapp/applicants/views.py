# applicants/views.py
import csv
import random
from django.db.models import Count, Q, Avg, Exists, OuterRef
import json
from itertools import cycle
from django.utils.safestring import mark_safe
from django.http import HttpResponse
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login
from django.contrib import messages
import pypdf
import re
from .decorators import admin_required
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from .models import Activity, Applicant, ApplicantFile, Score, Vote, DataSet, Batch
from .forms import ApplicantForm, ApplicantStatusForm, BatchAssignmentForm, BulkUploadForm, UploadManyFilesForm, EmailLoginForm,DataSetForm, BatchForm, CommentForm, ScoreForm

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

@admin_required
@login_required
def applicant_list(request):
    batches = Batch.objects.all()
    selected_batch_id = request.GET.get('batch')
    search_query = request.GET.get('q', '')
    status_filter = request.GET.get('status', '')
    flagged_only = request.GET.get('flagged_only', '')
    sort = request.GET.get('sort', 'date')
    direction = request.GET.get('dir', 'desc')

    user_has_voted_subquery = Vote.objects.filter(
        applicant=OuterRef('pk'),
        voter=request.user
    )

    applicants_list = Applicant.objects.select_related('dataset', 'round').annotate(
        avg_score=Avg('scores__overall_score'),
        user_has_voted=Exists(user_has_voted_subquery)
    )

    if selected_batch_id:
        applicants_list = applicants_list.filter(round_id=selected_batch_id)

    if search_query:
        applicants_list = applicants_list.filter(
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query)
        )

    if status_filter:
        applicants_list = applicants_list.filter(status=status_filter)

    if flagged_only:
        applicants_list = applicants_list.filter(flagged_by__isnull=False).distinct()

    sort_map = {
        'name': 'last_name',
        'status': 'status',
        'date': 'created_at',
        'score': 'avg_score',
    }
    order_field = sort_map.get(sort, 'created_at')
    if direction == 'asc':
        applicants_list = applicants_list.order_by(order_field)
    else:
        applicants_list = applicants_list.order_by(f'-{order_field}')

    paginator = Paginator(applicants_list, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'paginator_count': paginator.count,
        'batches': batches,
        'selected_batch_id': selected_batch_id,
        'search_query': search_query,
        'status_filter': status_filter,
        'flagged_only': flagged_only,
        'sort': sort,
        'direction': direction,
    }
    return render(request, 'applicant_list.html', context)

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

    if request.user.profile.role == 'ADMIN':
        comments = applicant.comments.select_related('author').all()
    else:
        comments = applicant.comments.filter(author=request.user).select_related('author')

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

    # ── Next / Previous Navigation ────────────────────────────────────────────
    batch_id     = request.GET.get('batch')
    search_q     = request.GET.get('q', '')
    from_queue   = request.GET.get('queue', '')
    from_reviews = request.GET.get('from') == 'reviews'
    from_batch   = request.GET.get('from') == 'batch'
    batch_pk     = request.GET.get('batch_pk')

    if from_reviews:
        assigned_batches = request.user.assigned_batches.all()
        reviewed_ids = Vote.objects.filter(
            voter=request.user,
            applicant__round__in=assigned_batches
        ).values_list('applicant__pk', flat=True)
        nav_qs = Applicant.objects.filter(pk__in=reviewed_ids).order_by('last_name')

    elif from_batch and batch_pk:
        nav_qs = Applicant.objects.filter(round_id=batch_pk).order_by('last_name')

    elif from_queue:
        user_batches = request.user.assigned_batches.all()
        nav_qs = Applicant.objects.filter(
            round__in=user_batches
        ).exclude(
            votes__voter=request.user
        ).order_by('-created_at')

    else:
        nav_qs = Applicant.objects.all().order_by('-created_at')
        if batch_id:
            nav_qs = nav_qs.filter(round_id=batch_id)
        if search_q:
            nav_qs = nav_qs.filter(
                Q(first_name__icontains=search_q) |
                Q(last_name__icontains=search_q)
            )

    nav_ids = list(nav_qs.values_list('pk', flat=True))
    prev_applicant = None
    next_applicant = None
    nav_position = None
    nav_total = len(nav_ids)

    if pk in nav_ids:
        idx = nav_ids.index(pk)
        nav_position = idx + 1
        if idx > 0:
            prev_applicant = Applicant.objects.get(pk=nav_ids[idx - 1])
        if idx < len(nav_ids) - 1:
            next_applicant = Applicant.objects.get(pk=nav_ids[idx + 1])

    # Build nav params to carry forward
    nav_params_parts = []
    if from_reviews:
        nav_params_parts.append('from=reviews')
    elif from_batch and batch_pk:
        nav_params_parts.append(f'from=batch&batch_pk={batch_pk}')
    elif from_queue:
        nav_params_parts.append('queue=1')
    else:
        if batch_id:
            nav_params_parts.append(f'batch={batch_id}')
        if search_q:
            nav_params_parts.append(f'q={search_q}')
    nav_params = '&'.join(nav_params_parts)

    # Back URL
    if from_reviews:
        back_url = '/committee/reviews/'
    elif from_batch and batch_pk:
        back_url = f'/batches/{batch_pk}/'
    elif from_queue:
        back_url = '/queue/'
    elif batch_id or search_q:
        back_url = f'/applicant/?batch={batch_id or ""}&q={search_q}'
    else:
        back_url = '/applicant/'

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
        'prev_applicant': prev_applicant,
        'next_applicant': next_applicant,
        'nav_position': nav_position,
        'nav_total': nav_total,
        'nav_params': nav_params,
        'back_url': back_url,
    }
    return render(request, "applicant_detail.html", context)

@login_required
@admin_required
def applicant_create(request):
    if request.method == "POST":
        a_form = ApplicantForm(request.POST, request.FILES)
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
        form = ApplicantForm(request.POST, request.FILES, instance=applicant)
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
@admin_required
def dataset_list(request):
    search_query = request.GET.get('q', '')
    status_filter = request.GET.get('status', 'all')

    datasets = DataSet.objects.annotate(
        applicant_count=Count('applicants', distinct=True),
        batch_count=Count('batches', distinct=True),
    ).order_by('DisplayName')

    if search_query:
        datasets = datasets.filter(DisplayName__icontains=search_query)
    if status_filter == 'live':
        datasets = datasets.filter(IsLive=True)
    elif status_filter == 'offline':
        datasets = datasets.filter(IsLive=False)

    active_datasets   = datasets.filter(Active=True)
    archived_datasets = datasets.filter(Active=False)

    # Pagination only on active datasets
    paginator = Paginator(active_datasets, 25)
    page_obj  = paginator.get_page(request.GET.get('page'))

    return render(request, "dataset_list.html", {
        'page_obj': page_obj,
        'archived_datasets': archived_datasets,
        'search_query': search_query,
        'status_filter': status_filter,
        'crumbs': [{'label': 'Datasets', 'url': ''}],
    })
    
@login_required
@admin_required
@require_POST
def dataset_archive(request, pk):
    dataset = get_object_or_404(DataSet, pk=pk)
    # Toggle — if active, archive it; if archived, restore it
    dataset.Active = not dataset.Active
    dataset.save()
    if dataset.Active:
        messages.success(request, f"'{dataset.DisplayName}' has been restored.")
    else:
        messages.success(request, f"'{dataset.DisplayName}' has been archived.")
    return redirect('dataset_list')

@login_required
@admin_required
def dataset_create(request):
    if request.method == "POST":
        form = DataSetForm(request.POST)
        if form.is_valid():
            dataset = form.save()
            messages.success(request, f"Dataset '{dataset.DisplayName}' created successfully.")
            return redirect("dataset_detail", pk=dataset.pk)
    else:
        form = DataSetForm()
    return render(request, "dataset_form.html", {
        "form": form,
        "dataset": None,
        "crumbs": [
            {'label': 'Datasets', 'url': '/datasets/'},
            {'label': 'New Dataset', 'url': ''},
        ],
    })



@login_required
@admin_required
def dataset_detail(request, pk):
    dataset = get_object_or_404(DataSet, pk=pk)

    batches = dataset.batches.annotate(
        applicant_count=Count('applicant', distinct=True),
        reviewed_count=Count(
            'applicant__votes',
            filter=Q(applicant__votes__isnull=False),
            distinct=True
        ),
    ).order_by('DisplayName')

    applicant_count = Applicant.objects.filter(dataset=dataset).count()

    return render(request, "dataset_detail.html", {
        "dataset": dataset,
        "batches": batches,
        "applicant_count": applicant_count,
        'crumbs': [
            {'label': 'Datasets', 'url': '/datasets/'},
            {'label': dataset.DisplayName, 'url': ''},
        ],
    })

@login_required
@admin_required
def dataset_edit(request, pk):
    dataset = get_object_or_404(DataSet, pk=pk)
    if request.method == "POST":
        form = DataSetForm(request.POST, instance=dataset)
        if form.is_valid():
            form.save()
            messages.success(request, f"Dataset '{dataset.DisplayName}' updated successfully.")
            return redirect("dataset_detail", pk=dataset.pk)
    else:
        form = DataSetForm(instance=dataset)
    return render(request, "dataset_form.html", {
        "form": form,
        "dataset": dataset,
        "crumbs": [
            {'label': 'Datasets', 'url': '/datasets/'},
            {'label': dataset.DisplayName, 'url': f'/datasets/{dataset.pk}/'},
            {'label': 'Edit', 'url': ''},
        ],
    })


@login_required
@admin_required
def batch_list(request):
    search_query = request.GET.get('q', '')
    status_filter = request.GET.get('status', 'all')

    batches_list = Batch.objects.select_related("DataSet").annotate(
        applicant_count=Count('applicant', distinct=True),
        reviewer_count=Count('assigned_reviewers', distinct=True),
    ).order_by('DataSet__DisplayName', 'DisplayName')

    if search_query:
        batches_list = batches_list.filter(DisplayName__icontains=search_query)
    if status_filter == 'active':
        batches_list = batches_list.filter(Active=True)
    elif status_filter == 'inactive':
        batches_list = batches_list.filter(Active=False)
        
    show_archived = request.GET.get('show_archived')
    if not show_archived:
        batches_list = batches_list.filter(DataSet__Active=True)
        
        
    paginator = Paginator(batches_list, 25)
    page_obj = paginator.get_page(request.GET.get("page"))

    # Run loop on page_obj AFTER pagination
    for batch in page_obj:
        potential = batch.applicant_count * batch.reviewer_count
        actual = Vote.objects.filter(
            applicant__round=batch,
            voter__in=batch.assigned_reviewers.all()
        ).count()
        batch.potential_votes = potential
        batch.actual_votes = actual
        batch.progress_pct = int((actual / potential * 100) if potential > 0 else 0)
        
        

    return render(request, "batch_list.html", {
        'page_obj': page_obj,
        'search_query': search_query,
        'status_filter': status_filter,
        'show_archived': show_archived,
        'crumbs': [{'label': 'Batches', 'url': ''}],
    })

@login_required
@admin_required
def batch_create(request):
    if request.method == "POST":
        form = BatchForm(request.POST)
        if form.is_valid():
            batch = form.save()
            messages.success(request, f"Batch '{batch.DisplayName}' created successfully.")
            return redirect("batch_detail", pk=batch.pk)
    else:
        form = BatchForm()
    return render(request, "batch_form.html", {
        "form": form,
        "batch": None,
        "crumbs": [
            {'label': 'Batches', 'url': '/batches/'},
            {'label': 'New Batch', 'url': ''},
        ],
    })


@admin_required
@login_required
def batch_detail(request, pk):
    batch = get_object_or_404(Batch, pk=pk)
    search_query = request.GET.get('q', '')

    applicants = Applicant.objects.filter(round=batch).annotate(
        avg_score=Avg('scores__overall_score'),
        vote_count=Count('votes')
    ).order_by('last_name')

    if search_query:
        applicants = applicants.filter(
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query)
        )

    assigned_reviewers = batch.assigned_reviewers.all()
    reviewer_count = assigned_reviewers.count()
    actual_votes = Vote.objects.filter(
        applicant__round=batch,
        voter__in=assigned_reviewers
    ).count()
    total = applicants.count()
    potential_votes = total * reviewer_count
    progress_pct = round((actual_votes / potential_votes) * 100) if potential_votes > 0 else 0
    pending = potential_votes - actual_votes

    context = {
        'batch': batch,
        'applicants': applicants,
        'total': total,
        'reviewer_count': reviewer_count,
        'actual_votes': actual_votes,
        'pending': pending,
        'progress_pct': progress_pct,
        'search_query': search_query,
        'crumbs': [
            {'label': 'Batches', 'url': '/batches/'},
            {'label': batch.DisplayName},
        ],
    }
    return render(request, 'batch_detail.html', context)

@login_required
@admin_required
def batch_edit(request, pk):
    batch = get_object_or_404(Batch, pk=pk)
    if request.method == "POST":
        form = BatchForm(request.POST, instance=batch)
        if form.is_valid():
            form.save()
            messages.success(request, f"Batch '{batch.DisplayName}' updated successfully.")
            return redirect("batch_detail", pk=batch.pk)
    else:
        form = BatchForm(instance=batch)
    return render(request, "batch_form.html", {
        "form": form,
        "batch": batch,
        "crumbs": [
            {'label': 'Batches', 'url': '/batches/'},
            {'label': batch.DisplayName, 'url': f'/batches/{batch.pk}/'},
            {'label': 'Edit', 'url': ''},
        ],
    })

@login_required
@admin_required
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
@admin_required
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


@admin_required
@login_required
def export_applicants_csv(request):
    selected_dataset_id = request.GET.get('dataset')
    selected_batch_id = request.GET.get('batch')

    applicants = Applicant.objects.all().order_by("-created_at")

    if selected_batch_id:
        applicants = applicants.filter(round_id=selected_batch_id)
    elif selected_dataset_id:
        applicants = applicants.filter(dataset_id=selected_dataset_id)

    response = HttpResponse(
        content_type='text/csv',
        headers={'Content-Disposition': 'attachment; filename="applicants.csv"'},
    )
    writer = csv.writer(response)
    writer.writerow(['First Name', 'Last Name', 'Email', 'Age', 'Gender', 'Ethnicity', 'Status', 'Dataset', 'Round'])
    for applicant in applicants:
        writer.writerow([
            applicant.first_name,
            applicant.last_name,
            applicant.email,
            applicant.age,
            applicant.gender,
            applicant.ethnicity,
            applicant.get_status_display(),
            applicant.dataset.DisplayName if applicant.dataset else '',
            applicant.round.DisplayName if applicant.round else '',
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
    batches = request.user.assigned_batches.all()
    selected_batch_id = request.GET.get('batch')
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
    
    if selected_batch_id:
        applicants_list = applicants_list.filter(round_id=selected_batch_id)

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
        'batches': batches,
        'selected_batch_id': selected_batch_id,
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

@login_required
@require_POST
def toggle_applicant_flag(request, pk):
    applicant = get_object_or_404(Applicant, pk=pk)

    if request.user in applicant.flagged_by.all():
        applicant.flagged_by.remove(request.user)
        Activity.objects.create(
            actor=request.user,
            action_type=Activity.FLAG_ADDED,
            details="unflagged",
            target_applicant=applicant
        )
        messages.success(request, "Your flag has been removed.")
    else:
        applicant.flagged_by.add(request.user)
        Activity.objects.create(
            actor=request.user,
            action_type=Activity.FLAG_ADDED,
            details="flagged",
            target_applicant=applicant
        )
        messages.success(request, f"You flagged {applicant.first_name} for discussion.")

    return redirect('applicant_detail', pk=applicant.pk)

@login_required
@admin_required
def bulk_upload_applicants(request):
    if request.method == "POST":
        files = request.FILES.getlist('folder_files')
        path_map_raw = request.POST.get('path_map')

        if path_map_raw:
            import json
            path_map = json.loads(path_map_raw)
            candidate_groups = {}
            for i, f in enumerate(files):
                relative_path = path_map.get(str(i), f.name)
                parts = relative_path.replace('\\', '/').split('/')
                if len(parts) < 3:
                    continue
                candidate_folder = parts[1]
                candidate_groups.setdefault(candidate_folder, []).append(f)
        else:
            candidate_groups = {}
            for f in files:
                parts = f.name.replace('\\', '/').split('/')
                if len(parts) < 3:
                    continue
                candidate_folder = parts[1]
                candidate_groups.setdefault(candidate_folder, []).append(f)



        # Optional dataset / batch assignment from the form
        dataset_id = request.POST.get('dataset_id') or None
        batch_id   = request.POST.get('batch_id')   or None

        selected_dataset = None
        selected_batch   = None
        if dataset_id:
            try:
                selected_dataset = DataSet.objects.get(pk=dataset_id)
            except DataSet.DoesNotExist:
                pass
        if batch_id:
            try:
                selected_batch = Batch.objects.get(pk=batch_id)
            except Batch.DoesNotExist:
                pass

        if not candidate_groups:
            messages.warning(
                request,
                "No candidate subfolders found. Make sure your structure is: "
                "BatchFolder → CandidateFolder → files."
            )
            return redirect('bulk_upload')

        created_count = 0
        skipped_count = 0

        for folder_name, group_files in candidate_groups.items():
            # ── Parse "Last, First - UniqueID" ───────────────────────────
            import re as _re
            match = _re.match(r'^(.+?)\s*-\s*(\w+)$', folder_name.strip())
            if match:
                raw_name = match.group(1).strip()   # "Ali, Huzaifa"
                unique_id = match.group(2).strip()  # "6815031005"
                # Name may be "LastName, FirstName" or just "FirstName LastName"
                if ',' in raw_name:
                    name_parts = [p.strip() for p in raw_name.split(',', 1)]
                    last_name  = name_parts[0]
                    first_name = name_parts[1] if len(name_parts) > 1 else ''
                else:
                    words      = raw_name.split()
                    first_name = words[0] if words else folder_name
                    last_name  = ' '.join(words[1:]) if len(words) > 1 else ''
            else:
                # Fallback: use whole folder name
                first_name = folder_name
                last_name  = 'Bulk'
                unique_id  = ''

            # ── Extract email from any PDF in the group ──────────────────
            email = None
            for f in group_files:
                if f.name.lower().endswith('.pdf'):
                    try:
                        import pypdf, io
                        reader = pypdf.PdfReader(f)
                        text = ''.join(page.extract_text() or '' for page in reader.pages)
                        email_match = _re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
                        if email_match:
                            email = email_match.group(0)
                        f.seek(0)   # reset file pointer after reading
                    except Exception as e:
                        print(f"PDF parse error for {f.name}: {e}")
                    break   # only check the first PDF

            # ── Create Applicant ─────────────────────────────────────────
            jpg_files = [f for f in group_files if f.name.lower().endswith(('.jpg', '.jpeg'))]
            profile_pic = jpg_files[2] if len(jpg_files) >= 3 else None

            applicant = Applicant.objects.create(
                first_name=first_name,
                last_name=last_name,
                email=email,
                age=0,                      # placeholder — can be edited later
                gender='Not Specified',     # placeholder
                dataset=selected_dataset,
                round=selected_batch,
                external_id=unique_id or None,
                profile_picture=profile_pic,
            )

            # ── Attach all files ─────────────────────────────────────────
            for f in group_files:
                ApplicantFile.objects.create(applicant=applicant, file=f)

            created_count += 1

        messages.success(
            request,
            f"✅ Successfully imported {created_count} candidate profile(s). "
            f"Review each profile to complete missing details like age and gender."
        )
        return redirect('applicant_list')

    # GET — render the upload page
    datasets = DataSet.objects.all().order_by('DisplayName')
    batches  = Batch.objects.select_related('DataSet').order_by('DisplayName')
    return render(request, "bulk_upload.html", {
        "datasets": datasets,
        "batches": batches,
    })
    
    
@login_required
def committee_dashboard(request):
    user = request.user

    # Get batches assigned to this reviewer
    assigned_batches = Batch.objects.filter(
        assigned_reviewers=user
    ).select_related('DataSet').annotate(
        applicant_count=Count('applicant', distinct=True),
        reviewer_count=Count('assigned_reviewers', distinct=True),
    )

    # Calculate progress for each batch
    for batch in assigned_batches:
        potential = batch.applicant_count * batch.reviewer_count
        actual = Vote.objects.filter(
            applicant__round=batch,
            voter=user
        ).count()
        batch.my_votes = actual
        batch.total_candidates = batch.applicant_count
        batch.progress_pct = int((actual / batch.applicant_count * 100) if batch.applicant_count > 0 else 0)

    # Candidates still pending a vote from this user
    pending_count = Applicant.objects.filter(
        round__in=assigned_batches
    ).exclude(
        votes__voter=user
    ).distinct().count()

    # Their recent activity
    recent_activity = Activity.objects.filter(
        actor=user
    ).select_related('target_applicant')[:5]

    # Their total votes and scores
    total_votes = Vote.objects.filter(voter=user).count()
    total_scores = Score.objects.filter(voter=user).count()

    return render(request, "committee_dashboard.html", {
        'assigned_batches': assigned_batches,
        'pending_count': pending_count,
        'recent_activity': recent_activity,
        'total_votes': total_votes,
        'total_scores': total_scores,
    })


@login_required
def my_reviews(request):
    user = request.user

    # Get all votes cast by this user on candidates in their assigned batches
    assigned_batches = Batch.objects.filter(assigned_reviewers=user)
    
    votes = Vote.objects.filter(
        voter=user,
        applicant__round__in=assigned_batches
    ).select_related('applicant', 'applicant__round', 'applicant__dataset').order_by('-created_at')

    scores = Score.objects.filter(
        voter=user,
        applicant__round__in=assigned_batches
    ).select_related('applicant', 'applicant__round').order_by('-updated_at')

    # Combine into a single dict keyed by applicant for easy display
    applicant_pks = set(
        list(votes.values_list('applicant__pk', flat=True)) +
        list(scores.values_list('applicant__pk', flat=True))
    )

    applicants = Applicant.objects.filter(pk__in=applicant_pks).select_related('round', 'dataset')

    reviews = []
    for applicant in applicants:
        vote = votes.filter(applicant=applicant).first()
        score = scores.filter(applicant=applicant).first()
        reviews.append({
            'applicant': applicant,
            'vote': vote,
            'score': score,
        })

    # Sort by applicant last name
    reviews.sort(key=lambda x: x['applicant'].last_name)

    return render(request, "my_reviews.html", {
        'reviews': reviews,
        'total_votes': votes.count(),
        'total_scores': scores.count(),
    })


@login_required
def my_activity(request):
    user = request.user

    activities = Activity.objects.filter(
        actor=user,
        action_type__in=[Activity.VOTE_CAST, Activity.COMMENT_ADDED, Activity.FLAG_ADDED]
    ).select_related('target_applicant').order_by('-created_at')

    paginator = Paginator(activities, 25)
    page_obj = paginator.get_page(request.GET.get('page'))

    return render(request, "my_activity.html", {
        'page_obj': page_obj,
    })
    
@login_required
@admin_required
def batch_bulk_action(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        batch_ids = request.POST.getlist('ids')

        if not batch_ids:
            messages.warning(request, "You didn't select any batches.")
            return redirect('batch_list')

        # A batch action updates the status of all APPLICANTS in those batches
        from .models import Applicant
        if action == 'set_status_review':
            count = Applicant.objects.filter(round_id__in=batch_ids).update(status=Applicant.Status.UNDER_REVIEW)
            messages.success(request, f"Updated {count} applicants to 'Under Review'.")
        elif action == 'set_status_interview':
            count = Applicant.objects.filter(round_id__in=batch_ids).update(status=Applicant.Status.INTERVIEW)
            messages.success(request, f"Updated {count} applicants to 'Interview'.")
        elif action == 'set_status_decided':
            count = Applicant.objects.filter(round_id__in=batch_ids).update(status=Applicant.Status.DECIDED)
            messages.success(request, f"Updated {count} applicants to 'Decision Made'.")
        else:
            messages.error(request, "No valid action selected.")

    return redirect('batch_list')
