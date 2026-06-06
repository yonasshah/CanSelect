# applicants/views.py
import csv
import random
from django.db.models import Avg, Case, Count, Exists, F, IntegerField, OuterRef, Q, Value, When
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
import pandas as pd
from decimal import Decimal, InvalidOperation
import pypdf
import re
from .decorators import admin_required
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from .models import Activity, Applicant, ApplicantFile, Notification, NotificationAttachment, Score, Vote, DataSet, Batch
from .forms import ApplicantForm, ApplicantStatusForm, BatchAssignmentForm, BulkUploadForm, ReviewerGroupForm, SendNotificationForm, UploadManyFilesForm, EmailLoginForm,DataSetForm, BatchForm, CommentForm, ScoreForm

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
    search_query      = request.GET.get('q', '')
    status_filter     = request.GET.get('status', '')
    flagged_only      = request.GET.get('flagged_only', '')
    sort              = request.GET.get('sort', 'date')
    direction         = request.GET.get('dir', 'desc')
    program_type      = request.GET.get('program_type', '')
    application_system  = request.GET.get('application_system', '')

    # ── Candidate-info boolean filters ──────────────────────────────
    filter_first_gen      = request.GET.get('first_gen', '')
    filter_re_applicant   = request.GET.get('re_applicant', '')
    filter_pb_to_dmd      = request.GET.get('pb_to_dmd', '')
    filter_former_pb      = request.GET.get('former_post_bacc', '')
    filter_three_plus_four = request.GET.get('three_plus_four', '')

    has_advanced = any([
        program_type, application_system, filter_first_gen, filter_re_applicant,
        filter_pb_to_dmd, filter_former_pb, filter_three_plus_four,
    ])

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
            Q(last_name__icontains=search_query) |
            Q(external_id__icontains=search_query)
        )

    if status_filter:
        applicants_list = applicants_list.filter(status=status_filter)

    if flagged_only:
        applicants_list = applicants_list.filter(flagged_by__isnull=False).distinct()

    if application_system:
        applicants_list = applicants_list.filter(dataset__application_system=application_system)
        
    if program_type:
        applicants_list = applicants_list.filter(dataset__program_type=program_type)

    if filter_first_gen:
        applicants_list = applicants_list.filter(first_gen=True)
    if filter_re_applicant:
        applicants_list = applicants_list.filter(re_applicant=True)
    if filter_pb_to_dmd:
        applicants_list = applicants_list.filter(pb_to_dmd=True)
    if filter_former_pb:
        applicants_list = applicants_list.filter(former_post_bacc=True)
    if filter_three_plus_four:
        applicants_list = applicants_list.filter(three_plus_four=True)

    sort_map = {
        'name':   'last_name',
        'status': 'status',
        'date':   'created_at',
        'score':  'avg_score',
    }
    order_field = sort_map.get(sort, 'created_at')
    if direction == 'asc':
        applicants_list = applicants_list.order_by(order_field)
    else:
        applicants_list = applicants_list.order_by(f'-{order_field}')

    paginator = Paginator(applicants_list, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # ── Progress bar reflects filtered queryset ──────────────────────
    # For the queue progress strip: count against filtered set when
    # filters are active, otherwise all assigned.
    total_filtered = paginator.count

    context = {
        'page_obj':            page_obj,
        'paginator_count':     total_filtered,
        'batches':             batches,
        'selected_batch_id':   selected_batch_id,
        'search_query':        search_query,
        'status_filter':       status_filter,
        'flagged_only':        flagged_only,
        'sort':                sort,
        'direction':           direction,
        'program_type':        program_type,
        'application_system':    application_system,
        'filter_first_gen':       filter_first_gen,
        'filter_re_applicant':    filter_re_applicant,
        'filter_pb_to_dmd':       filter_pb_to_dmd,
        'filter_former_pb':       filter_former_pb,
        'filter_three_plus_four': filter_three_plus_four,
        'has_advanced':        has_advanced,
        'program_type_choices': DataSet.ProgramType.choices,
        'application_system_choices': DataSet.ApplicationSystem.choices,
    }
    return render(request, 'applicant_list.html', context)

@login_required
def applicant_detail(request, pk):
    applicant = get_object_or_404(Applicant, pk=pk)
    user_score = Score.objects.filter(applicant=applicant, voter=request.user).first()
    score_form = ScoreForm(instance=user_score)
    status_form = ApplicantStatusForm(instance=applicant)

    avg_scores = Score.objects.filter(applicant=applicant).aggregate(
        avg_research=Avg('research_score'),
        avg_statement=Avg('statement_score'),
        avg_overall=Avg('overall_score')
    )

    comment_form = CommentForm()
    current_user_vote = Vote.objects.filter(applicant=applicant, voter=request.user).first()
    
    v_options = [
        ('1', 'Accept', 'btn-success' if current_user_vote and current_user_vote.value == 1 else 'btn-outline-success'),
        ('-1', 'Deny', 'btn-danger' if current_user_vote and current_user_vote.value == -1 else 'btn-outline-danger'),
        ('0', 'Wait', 'btn-warning' if current_user_vote and current_user_vote.value == 0 else 'btn-outline-warning'),
    ]

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
    
    # Categorize files into organized sections
    video_files = []
    application_docs = []
    evaluation_docs = []
    photo_files = []
    other_docs = []
    
    for f in all_files:
        fname = f.file.name.lower()
        if f.is_video:
            video_files.append(f)
        elif fname.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
            photo_files.append(f)
        elif 'application' in fname and fname.endswith('.pdf'):
            application_docs.append(f)
        elif 'evaluation' in fname and fname.endswith('.pdf'):
            evaluation_docs.append(f)
        else:
            other_docs.append(f)

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
        'application_docs': application_docs,
        'evaluation_docs': evaluation_docs,
        'photo_files': photo_files,
        'other_docs': other_docs,
        'score_form': score_form,
        'status_form': status_form,
        'avg_scores': avg_scores,
        'prev_applicant': prev_applicant,
        'next_applicant': next_applicant,
        'nav_position': nav_position,
        'nav_total': nav_total,
        'nav_params': nav_params,
        'back_url': back_url,
        'v_options': v_options,
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


from django.utils.safestring import mark_safe # REQUIRED

def vote(request, pk, value):
    if request.method == "POST":
        applicant = get_object_or_404(Applicant, pk=pk)
        Vote.objects.update_or_create(
            applicant=applicant, voter=request.user, defaults={"value": int(value)}
        )
        
        next_id = request.POST.get('next_id')
        nav_params = request.POST.get('nav_params', '')
        
        if next_id and next_id != 'None':
            # Create a clickable banner to advance
            link = f'<a href="/applicant/{next_id}/?{nav_params}" class="alert-link ms-2">Go to Next Candidate &raquo;</a>'
            messages.success(request, mark_safe(f"Vote recorded for {applicant.first_name}. {link}"))
        else:
            back_url = request.POST.get('back_url', '/applicant/')
            link = f'<a href="{back_url}" class="alert-link ms-2">Return to List</a>'
            messages.success(request, mark_safe(f"Vote recorded. End of queue. {link}"))
            
    return redirect('applicant_detail', pk=pk)

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
    candidate_search = request.GET.get('candidate', '').strip()

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

    # ── Candidate search across all datasets ─────────────────────────────
    candidate_results = None
    if candidate_search:
        candidate_results = list(
            Applicant.objects.select_related('dataset', 'round')
            .filter(
                Q(first_name__icontains=candidate_search) |
                Q(last_name__icontains=candidate_search) |
                Q(external_id__icontains=candidate_search)
            )
            .order_by('last_name', 'first_name')[:50]
        )

        # Count how many distinct datasets each name appears in
        # Group by normalized name to catch the same person across datasets
        from collections import Counter
        name_counts = Counter()
        for a in candidate_results:
            key = f"{a.last_name.lower().strip()},{a.first_name.lower().strip()}"
            name_counts[key] += 1

        # Also count by external_id if present
        id_counts = Counter()
        for a in candidate_results:
            if a.external_id:
                id_counts[a.external_id] += 1

        # Attach appearance_count to each result
        for a in candidate_results:
            key = f"{a.last_name.lower().strip()},{a.first_name.lower().strip()}"
            by_name = name_counts.get(key, 1)
            by_id = id_counts.get(a.external_id, 1) if a.external_id else 1
            a.appearance_count = max(by_name, by_id)

    return render(request, "dataset_list.html", {
        'page_obj': page_obj,
        'archived_datasets': archived_datasets,
        'search_query': search_query,
        'status_filter': status_filter,
        'candidate_search': candidate_search,
        'candidate_results': candidate_results,
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

# update 

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

    # Prefetch candidates for each batch with their avg scores
    for batch in batches:
        batch.candidates = (
            Applicant.objects.filter(round=batch)
            .annotate(avg_score=Avg('scores__overall_score'))
            .order_by('last_name', 'first_name')
        )

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
def dataset_decisions(request, pk):
    dataset = get_object_or_404(DataSet, pk=pk)

    THRESHOLD_OPTIONS = [
        (60,  '60%+'),
        (67,  '67%+'),
        (75,  '75%+'),
        (80,  '80%+'),
        (100, '100% (unanimous)'),
    ]
    threshold = int(request.GET.get('threshold', 80))
    if threshold not in [v for v, _ in THRESHOLD_OPTIONS]:
        threshold = 80
    threshold_display = next(label for val, label in THRESHOLD_OPTIONS if val == threshold)

    # Class size cap — URL param overrides stored dataset value
    cap_param = request.GET.get('cap', '')
    if cap_param.isdigit():
        cap = int(cap_param)
    elif dataset.target_class_size:
        cap = dataset.target_class_size
    else:
        cap = None

    applicants = (
        Applicant.objects.filter(dataset=dataset)
        .select_related('round')
        .prefetch_related('flagged_by')
        .annotate(
            avg_score=Avg('scores__overall_score'),
            accept_ct=Count('votes', filter=Q(votes__value=1),  distinct=True),
            deny_ct=Count('votes',   filter=Q(votes__value=-1), distinct=True),
            wait_ct=Count('votes',   filter=Q(votes__value=0),  distinct=True),
            total_votes=Count('votes', distinct=True),
        )
    )

    FINAL_STATUSES = {'ACCEPTED', 'ACCEPTED_MAILED', 'ACCEPTED_NOT_MAILED', 'REJECTED', 'WAITLISTED', 'DECLINED'}
    ACCEPTED_STATUSES = {'ACCEPTED', 'ACCEPTED_MAILED', 'ACCEPTED_NOT_MAILED'}

    clear_accepts    = []
    clear_denies     = []
    split_candidates = []
    no_votes         = []

    for a in applicants:
        a.is_final   = a.status in FINAL_STATUSES
        a.is_flagged = a.flagged_by.exists()

        if a.total_votes == 0:
            a.accept_pct = 0
            a.deny_pct   = 0
            no_votes.append(a)
            continue

        a.accept_pct = round(a.accept_ct / a.total_votes * 100)
        a.deny_pct   = round(a.deny_ct   / a.total_votes * 100)

        if a.accept_pct >= threshold:
            clear_accepts.append(a)
        elif a.deny_pct >= threshold:
            clear_denies.append(a)
        else:
            split_candidates.append(a)

    # Sort by vote % descending, ties broken by avg score descending
    clear_accepts.sort(key=lambda a: (-(a.accept_pct), -(a.avg_score or 0)))
    clear_denies.sort(key=lambda a:  (-(a.deny_pct),   -(a.avg_score or 0)))

    # Split: flagged float to top, then sort by accept %
    split_candidates.sort(key=lambda a: (not a.is_flagged, -(a.accept_pct), -(a.avg_score or 0)))

    split_flagged_count = sum(1 for a in split_candidates if a.is_flagged)

    # Batch readiness — which batches still have outstanding votes
    batches = dataset.batches.annotate(
        applicant_count=Count('applicant', distinct=True),
        reviewer_count=Count('assigned_reviewers', distinct=True),
    )
    pending_batches  = []
    complete_batches = []
    for b in batches:
        potential = b.applicant_count * b.reviewer_count
        actual    = Vote.objects.filter(
            applicant__round=b,
            voter__in=b.assigned_reviewers.all()
        ).count()
        b.votes_remaining = potential - actual
        if b.votes_remaining > 0:
            pending_batches.append(b)
        else:
            complete_batches.append(b)

    all_candidates   = clear_accepts + clear_denies + split_candidates + no_votes
    total            = len(all_candidates)
    accepted_count   = sum(1 for a in all_candidates if a.status in ACCEPTED_STATUSES)
    rejected_count   = sum(1 for a in all_candidates if a.status == 'REJECTED')
    waitlisted_count = sum(1 for a in all_candidates if a.status == 'WAITLISTED')
    decided_count    = sum(1 for a in all_candidates if a.status in FINAL_STATUSES)
    undecided_count  = total - decided_count
    spots_remaining  = (cap - accepted_count) if cap else None

    decided_pct    = round(decided_count    / total * 100) if total > 0 else 0
    accepted_pct   = round(accepted_count   / total * 100) if total > 0 else 0
    rejected_pct   = round(rejected_count   / total * 100) if total > 0 else 0
    waitlisted_pct = round(waitlisted_count / total * 100) if total > 0 else 0

    # Decision log — most recent 50 decisions for this dataset
    decision_log = Activity.objects.filter(
        action_type=Activity.DECISION_MADE,
        target_applicant__dataset=dataset,
    ).select_related('actor', 'target_applicant').order_by('-created_at')[:50]

    return render(request, 'dataset_decisions.html', {
        'dataset':             dataset,
        'clear_accepts':       clear_accepts,
        'clear_denies':        clear_denies,
        'split_candidates':    split_candidates,
        'split_flagged_count': split_flagged_count,
        'no_votes':            no_votes,
        'pending_batches':     pending_batches,
        'complete_batches':    complete_batches,
        'total':               total,
        'accepted_count':      accepted_count,
        'rejected_count':      rejected_count,
        'waitlisted_count':    waitlisted_count,
        'decided_count':       decided_count,
        'undecided_count':     undecided_count,
        'decided_pct':         decided_pct,
        'accepted_pct':        accepted_pct,
        'rejected_pct':        rejected_pct,
        'waitlisted_pct':      waitlisted_pct,
        'threshold':           threshold,
        'threshold_display':   threshold_display,
        'threshold_options':   THRESHOLD_OPTIONS,
        'cap':                 cap,
        'spots_remaining':     spots_remaining,
        'decision_log':        decision_log,
        'crumbs': [
            {'label': 'Datasets',          'url': '/datasets/'},
            {'label': dataset.DisplayName, 'url': f'/datasets/{dataset.pk}/'},
            {'label': 'Final Decisions',   'url': ''},
        ],
    })


@login_required
@admin_required
@require_POST
def dataset_decisions_action(request, pk):
    dataset   = get_object_or_404(DataSet, pk=pk)
    action    = request.POST.get('action')
    ids       = request.POST.getlist('ids')
    threshold = request.POST.get('threshold', '80')
    cap       = request.POST.get('cap', '')

    if not ids:
        messages.warning(request, "No candidates selected.")
        return redirect('dataset_decisions', pk=pk)

    # Safety: only update applicants that belong to this dataset
    qs = Applicant.objects.filter(pk__in=ids, dataset=dataset)

    STATUS_MAP = {
        'set_accepted':   (Applicant.Status.ACCEPTED,   'accepted'),
        'set_rejected':   (Applicant.Status.REJECTED,   'rejected'),
        'set_waitlisted': (Applicant.Status.WAITLISTED, 'waitlisted'),
    }

    if action in STATUS_MAP:
        new_status, label = STATUS_MAP[action]
        updated = list(qs)
        qs.update(status=new_status)

        # Log each decision individually for FERPA compliance
        for applicant in updated:
            Activity.objects.create(
                actor=request.user,
                action_type=Activity.DECISION_MADE,
                details=label,
                target_applicant=applicant,
            )

        messages.success(request, f"Marked {len(updated)} candidate(s) as {label}.")
    else:
        messages.error(request, "Invalid action.")

    params = f"threshold={threshold}"
    if cap:
        params += f"&cap={cap}"
    return redirect(f'/datasets/{pk}/decisions/?{params}')
 
 
@login_required
@admin_required
def export_decisions_csv(request, pk):
    dataset = get_object_or_404(DataSet, pk=pk)
 
    applicants = (
        Applicant.objects.filter(dataset=dataset)
        .select_related('round')
        .annotate(
            accept_ct=Count('votes', filter=Q(votes__value=1),  distinct=True),
            deny_ct=Count('votes',   filter=Q(votes__value=-1), distinct=True),
            wait_ct=Count('votes',   filter=Q(votes__value=0),  distinct=True),
            avg_score=Avg('scores__overall_score'),
        )
        .filter(status__in=['ACCEPTED', 'REJECTED', 'WAITLISTED'])
        .order_by('status', 'last_name', 'first_name')
    )
 
    response = HttpResponse(
        content_type='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{dataset.DisplayName} - Decisions.csv"'},
    )
    writer = csv.writer(response)
    writer.writerow([
        'Last Name', 'First Name', 'Email', 'External ID',
        'Batch', 'Decision',
        'Accept Votes', 'Deny Votes', 'Waitlist Votes',
        'Avg Score',
    ])
    for a in applicants:
        writer.writerow([
            a.last_name,
            a.first_name,
            a.email or '',
            a.external_id or '',
            a.round.DisplayName if a.round else '',
            a.get_status_display(),
            a.accept_ct,
            a.deny_ct,
            a.wait_ct,
            f"{a.avg_score:.1f}" if a.avg_score else '',
        ])
    return response

@login_required
@admin_required
def batch_list(request):
    search_query = request.GET.get('q', '')
    status_filter = request.GET.get('status', 'all')
    group_filter = request.GET.get('group', '')

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

    if group_filter == 'none':
        batches_list = batches_list.filter(review_group='')
    elif group_filter:
        batches_list = batches_list.filter(review_group=group_filter)
        
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
        'group_filter': group_filter,
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
 
    assigned_reviewer_ids = batch.assigned_reviewers.values_list('pk', flat=True)

    applicants = Applicant.objects.filter(round=batch).annotate(
        avg_score=Avg('scores__overall_score'),
        vote_count=Count(
            'votes',
            filter=Q(votes__voter_id__in=assigned_reviewer_ids)
        )
    ).order_by('last_name')
 
    if search_query:
        applicants = applicants.filter(
            Q(first_name__icontains=search_query) |
            Q(last_name__icontains=search_query) |
            Q(external_id__icontains=search_query)
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
 
    # ── Per-reviewer progress ────────────────────────────────────────────
    reviewer_progress = []
    for reviewer in assigned_reviewers.select_related('profile'):
        votes_cast = Vote.objects.filter(
            applicant__round=batch,
            voter=reviewer,
        ).count()
        pct = round((votes_cast / total) * 100) if total > 0 else 0
        reviewer_progress.append({
            'user': reviewer,
            'votes_cast': votes_cast,
            'total': total,
            'pct': pct,
        })
    reviewer_progress.sort(key=lambda r: r['pct'], reverse=True)
 
    # ── Group applicants by source_folder for display ────────────────────
    from collections import OrderedDict
    grouped_applicants = OrderedDict()
    for a in applicants:
        folder = a.source_folder or ''
        grouped_applicants.setdefault(folder, []).append(a)
 
    has_multiple_groups = len(grouped_applicants) > 1 or (len(grouped_applicants) == 1 and '' not in grouped_applicants)
 
    context = {
        'batch': batch,
        'applicants': applicants,
        'grouped_applicants': grouped_applicants,
        'has_multiple_groups': has_multiple_groups,
        'total': total,
        'reviewer_count': reviewer_count,
        'actual_votes': actual_votes,
        'pending': pending,
        'progress_pct': progress_pct,
        'search_query': search_query,
        'reviewer_progress': reviewer_progress,
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
    old_group = batch.review_group
    if request.method == "POST":
        form = BatchForm(request.POST, instance=batch)
        if form.is_valid():
            batch = form.save()

            # If the group changed, sync reviewers to the new group
            if batch.review_group != old_group and batch.review_group:
                from .models import Profile
                group_members = User.objects.filter(
                    profile__role=Profile.Role.COMMITTEE_MEMBER,
                    profile__review_group=batch.review_group,
                )
                batch.assigned_reviewers.set(group_members)

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
            action_type__in=[Activity.VOTE_CAST, Activity.COMMENT_ADDED, Activity.DECISION_MADE]
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
 
    applicants = Applicant.objects.filter(pk__in=applicant_ids).select_related('dataset', 'round')
 
    # Annotate average scores for each applicant
    for applicant in applicants:
        applicant.avg_scores = Score.objects.filter(applicant=applicant).aggregate(
            avg_research=Avg('research_score'),
            avg_statement=Avg('statement_score'),
            avg_overall=Avg('overall_score'),
        )
 
    column_count = len(applicants)
    col_class = f"col-md-{12 // column_count if column_count > 0 else 12}"
 
    context = {
        'applicants': applicants,
        'col_class': col_class,
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
    writer.writerow([
        'First Name', 'Last Name', 'Email', 'External ID', 'Age', 'Gender',
        'Status', 'Dataset', 'Round',
        'Total AI', 'Total NC', 'Z-Score', 'First Gen', 'Re-Applicant',
        'PB to DMD', 'Former Post Bacc', '3+4',
    ])
    for applicant in applicants:
        writer.writerow([
            applicant.first_name,
            applicant.last_name,
            applicant.email,
            applicant.external_id or '',
            applicant.age,
            applicant.gender,
            applicant.get_status_display(),
            applicant.dataset.DisplayName if applicant.dataset else '',
            applicant.round.DisplayName if applicant.round else '',
            applicant.total_ai if applicant.total_ai is not None else '',
            applicant.total_nc if applicant.total_nc is not None else '',
            applicant.z_score if applicant.z_score is not None else '',
            'Yes' if applicant.first_gen else 'No',
            'Yes' if applicant.re_applicant else 'No',
            'Yes' if applicant.pb_to_dmd else 'No',
            'Yes' if applicant.former_post_bacc else 'No',
            'Yes' if applicant.three_plus_four else 'No',
        ])
    return response

@login_required
@admin_required
def activity_feed(request):
    activities = Activity.objects.filter(
        action_type__in=[Activity.VOTE_CAST, Activity.COMMENT_ADDED, Activity.DECISION_MADE]
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

    # --- PROGRESS CALCULATION ---
    # Total applicants assigned to this user across all their batches
    total_assigned = Applicant.objects.filter(round__in=batches).count()
    
    # Total applicants the user has already voted for in those batches
    voted_count = Vote.objects.filter(
        voter=request.user, 
        applicant__round__in=batches
    ).count()

    # Calculate percentage (prevent division by zero)
    progress_pct = int((voted_count / total_assigned * 100)) if total_assigned > 0 else 0
    # ----------------------------

    user_has_voted_subquery = Vote.objects.filter(
        applicant=OuterRef('pk'),
        voter=request.user
    )

    applicants_list = Applicant.objects.filter(
        round__in=batches
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
            Q(last_name__icontains=search_query) |
            Q(external_id__icontains=search_query)
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
        'voted_count': voted_count,
        'total_assigned': total_assigned,
        'progress_pct': progress_pct,
        'program_type':           '',
        'application_system':     '',
        'filter_first_gen':       '',
        'filter_re_applicant':    '',
        'filter_pb_to_dmd':       '',
        'filter_former_pb':       '',
        'filter_three_plus_four': '',
        'has_advanced':           False,
        'program_type_choices':   DataSet.ProgramType.choices,
        'application_system_choices': DataSet.ApplicationSystem.choices,
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
        
        if action == 'set_status_interview':
            count = queryset.update(status=Applicant.Status.INTERVIEW)
            messages.success(request, f"Updated {count} applicants to 'Interview'.")
        elif action == 'set_status_interview_complete':
            count = queryset.update(status=Applicant.Status.INTERVIEW_COMPLETE)
            messages.success(request, f"Updated {count} applicants to 'Interview Complete'.")
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
    
    # Admins can clear every reviewer's flag on this candidate.
    if request.POST.get('clear_all') and request.user.profile.role == 'ADMIN':
        applicant.flagged_by.clear()
        Activity.objects.create(
            actor=request.user,
            action_type=Activity.FLAG_ADDED,
            details="cleared all flags on",
            target_applicant=applicant,
        )
        messages.success(request, "All flags cleared for this candidate.")
        return redirect('applicant_detail', pk=applicant.pk)

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
            folder_name_map = {}  # candidate_folder -> top-level batch folder name
            for i, f in enumerate(files):
                relative_path = path_map.get(str(i), f.name)
                parts = relative_path.replace('\\', '/').split('/')
                if len(parts) < 3:
                    continue
                batch_folder = parts[0]
                candidate_folder = parts[1]
                candidate_groups.setdefault(candidate_folder, []).append(f)
                folder_name_map[candidate_folder] = batch_folder
        else:
            candidate_groups = {}
            folder_name_map = {}
            for f in files:
                parts = f.name.replace('\\', '/').split('/')
                if len(parts) < 3:
                    continue
                batch_folder = parts[0]
                candidate_folder = parts[1]
                candidate_groups.setdefault(candidate_folder, []).append(f)
                folder_name_map[candidate_folder] = batch_folder

        # ── Dataset is now required ──────────────────────────────────────
        dataset_id = request.POST.get('dataset_id') or None

        selected_dataset = None
        if dataset_id:
            try:
                selected_dataset = DataSet.objects.get(pk=dataset_id)
            except DataSet.DoesNotExist:
                pass

        if not selected_dataset:
            messages.error(
                request,
                "Please select a dataset. Batches are auto-created and must be linked to a dataset."
            )
            return redirect('bulk_upload')

        if not candidate_groups:
            messages.warning(
                request,
                "No candidate subfolders found. Make sure your structure is: "
                "BatchFolder → CandidateFolder → files."
            )
            return redirect('bulk_upload')

        # ── Determine the combined folder name for batch naming ──────────
        unique_batch_folders = list(dict.fromkeys(folder_name_map.values()))
        combined_folder_name = ' - '.join(unique_batch_folders) if unique_batch_folders else 'Bulk Upload'

        created_count = 0
        skipped_count = 0

        # ── Parse all candidates first ───────────────────────────────────
        parsed_candidates = []

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
                        import pypdf, io, logging
                        logging.getLogger('pypdf').setLevel(logging.ERROR)
                        reader = pypdf.PdfReader(f)
                        text = ''.join(page.extract_text() or '' for page in reader.pages)
                        email_match = _re.search(r'[\w\.-]+@[\w\.-]+\.\w+', text)
                        if email_match:
                            email = email_match.group(0)
                        f.seek(0)   # reset file pointer after reading
                    except Exception as e:
                        print(f"PDF parse error for {f.name}: {e}")
                    break   # only check the first PDF

            jpg_files = [f for f in group_files if f.name.lower().endswith(('.jpg', '.jpeg'))]
            profile_pic = jpg_files[2] if len(jpg_files) >= 3 else None

            source = folder_name_map.get(folder_name, '')

            parsed_candidates.append({
                'first_name': first_name,
                'last_name': last_name,
                'email': email,
                'unique_id': unique_id,
                'profile_pic': profile_pic,
                'group_files': group_files,
                'source_folder': source,
            })

        # Sort candidates by source folder so earlier dates fill first
        parsed_candidates.sort(key=lambda c: c.get('source_folder', ''))
        
        # ── Place candidates into auto-created batches ───────────────────
        batch_placements = _assign_candidates_to_batches(
            dataset=selected_dataset,
            folder_name=combined_folder_name,
            candidates=parsed_candidates,
        )

        batch_summary = []

        for batch_obj, candidate_chunk in batch_placements:
            for cand in candidate_chunk:
                applicant = Applicant.objects.create(
                    first_name=cand['first_name'],
                    last_name=cand['last_name'],
                    email=cand['email'],
                    age=0,
                    gender='Not Specified',
                    dataset=selected_dataset,
                    round=batch_obj,
                    external_id=cand['unique_id'] or None,
                    profile_picture=cand['profile_pic'],
                    source_folder=cand['source_folder'],
                )

                # ── Attach all files ─────────────────────────────────────
                for f in cand['group_files']:
                    ApplicantFile.objects.create(applicant=applicant, file=f)

                created_count += 1

            count_in_batch = Applicant.objects.filter(round=batch_obj).count()
            batch_summary.append(f'"{batch_obj.DisplayName}" ({count_in_batch}/{BATCH_MAX_SIZE})')

        batch_details = ', '.join(batch_summary)
        messages.success(
            request,
            f"✅ Successfully imported {created_count} candidate profile(s) "
            f"into {len(batch_placements)} batch(es): {batch_details}. "
            f"Review each profile to complete missing details like age and gender."
        )
        return redirect('applicant_list')

    # GET — render the upload page
    datasets = DataSet.objects.filter(Active=True).order_by('DisplayName')
    return render(request, "bulk_upload.html", {
        "datasets": datasets,
    })


# ═══════════════════════════════════════════════════════════════════════════════
#  BATCH AUTO-CREATION HELPERS (used by bulk_upload_applicants)
# ═══════════════════════════════════════════════════════════════════════════════

BATCH_MAX_SIZE = 10

@login_required
@admin_required
def candidate_info_upload(request):
    """
    Upload an Excel file with candidate information.
    Matches rows to Applicant records by external_id (which can be an AADSAS ID or TUID).
    """
    import pandas as pd
    from decimal import Decimal, InvalidOperation
 
    datasets = DataSet.objects.filter(Active=True).order_by('DisplayName')
    results = None
 
    if request.method == 'POST':
        excel_file = request.FILES.get('excel_file')
        dataset_id = request.POST.get('dataset_id') or None
 
        if not excel_file:
            messages.error(request, "Please select a file to upload.")
            return redirect('candidate_info_upload')
 
        # Read the file
        try:
            fname = excel_file.name.lower()
            if fname.endswith('.csv'):
                df = pd.read_csv(excel_file, dtype=str)
            elif fname.endswith(('.xlsx', '.xls')):
                df = pd.read_excel(excel_file, dtype=str)
            else:
                messages.error(request, "Unsupported file type. Please upload an .xlsx, .xls, or .csv file.")
                return redirect('candidate_info_upload')
        except Exception as e:
            messages.error(request, f"Could not read file: {e}")
            return redirect('candidate_info_upload')
 
        # Normalize column names for flexible matching
        col_map = {}
        for col in df.columns:
            normalized = col.strip().lower().replace(' ', '_').replace('-', '_')
            col_map[normalized] = col
 
        # Detect the ID column — AADSAS ID or TUID, whichever is present
        id_col = None
        id_col_label = None
        for possible, label in [
            ('aadsas_id', 'AADSAS ID'),
            ('aadsasid', 'AADSAS ID'),
            ('tuid', 'TUID'),
            ('tu_id', 'TUID'),
            ('temple_id', 'TUID'),
            ('external_id', 'External ID'),
            ('unique_id', 'Unique ID'),
            ('id', 'ID'),
        ]:
            if possible in col_map:
                id_col = col_map[possible]
                id_col_label = label
                break
 
        if id_col is None:
            messages.error(
                request,
                "Could not find an ID column. "
                "Expected a column named 'AADSAS ID', 'TUID', or similar."
            )
            return redirect('candidate_info_upload')
 
        # Parsers
        def parse_bool_yes_no(val):
            if pd.isna(val) or val is None:
                return False
            return str(val).strip().lower() in ('yes', 'y', 'true', '1')
 
        def parse_decimal(val):
            if pd.isna(val) or val is None or str(val).strip() == '':
                return None
            try:
                return Decimal(str(val).strip())
            except (InvalidOperation, ValueError):
                return None
 
        # Column name -> (model_field, parser)
        field_mapping = {
            'total_ai':         ('total_ai',         parse_decimal),
            'total_nc':         ('total_nc',         parse_decimal),
            'first_gen':        ('first_gen',        parse_bool_yes_no),
            'first_generation': ('first_gen',        parse_bool_yes_no),
            're_applicant':     ('re_applicant',     parse_bool_yes_no),
            'reapplicant':      ('re_applicant',     parse_bool_yes_no),
            'pb_to_dmd':        ('pb_to_dmd',        parse_bool_yes_no),
            'z_score':          ('z_score',          parse_decimal),
            'zscore':           ('z_score',          parse_decimal),
            'former_post_bacc': ('former_post_bacc', parse_bool_yes_no),
            'formerpostbacc':   ('former_post_bacc', parse_bool_yes_no),
            '3+4':              ('three_plus_four',  parse_bool_yes_no),
            '3_4':              ('three_plus_four',  parse_bool_yes_no),
            'three_plus_four':  ('three_plus_four',  parse_bool_yes_no),
            'gender':           ('gender',           lambda v: str(v).strip() if pd.notna(v) and str(v).strip() else None),
        }
 
        # Build active column mapping for this file
        active_mappings = {}
        for normalized, excel_col in col_map.items():
            if normalized in field_mapping:
                active_mappings[excel_col] = field_mapping[normalized]
 
        # Process rows
        total_rows = len(df)
        matched = 0
        unmatched = 0
        errors = 0
        unmatched_ids = []
        error_details = []
        matched_applicants = []
 
        for idx, row in df.iterrows():
            ext_id = str(row[id_col]).strip() if pd.notna(row.get(id_col)) else ''
            if not ext_id:
                errors += 1
                error_details.append(f"Row {idx + 2}: Missing {id_col_label}")
                continue
 
            # Find matching applicant(s) by external_id
            applicant_qs = Applicant.objects.filter(external_id=ext_id)
            if dataset_id:
                applicant_qs = applicant_qs.filter(dataset_id=dataset_id)
 
            applicants_found = list(applicant_qs)
 
            if not applicants_found:
                unmatched += 1
                unmatched_ids.append(ext_id)
                continue
 
            for applicant in applicants_found:
                update_fields = ['candidate_info_imported']
                applicant.candidate_info_imported = True
 
                for excel_col, (model_field, parser) in active_mappings.items():
                    raw_value = row.get(excel_col)
                    parsed_value = parser(raw_value)
 
                    # For gender, only update if we got a non-None value
                    if model_field == 'gender' and parsed_value is None:
                        continue
 
                    setattr(applicant, model_field, parsed_value)
                    if model_field not in update_fields:
                        update_fields.append(model_field)
 
                try:
                    applicant.save(update_fields=update_fields)
                    matched += 1
                    matched_applicants.append(applicant)
                except Exception as e:
                    errors += 1
                    error_details.append(f"Row {idx + 2} (ID {ext_id}): {e}")
 
        results = {
            'total_rows': total_rows,
            'matched': matched,
            'unmatched': unmatched,
            'errors': errors,
            'unmatched_ids': unmatched_ids[:20],
            'error_details': error_details[:10],
            'matched_applicants': matched_applicants[:25],
            'id_col_label': id_col_label,
        }
 
        if matched > 0:
            messages.success(request, f"Successfully updated {matched} candidate(s) from {total_rows} rows.")
        if unmatched > 0:
            messages.warning(request, f"{unmatched} row(s) had no matching candidate (matched by {id_col_label}).")
 
    return render(request, "candidate_info_upload.html", {
        'datasets': datasets,
        'results': results,
    })


def _get_batch_folder_names(batch):
    """
    Extract the list of source folder names from a batch's DisplayName.

    "December 1 - December 2"  →  ["December 1", "December 2"]
    "December 1 (2)"           →  ["December 1"]
    "December 1"               →  ["December 1"]
    """
    name = batch.DisplayName
    # Strip overflow suffix like " (2)"
    base = re.sub(r'\s*\(\d+\)\s*$', '', name)
    return [part.strip() for part in base.split(' - ') if part.strip()]


def _build_batch_display_name(folder_names, overflow_index=None):
    """
    Build a display name from unique folder names + optional overflow number.

    (["December 1"])                        →  "December 1"
    (["December 1", "December 2"])          →  "December 1 - December 2"
    (["December 1"], overflow_index=2)      →  "December 1 (2)"
    """
    seen = set()
    unique = []
    for n in folder_names:
        if n not in seen:
            seen.add(n)
            unique.append(n)

    base = ' - '.join(unique)
    if overflow_index and overflow_index > 1:
        base = f"{base} ({overflow_index})"
    return base


def _assign_candidates_to_batches(dataset, folder_name, candidates):
    """
    Place a list of parsed candidate dicts into batches of max BATCH_MAX_SIZE.
    Fills the most recent underfull batch first, then creates new ones.
    Batch names reflect only the source folders of candidates actually in that batch.
    Overflow numbering only applies when names would collide.

    Returns: list of (Batch, [candidate_dicts]) tuples.
    """
    result = []
    remaining = list(candidates)

    # ── Step 1: Try to fill the most recent underfull batch ───────────────
    recent_batch = (
        Batch.objects.filter(DataSet=dataset, Active=True)
        .annotate(candidate_count=Count('applicant'))
        .filter(candidate_count__lt=BATCH_MAX_SIZE)
        .order_by('-CreatedAt')
        .first()
    )

    if recent_batch:
        current_count = Applicant.objects.filter(round=recent_batch).count()
        slots = BATCH_MAX_SIZE - current_count
        if slots > 0:
            to_fill = remaining[:slots]
            remaining = remaining[slots:]

            # Update batch name based on NEW candidates' actual source folders
            new_folders = list(dict.fromkeys(
                c['source_folder'] for c in to_fill if c.get('source_folder')
            ))
            existing_names = _get_batch_folder_names(recent_batch)
            changed = False
            for nf in new_folders:
                if nf not in existing_names:
                    existing_names.append(nf)
                    changed = True
            if changed:
                # Sort folder names so ordering is consistent
                existing_names.sort()
                recent_batch.DisplayName = _build_batch_display_name(existing_names)
                recent_batch.save()

            result.append((recent_batch, to_fill))

    if not remaining:
        # ── Clean up orphaned numbering ──────────────────────────────────
        _cleanup_batch_numbering(dataset)
        return result

    # ── Step 2: Create new batches for the rest ──────────────────────────
    chunks = []
    while remaining:
        chunks.append(remaining[:BATCH_MAX_SIZE])
        remaining = remaining[BATCH_MAX_SIZE:]

    for chunk in chunks:
        # Derive name from the actual source folders in THIS chunk
        chunk_folders = list(dict.fromkeys(
            c['source_folder'] for c in chunk if c.get('source_folder')
        ))
        if not chunk_folders:
            chunk_folders = [folder_name]

        # Sort for consistent ordering
        chunk_folders.sort()
        base_name = _build_batch_display_name(chunk_folders)

        # Check if this exact name already exists — if so, add numbering
        existing_with_name = Batch.objects.filter(
            DataSet=dataset,
            DisplayName__startswith=base_name
        ).count()

        if existing_with_name > 0:
            display_name = f"{base_name} ({existing_with_name + 1})"

            # Retroactively number the first one as (1) if it's unnumbered
            first_batch = Batch.objects.filter(
                DataSet=dataset,
                DisplayName=base_name,
            ).first()
            if first_batch:
                first_batch.DisplayName = f"{base_name} (1)"
                first_batch.save()
        else:
            display_name = base_name

        new_batch = Batch.objects.create(
            DataSet=dataset,
            DisplayName=display_name,
            Active=True,
        )
        _auto_assign_batch_to_group(new_batch)
        result.append((new_batch, chunk))

    # ── Clean up orphaned numbering ──────────────────────────────────────
    _cleanup_batch_numbering(dataset)

    return result


def _cleanup_batch_numbering(dataset):
    """
    If a batch is named "Something (1)" but there's no "Something (2)",
    rename it back to just "Something" since numbering is unnecessary.
    """
    import re as _re
    numbered_batches = Batch.objects.filter(
        DataSet=dataset,
        Active=True,
        DisplayName__regex=r'.+ \(\d+\)$',
    )

    for batch in numbered_batches:
        match = _re.match(r'^(.+?) \((\d+)\)$', batch.DisplayName)
        if not match:
            continue

        base_name = match.group(1)
        # Count how many batches share this base name (numbered or exact)
        siblings = Batch.objects.filter(
            DataSet=dataset,
            Active=True,
            DisplayName__startswith=base_name,
        ).count()

        # If this is the only one with this base name, drop the number
        if siblings == 1:
            batch.DisplayName = base_name
            batch.save()
    
    
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
    sort = request.GET.get('sort', 'name')
    direction = request.GET.get('dir', 'asc')

    assigned_batches = Batch.objects.filter(assigned_reviewers=user)

    votes = Vote.objects.filter(
        voter=user,
        applicant__round__in=assigned_batches,
    ).select_related('applicant', 'applicant__round', 'applicant__dataset').order_by('-created_at')

    scores = Score.objects.filter(
        voter=user,
        applicant__round__in=assigned_batches,
    ).select_related('applicant', 'applicant__round')

    # Membership is driven by VOTES only — a candidate appears here only
    # once this reviewer has actually voted on them (fixes the blank-Score leak).
    applicant_pks = set(votes.values_list('applicant__pk', flat=True))
    applicants = (
        Applicant.objects.filter(pk__in=applicant_pks)
        .select_related('round', 'dataset')
        .prefetch_related('flagged_by')
    )

    reviews = []
    for applicant in applicants:
        vote = votes.filter(applicant=applicant).first()
        score = scores.filter(applicant=applicant).first()
        reviews.append({
            'applicant': applicant,
            'vote': vote,
            'score': score,
            'is_flagged': user in applicant.flagged_by.all(),
        })

    def sort_key(r):
        a = r['applicant']
        if sort == 'batch':
            return (a.round.DisplayName if a.round else '').lower()
        if sort == 'vote':
            return r['vote'].value if r['vote'] else -99
        if sort == 'overall':
            return (r['score'].overall_score or 0) if r['score'] else 0
        if sort == 'flag':
            return 1 if r['is_flagged'] else 0
        return (a.last_name.lower(), a.first_name.lower())  # default: name

    reviews.sort(key=sort_key, reverse=(direction == 'desc'))

    return render(request, "my_reviews.html", {
        'reviews': reviews,
        'total_votes': votes.count(),
        'total_scores': scores.count(),
        'sort': sort,
        'direction': direction,
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
        if action == 'set_status_interview':
            count = Applicant.objects.filter(round_id__in=batch_ids).update(status=Applicant.Status.INTERVIEW)
            messages.success(request, f"Updated {count} applicants to 'Interview'.")
        elif action == 'set_status_interview_complete':
            count = Applicant.objects.filter(round_id__in=batch_ids).update(status=Applicant.Status.INTERVIEW_COMPLETE)
            messages.success(request, f"Updated {count} applicants to 'Interview Complete'.")
        else:
            messages.error(request, "No valid action selected.")
            
    return redirect('batch_list')

@login_required
@admin_required
def manage_reviewer_groups(request):
    """Admin page to assign committee members to review groups (A, B, C)."""
    members = (
        User.objects.filter(profile__role='COMMITTEE_MEMBER')
        .select_related('profile')
        .order_by('username')
    )

    if request.method == 'POST':
        form = ReviewerGroupForm(request.POST, members=members)
        if form.is_valid():
            for user in members:
                field_name = f'user_{user.pk}'
                new_group = form.cleaned_data.get(field_name, '')
                if user.profile.review_group != new_group:
                    user.profile.review_group = new_group
                    user.profile.save()

            # Sync batch reviewers to match current group assignments
            for group_code in ['A', 'B', 'C']:
                group_members = User.objects.filter(
                    profile__role='COMMITTEE_MEMBER',
                    profile__review_group=group_code,
                )
                group_batches = Batch.objects.filter(
                    review_group=group_code,
                    Active=True,
                )
                for batch in group_batches:
                    batch.assigned_reviewers.set(group_members)

            messages.success(request, "Reviewer group assignments updated.")
            return redirect('manage_reviewer_groups')
    else:
        form = ReviewerGroupForm(members=members)

    groups = {
        'A': members.filter(profile__review_group='A'),
        'B': members.filter(profile__review_group='B'),
        'C': members.filter(profile__review_group='C'),
        'unassigned': members.filter(profile__review_group=''),
    }

    return render(request, 'manage_reviewer_groups.html', {
        'form': form,
        'members': members,
        'groups': groups,
    })


def _auto_assign_batch_to_group(batch):
    """
    Assign a batch to the review group (A, B, C) with the fewest active batches.
    Then set the batch's assigned_reviewers to all committee members in that group.
    """
    from .models import Profile

    group_choices = ['A', 'B', 'C']

    group_counts = {}
    for g in group_choices:
        group_counts[g] = Batch.objects.filter(
            DataSet=batch.DataSet,
            Active=True,
            review_group=g,
        ).count()

    selected_group = min(group_choices, key=lambda g: group_counts[g])

    batch.review_group = selected_group
    batch.save()

    group_members = User.objects.filter(
        profile__role=Profile.Role.COMMITTEE_MEMBER,
        profile__review_group=selected_group,
    )
    batch.assigned_reviewers.set(group_members)

    return selected_group

@login_required
@admin_required
def send_notification(request):
    if request.method == 'POST':
        form = SendNotificationForm(request.POST, request.FILES)
        if form.is_valid():
            recipient_type = form.cleaned_data['recipient_type']
            subject = form.cleaned_data['subject']
            message = form.cleaned_data['message']
 
            # Determine recipients
            recipients = User.objects.none()
 
            if recipient_type == 'dataset':
                dataset = form.cleaned_data.get('dataset')
                if dataset:
                    batch_ids = Batch.objects.filter(DataSet=dataset).values_list('pk', flat=True)
                    reviewer_ids = Batch.objects.filter(pk__in=batch_ids).values_list('assigned_reviewers', flat=True)
                    recipients = User.objects.filter(pk__in=reviewer_ids).distinct()
 
            elif recipient_type == 'group':
                group = form.cleaned_data.get('group')
                if group:
                    recipients = User.objects.filter(
                        profile__role='COMMITTEE_MEMBER',
                        profile__review_group=group,
                    )
 
            elif recipient_type == 'batch':
                batch = form.cleaned_data.get('batch')
                if batch:
                    recipients = batch.assigned_reviewers.all()
 
            elif recipient_type == 'individual':
                recipients = form.cleaned_data.get('individual_reviewers', User.objects.none())
 
            # Create notifications
            count = 0
            for user in recipients:
                notification = Notification.objects.create(
                    recipient=user,
                    sender=request.user,
                    subject=subject,
                    message=message,
                    deadline=form.cleaned_data.get('deadline'),
                )
                for f in request.FILES.getlist('attachments'):
                    NotificationAttachment.objects.create(
                        notification=notification,
                        file=f,
                    )
                count += 1
 
            if count:
                messages.success(request, f"Notification sent to {count} reviewer(s).")
            else:
                messages.warning(request, "No recipients found for your selection.")
            return redirect('send_notification')
    else:
        form = SendNotificationForm()
    
 
    return render(request, 'send_notification.html', {'form': form})
 
 
@login_required
def notification_list(request):
    from django.db.models import Case, When, Value, IntegerField
    notifications = (
        request.user.notifications
        .annotate(
            has_deadline=Case(
                When(deadline__isnull=False, then=Value(0)),
                default=Value(1),
                output_field=IntegerField(),
            )
        )
        .order_by('has_deadline', 'deadline', '-created_at')[:50]
    )
    unread_count = request.user.notifications.filter(is_read=False).count()

    return render(request, 'notification_list.html', {
        'notifications': notifications,
        'unread_count': unread_count,
    })
 
 
@login_required
def notification_detail(request, pk):
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
 
    from django.utils import timezone
    if not notification.is_read:
        # Only auto-mark read if no deadline or deadline has passed
        if not notification.deadline or notification.deadline <= timezone.now():
            notification.is_read = True
            notification.read_at = timezone.now()
            notification.save()
 
    return render(request, 'notification_detail.html', {
        'notification': notification,
    })
 
 
@login_required
@require_POST
def mark_all_notifications_read(request):
    from django.utils import timezone
    now = timezone.now()
    # Delete all except those with upcoming deadlines
    request.user.notifications.filter(
        Q(deadline__isnull=True) | Q(deadline__lte=now)
    ).delete()
    messages.success(request, "Cleared all notifications (except those with upcoming deadlines).")
    return redirect('notification_list')

@login_required
@require_POST
def mark_notification_read(request, pk):
    from django.utils import timezone
    notification = get_object_or_404(Notification, pk=pk, recipient=request.user)
    if notification.deadline and notification.deadline > timezone.now():
        messages.warning(request, "Cannot dismiss a notification with an upcoming deadline.")
        return redirect('notification_list')
    notification.delete()
    return redirect('notification_list')
