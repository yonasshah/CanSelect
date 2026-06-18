# applicants/views.py
import csv
import random
from urllib import request
from django.urls import reverse
from django.db.models import Avg, Case, Count, Exists, F, IntegerField, OuterRef, Q, Value, When
import json
from itertools import cycle
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth import update_session_auth_hash
from django.utils.safestring import mark_safe
from datetime import datetime
from django.http import HttpResponse
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.core.paginator import Paginator
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login
from django.contrib import messages
import pandas as pd
from django.utils import timezone
from decimal import Decimal, InvalidOperation
import pypdf
import re as _re
from collections import OrderedDict
from .decorators import admin_required, committee_access_required
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from .models import Activity, Applicant, ApplicantFile, Notification, NotificationAttachment, Profile, Score, Vote, DataSet, Batch, Flag, Comment, ReviewPanel
from .forms import ApplicantForm, ApplicantStatusForm, BatchAssignmentForm, BulkUploadForm, SendNotificationForm, UploadManyFilesForm, EmailLoginForm, DataSetForm, BatchForm, CommentForm, ScoreForm, ReviewPanelForm

def email_login(request):
    if request.method == "POST":
        form = EmailLoginForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data["email"]
            user, _ = User.objects.get_or_create(username=email, defaults={"email": email})
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            return redirect("dashboard")
    else:
        form = EmailLoginForm()
    return render(request, "login.html", {"form": form})

def _extract_pdf_data(file_obj):
    """
    Extract structured candidate data from an AADSAS application PDF.
    Returns a dict of field_name -> value. Only includes keys where a value
    was successfully found — never overwrites with None.
    """
    try:
        import pypdf, io, logging
        logging.getLogger('pypdf').setLevel(logging.ERROR)
        file_obj.seek(0)
        reader = pypdf.PdfReader(file_obj)
        text = '\n'.join(page.extract_text() or '' for page in reader.pages)
        file_obj.seek(0)
    except Exception:
        return {}
 
    data = {}
 
    def safe_decimal(val):
        try:
            return Decimal(str(val).strip())
        except (InvalidOperation, ValueError):
            return None
 
    def safe_int(val):
        try:
            return int(str(val).strip())
        except (ValueError, TypeError):
            return None
 
    # ── Email ────────────────────────────────────────────────────────
    m = _re.search(r'[\w.\-]+@[\w.\-]+\.\w+', text)
    if m:
        data['email'] = m.group(0)
 
    # ── Phone ────────────────────────────────────────────────────────
    m = _re.search(r'Preferred Phone Number\s+(\+[\d\s]+?)(?:\s+Type)', text)
    if m:
        data['phone'] = m.group(1).strip()
 
    # ── Date of birth ─────────────────────────────────────────────────
    m = _re.search(r'Date of Birth:\s*([\d-]+)', text)
    if m:
        raw = m.group(1).strip()  # e.g. "10-25-2003"
        try:
            from datetime import datetime
            data['date_of_birth'] = datetime.strptime(raw, '%m-%d-%Y').date()
        except ValueError:
            pass
 
    # ── Gender ───────────────────────────────────────────────────────
    m = _re.search(r'\nSex:\s*(MALE|FEMALE)', text)
    if m:
        raw = m.group(1)
        data['gender'] = 'Male' if raw == 'MALE' else 'Female'
 
    # ── Citizenship ──────────────────────────────────────────────────
    m = _re.search(r'Citizenship Status:\s*([^\n]+)', text)
    if m:
        data['citizenship'] = m.group(1).strip()
 
    # ── State of residence ───────────────────────────────────────────
    m = _re.search(r'State of Residence:\s*(\w+)', text)
    if m:
        data['state_of_residence'] = m.group(1).strip()
 
    # ── First generation ─────────────────────────────────────────────
    m = _re.search(r'first.generation college student.*?Answer:\s*(Yes|No)', text, _re.IGNORECASE | _re.DOTALL)
    if m:
        data['first_gen'] = (m.group(1).strip().lower() == 'yes')
 
    # ── GPA (from the summary table on page 1) ────────────────────────
    # Row: "UnderGraduate  BCP_GPA  BCP_HRS  SCI_GPA  SCI_HRS  NSCI_GPA  NSCI_HRS  TOT_GPA  TOT_HRS"
    m = _re.search(
        r'UnderGraduate\s+([\d.]+)\s+[\d.]+\s+([\d.]+)\s+[\d.]+\s+[\d.]+\s+[\d.]+\s+([\d.]+)',
        text
    )
    if m:
        bcp = safe_decimal(m.group(1))
        sci = safe_decimal(m.group(2))
        tot = safe_decimal(m.group(3))
        if bcp is not None:
            data['gpa_bcp'] = bcp
        if sci is not None:
            data['gpa_science'] = sci
        if tot is not None:
            data['gpa_overall'] = tot
 
    # ── DAT scores (Official DAT table) ──────────────────────────────
    # Order: Date  AcadAvg  PAT  QuantReas  ReadComp  Bio  GenChem  OrgChem  TotalSci
    m = _re.search(
        r'OFFICIAL DAT.*?(\d{2}-\d{2}-\d{4})\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)',
        text, _re.DOTALL
    )
    if m:
        fields = [
            ('dat_academic_avg',          m.group(2)),
            ('dat_perceptual_ability',    m.group(3)),
            ('dat_quantitative_reasoning',m.group(4)),
            ('dat_reading_comp',          m.group(5)),
            ('dat_biology',               m.group(6)),
            ('dat_general_chem',          m.group(7)),
            ('dat_organic_chem',          m.group(8)),
            ('dat_total_science',         m.group(9)),
        ]
        for field_name, raw_val in fields:
            v = safe_int(raw_val)
            if v is not None:
                data[field_name] = v
 
    # ── Experience hours ──────────────────────────────────────────────
    m = _re.search(r'DENTAL RELATED EXPERIENCE TOTAL HOURS:\s*(\d+)', text)
    if m:
        v = safe_int(m.group(1))
        if v is not None:
            data['dental_experience_hours'] = v
 
    m = _re.search(r'SHADOWING EXPERIENCE TOTAL HOURS:\s*(\d+)', text)
    if m:
        v = safe_int(m.group(1))
        if v is not None:
            data['shadowing_hours'] = v
 
    return data

@login_required
@admin_required
@require_POST
def update_member_type(request):
    """
    Quick AJAX-style POST to update a committee member's person_type.
    Called from the Manage Panels page inline dropdown.
    """
    user_id     = request.POST.get('user_id')
    person_type = request.POST.get('person_type', '')
 
    try:
        profile = User.objects.get(pk=user_id).profile
        valid_types = [c[0] for c in profile.PersonType.choices]
        if person_type in valid_types:
            profile.person_type = person_type
            profile.save()
    except User.DoesNotExist:
        pass
 
    return redirect('manage_panels')

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
            nav_params = request.POST.get('nav_params', '')
            redirect_url = f'/applicant/{pk}/?{nav_params}' if nav_params else f'/applicant/{pk}/'
            return redirect(redirect_url)

    all_files = applicant.files.all()
    
    video_files = []
    application_docs = []
    evaluation_docs = []
    photo_files = []
    other_docs = []
    
    for f in all_files:
        fname = f.file.name.lower()
        basename = fname.split('/')[-1]
        if f.is_video:
            video_files.append(f)
        elif fname.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
            photo_files.append(f)
        elif fname.endswith('.pdf') and 'application' in fname:
            application_docs.append(f)
        elif fname.endswith('.pdf') and (
            'evaluation' in basename
            or _re.search(r'^\d+_\d{2}_\d{2}_\d{4}_', basename)
        ):
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
    queue_complete = request.GET.get('queue_complete') == '1'

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
            Q(votes__voter=request.user) &
            Q(votes__value__in=[1, -1]) &
            ~Q(pk=pk)
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

    # ── Nav params ────────────────────────────────────────────────────────────
    nav_params_parts = []
    if from_reviews:
        nav_params_parts.append('from=reviews')
    elif from_batch and batch_pk:
        nav_params_parts.append(f'from=batch&batch_pk={batch_pk}')
    elif from_queue:
        if batch_id:
            nav_params_parts.append(f'queue=1&batch={batch_id}')
        else:
            nav_params_parts.append('queue=1')
    else:
        if batch_id:
            nav_params_parts.append(f'batch={batch_id}')
        if search_q:
            nav_params_parts.append(f'q={search_q}')
    nav_params = '&'.join(nav_params_parts)

    # ── Back URL ──────────────────────────────────────────────────────────────
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

    # ── Batch completion card ─────────────────────────────────────────────────
    batch_completion = None
    if queue_complete and batch_id:
        try:
            completed_batch = Batch.objects.get(pk=batch_id)
            user_votes = Vote.objects.filter(
                voter=request.user,
                applicant__round=completed_batch,
            )
            batch_completion = {
                'batch': completed_batch,
                'accept': user_votes.filter(value=1).count(),
                'deny': user_votes.filter(value=-1).count(),
                'waitlist': user_votes.filter(value=0).count(),
                'total': user_votes.count(),
            }
        except Batch.DoesNotExist:
            pass

    if request.user.profile.role == 'ADMIN':
        flags = applicant.flags.select_related('user').all()
    else:
        flags = applicant.flags.filter(user=request.user).select_related('user')
        
    reviewer_vote_progress = None
    if applicant.round:
        assigned = applicant.round.assigned_reviewers.all()
        total_reviewers = assigned.count()
        if total_reviewers > 0:
            voted_reviewers = Vote.objects.filter(
                applicant=applicant,
                voter__in=assigned,
            ).values('voter').distinct().count()
            reviewer_vote_progress = {
                'voted': voted_reviewers,
                'total': total_reviewers,
                'pct':   round(voted_reviewers / total_reviewers * 100),
            }
 

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
        'flags': flags,
        'batch_completion': batch_completion,
        'reviewer_vote_progress': reviewer_vote_progress,
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
        print(f"DEBUG vote: next_id={next_id!r}, nav_params={nav_params!r}")
        
        if next_id and next_id != 'None':
            link = f'<a href="/applicant/{next_id}/?{nav_params}" class="alert-link ms-2">Go to Next Candidate &raquo;</a>'
            messages.success(request, mark_safe(f"Vote recorded for {applicant.first_name}. {link}"))
        else:
            is_queue = 'queue=1' in nav_params
            batch_id_from_nav = None
            if is_queue:
                import urllib.parse
                params = dict(urllib.parse.parse_qsl(nav_params))
                batch_id_from_nav = params.get('batch')

            if is_queue and batch_id_from_nav:
                return redirect(f'/applicant/{pk}/?queue=1&batch={batch_id_from_nav}&queue_complete=1')
            else:
                back_url = request.POST.get('back_url', '/applicant/')
                link = f'<a href="{back_url}" class="alert-link ms-2">Return to List</a>'
                messages.success(request, mark_safe(f"Vote recorded. End of queue. {link}"))

        # Always redirect back with nav_params preserved
        return redirect(f'/applicant/{pk}/?{nav_params}')
    
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
    search_query       = request.GET.get('q', '').strip()
    dataset_status     = request.GET.get('dataset_status', '')   # replaces status + show_archived
    application_system = request.GET.get('application_system', '')
    program_type       = request.GET.get('program_type', '')
    candidate_search   = request.GET.get('candidate', '').strip()

    datasets = DataSet.objects.annotate(
        applicant_count=Count('applicants', distinct=True),
        batch_count=Count('batches', distinct=True),
    ).order_by('DisplayName')

    if search_query:
        datasets = datasets.filter(DisplayName__icontains=search_query)
    if application_system:
        datasets = datasets.filter(application_system=application_system)
    if program_type:
        datasets = datasets.filter(program_type=program_type)

    # ── Unified status filter ────────────────────────────────────────
    if dataset_status == 'live':
        filtered_qs      = datasets.filter(Active=True, IsLive=True)
        archived_datasets = DataSet.objects.none()
    elif dataset_status == 'offline':
        filtered_qs      = datasets.filter(Active=True, IsLive=False)
        archived_datasets = DataSet.objects.none()
    elif dataset_status == 'archived':
        filtered_qs      = datasets.filter(Active=False)
        archived_datasets = DataSet.objects.none()
    elif dataset_status == 'all':
        filtered_qs      = datasets
        archived_datasets = DataSet.objects.none()
    else:                                           # default: active only
        filtered_qs      = datasets.filter(Active=True)
        archived_datasets = datasets.filter(Active=False)

    paginator = Paginator(filtered_qs, 25)
    page_obj  = paginator.get_page(request.GET.get('page'))

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
        from collections import Counter
        name_counts = Counter()
        for a in candidate_results:
            key = f"{a.last_name.lower().strip()},{a.first_name.lower().strip()}"
            name_counts[key] += 1
        id_counts = Counter()
        for a in candidate_results:
            if a.external_id:
                id_counts[a.external_id] += 1
        for a in candidate_results:
            key = f"{a.last_name.lower().strip()},{a.first_name.lower().strip()}"
            a.appearance_count = max(name_counts.get(key, 1), id_counts.get(a.external_id, 1) if a.external_id else 1)

    return render(request, "dataset_list.html", {
        'page_obj':                   page_obj,
        'archived_datasets':          archived_datasets,
        'search_query':               search_query,
        'dataset_status':             dataset_status,
        'application_system':         application_system,
        'program_type':               program_type,
        'application_system_choices': DataSet.ApplicationSystem.choices,
        'program_type_choices':       DataSet.ProgramType.choices,
        'candidate_search':           candidate_search,
        'candidate_results':          candidate_results,
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

    # ── Filters ──────────────────────────────────────────────────────
    search_query        = request.GET.get('q', '').strip()
    status_filter       = request.GET.get('status', '')
    batch_filter        = request.GET.get('batch_id', '')
    flagged_only        = request.GET.get('flagged_only', '')
    filter_first_gen    = request.GET.get('first_gen', '')
    filter_re_applicant = request.GET.get('re_applicant', '')
    filter_pb_to_dmd    = request.GET.get('pb_to_dmd', '')
    filter_former_pb    = request.GET.get('former_post_bacc', '')
    filter_3p4          = request.GET.get('three_plus_four', '')

    has_filters = any([
        search_query, status_filter, batch_filter, flagged_only,
        filter_first_gen, filter_re_applicant, filter_pb_to_dmd,
        filter_former_pb, filter_3p4,
    ])
    has_advanced = any([
        filter_first_gen, filter_re_applicant, filter_pb_to_dmd,
        filter_former_pb, filter_3p4,
    ])

    batches = dataset.batches.annotate(
        applicant_count=Count('applicant', distinct=True),
        reviewed_count=Count(
            'applicant__votes',
            filter=Q(applicant__votes__isnull=False),
            distinct=True
        ),
    ).order_by('DisplayName')

    if batch_filter:
        batches = batches.filter(pk=batch_filter)

    # Build a filtered candidate queryset per batch
    def build_candidate_qs(batch):
        qs = Applicant.objects.filter(round=batch).annotate(
            avg_score=Avg('scores__overall_score')
        )
        if search_query:
            qs = qs.filter(
                Q(first_name__icontains=search_query) |
                Q(last_name__icontains=search_query) |
                Q(external_id__icontains=search_query)
            )
        if status_filter:
            qs = qs.filter(status=status_filter)
        if flagged_only:
            qs = qs.filter(flagged_by__isnull=False).distinct()
        if filter_first_gen:
            qs = qs.filter(first_gen=True)
        if filter_re_applicant:
            qs = qs.filter(re_applicant=True)
        if filter_pb_to_dmd:
            qs = qs.filter(pb_to_dmd=True)
        if filter_former_pb:
            qs = qs.filter(former_post_bacc=True)
        if filter_3p4:
            qs = qs.filter(three_plus_four=True)
        return qs.order_by('last_name', 'first_name')

    for batch in batches:
        batch.candidates = build_candidate_qs(batch)

    # For no-results message
    total_candidates = sum(b.candidates.count() for b in batches)
    applicant_count  = Applicant.objects.filter(dataset=dataset).count()

    return render(request, "dataset_detail.html", {
        "dataset":              dataset,
        "batches":              batches,
        "applicant_count":      applicant_count,
        "total_candidates":     total_candidates,
        "has_filters":          has_filters,
        "has_advanced":         has_advanced,
        "search_query":         search_query,
        "status_filter":        status_filter,
        "batch_filter":         batch_filter,
        "flagged_only":         flagged_only,
        "filter_first_gen":     filter_first_gen,
        "filter_re_applicant":  filter_re_applicant,
        "filter_pb_to_dmd":     filter_pb_to_dmd,
        "filter_former_pb":     filter_former_pb,
        "filter_3p4":           filter_3p4,
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
        'total_batches': len(pending_batches) + len(complete_batches),
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
def dataset_decisions_section(request, pk, section):
    """
    Dedicated page for a single decisions section:
    section = 'accepts' | 'split' | 'denies'
    Shows expanded columns: GPA, DAT, experience hours, full vote breakdown.
    """
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
    cap = request.GET.get('cap', '')
 
    SECTION_META = {
        'accepts': {
            'label':      'Clear Accepts',
            'icon':       'bi-check-circle-fill',
            'color':      'success',
            'pct_field':  'accept_pct',
            'min_pct':    threshold,
            'sort_key':   lambda a: (-a.accept_pct, -(a.avg_score or 0)),
        },
        'denies': {
            'label':      'Clear Denies',
            'icon':       'bi-x-circle-fill',
            'color':      'danger',
            'pct_field':  'deny_pct',
            'min_pct':    threshold,
            'sort_key':   lambda a: (-a.deny_pct, -(a.avg_score or 0)),
        },
        'split': {
            'label':      'Split — Needs Discussion',
            'icon':       'bi-exclamation-circle-fill',
            'color':      'warning',
            'pct_field':  None,
            'min_pct':    None,
            'sort_key':   lambda a: (not a.is_flagged, -a.accept_pct, -(a.avg_score or 0)),
        },
    }
 
    if section not in SECTION_META:
        return redirect('dataset_decisions', pk=pk)
 
    meta = SECTION_META[section]
 
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
 
    FINAL_STATUSES = {'ACCEPTED', 'ACCEPTED_MAILED', 'ACCEPTED_NOT_MAILED',
                      'REJECTED', 'WAITLISTED', 'DECLINED'}
 
    candidates = []
    for a in applicants:
        if a.total_votes == 0:
            continue
        a.accept_pct = round(a.accept_ct / a.total_votes * 100)
        a.deny_pct   = round(a.deny_ct   / a.total_votes * 100)
        a.is_final   = a.status in FINAL_STATUSES
        a.is_flagged = a.flagged_by.exists()
 
        if section == 'accepts' and a.accept_pct >= threshold:
            candidates.append(a)
        elif section == 'denies' and a.deny_pct >= threshold:
            candidates.append(a)
        elif section == 'split' and a.accept_pct < threshold and a.deny_pct < threshold:
            candidates.append(a)
 
    candidates.sort(key=meta['sort_key'])
 
    # ── Sort overrides from query params ─────────────────────────────
    sort      = request.GET.get('sort', '')
    direction = request.GET.get('dir', 'asc')
    SORT_MAP = {
        'name':      lambda a: (a.last_name.lower(), a.first_name.lower()),
        'batch':     lambda a: (a.round.DisplayName if a.round else '').lower(),
        'gpa':       lambda a: -(a.gpa_overall or 0),
        'dat':       lambda a: -(a.dat_academic_avg or 0),
        'accept_pct':lambda a: -a.accept_pct,
        'score':     lambda a: -(a.avg_score or 0),
    }
    if sort in SORT_MAP:
        reverse = (direction == 'desc')
        candidates.sort(key=SORT_MAP[sort], reverse=reverse)
 
    sort_options = [
        ("name",       "Name"),
        ("batch",      "Batch"),
        ("gpa",        "Overall GPA"),
        ("dat",        "DAT Average"),
        ("accept_pct", "Accept %"),
        ("score",      "Review Score"),
    ]
 
    return render(request, "dataset_decisions_section.html", {
        'dataset':            dataset,
        'section':            section,
        'meta':               meta,
        'candidates':         candidates,
        'threshold':          threshold,
        'threshold_display':  threshold_display,
        'cap':                cap,
        'sort':               sort,
        'direction':          direction,
        'sort_options':       sort_options,
        'crumbs': [
            {'label': 'Datasets',          'url': '/datasets/'},
            {'label': dataset.DisplayName, 'url': f'/datasets/{dataset.pk}/'},
            {'label': 'Final Decisions',   'url': f'/datasets/{dataset.pk}/decisions/?threshold={threshold}{"&cap=" + cap if cap else ""}'},
            {'label': meta["label"],       'url': ''},
        ],
    })
 
 
@login_required
@admin_required
def export_decisions_csv(request, pk):
    dataset = get_object_or_404(DataSet, pk=pk)

    ACCEPTED_STATUSES = {'ACCEPTED', 'ACCEPTED_MAILED', 'ACCEPTED_NOT_MAILED'}

    applicants = (
        Applicant.objects.filter(dataset=dataset)
        .select_related('round')
        .prefetch_related('flagged_by')
        .annotate(
            accept_ct=Count('votes', filter=Q(votes__value=1),  distinct=True),
            deny_ct=Count('votes',   filter=Q(votes__value=-1), distinct=True),
            wait_ct=Count('votes',   filter=Q(votes__value=0),  distinct=True),
            avg_score=Avg('scores__overall_score'),
        )
        .filter(status__in=[
            'ACCEPTED', 'ACCEPTED_MAILED', 'ACCEPTED_NOT_MAILED',
            'REJECTED', 'WAITLISTED', 'DECLINED',
        ])
        .order_by('status', 'last_name', 'first_name')
    )

    response = HttpResponse(
        content_type='text/csv',
        headers={'Content-Disposition': f'attachment; filename="{dataset.DisplayName} - Decisions.csv"'},
    )
    writer = csv.writer(response)
    writer.writerow([
        'Last Name', 'First Name', 'Email', 'External ID',
        'Dataset', 'Batch', 'Committee Decision',
        'Yes', 'No', 'Waitlist', 'Flag',
        'Avg Score', 'Total AI', 'Total NC', 'Z-Score',
        'First Gen', 'Re-Applicant', 'PB to DMD', 'Former Post Bacc', '3+4',
    ])
    for a in applicants:
        decision = 'Yes' if a.status in ACCEPTED_STATUSES else a.get_status_display()
        writer.writerow([
            a.last_name,
            a.first_name,
            a.email or '',
            a.external_id or '',
            dataset.DisplayName,
            a.round.DisplayName if a.round else '',
            decision,
            a.accept_ct,
            a.deny_ct,
            a.wait_ct,
            'Discuss' if a.flagged_by.exists() else '',
            f"{a.avg_score:.1f}" if a.avg_score else '',
            a.total_ai if a.total_ai is not None else '',
            a.total_nc if a.total_nc is not None else '',
            a.z_score if a.z_score is not None else '',
            'Yes' if a.first_gen else 'No',
            'Yes' if a.re_applicant else 'No',
            'Yes' if a.pb_to_dmd else 'No',
            'Yes' if a.former_post_bacc else 'No',
            'Yes' if a.three_plus_four else 'No',
        ])
    return response

@login_required
@admin_required
def batch_list(request):
    search_query       = request.GET.get('q', '')
    status_filter      = request.GET.get('status', 'all')
    group_filter       = request.GET.get('group', '')
    dataset_id         = request.GET.get('dataset_id', '')
    application_system = request.GET.get('application_system', '')
    program_type       = request.GET.get('program_type', '')
    deadline_before    = request.GET.get('deadline_before', '')
    deadline_after     = request.GET.get('deadline_after', '')
    progress_filter    = request.GET.get('progress', '')
    show_archived      = request.GET.get('show_archived', '')
    sort               = request.GET.get('sort', 'name')
    direction          = request.GET.get('dir', 'asc')

    batches_list = Batch.objects.select_related("DataSet").annotate(
        applicant_count=Count('applicant', distinct=True),
        reviewer_count=Count('assigned_reviewers', distinct=True),
    ).order_by('DataSet__DisplayName', 'DisplayName')  # default; overridden below

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
    if dataset_id:
        batches_list = batches_list.filter(DataSet_id=dataset_id)
    if application_system:
        batches_list = batches_list.filter(DataSet__application_system=application_system)
    if program_type:
        batches_list = batches_list.filter(DataSet__program_type=program_type)
    if deadline_before:
        try:
            from django.utils.dateparse import parse_date
            d = parse_date(deadline_before)
            if d:
                batches_list = batches_list.filter(VoteExpire__date__lte=d)
        except Exception:
            pass
    if deadline_after:
        try:
            from django.utils.dateparse import parse_date
            d = parse_date(deadline_after)
            if d:
                batches_list = batches_list.filter(VoteExpire__date__gte=d)
        except Exception:
            pass
    if not show_archived:
        batches_list = batches_list.filter(DataSet__Active=True)

    DB_SORT_MAP = {
        'name':     'DisplayName',
        'dataset':  'DataSet__DisplayName',
        'deadline': 'VoteExpire',
        'group':    'review_group',
        'status':   'Active',
    }
    if sort in DB_SORT_MAP:
        order_field = DB_SORT_MAP[sort]
        batches_list = batches_list.order_by(
            f'-{order_field}' if direction == 'desc' else order_field
        )
    # ── Compute progress for each batch, then apply progress filter ──
    # (done in Python; batch counts are small)
    all_batches = list(batches_list)
    for batch in all_batches:
        potential = batch.applicant_count * batch.reviewer_count
        actual = Vote.objects.filter(
            applicant__round=batch,
            voter__in=batch.assigned_reviewers.all()
        ).count()
        batch.actual_votes    = actual
        batch.voted_count     = actual
        batch.potential_votes = potential
        batch.progress_pct    = int((actual / potential * 100) if potential > 0 else 0)
        
    if sort == 'candidates':
        all_batches.sort(key=lambda b: b.applicant_count, reverse=(direction == 'desc'))
    elif sort == 'progress':
        all_batches.sort(key=lambda b: b.progress_pct, reverse=(direction == 'desc'))

    if progress_filter == 'complete':
        all_batches = [b for b in all_batches if b.applicant_count > 0 and b.progress_pct == 100]
    elif progress_filter == 'in_progress':
        all_batches = [b for b in all_batches if 0 < b.progress_pct < 100]
    elif progress_filter == 'not_started':
        all_batches = [b for b in all_batches if b.progress_pct == 0]

    paginator   = Paginator(all_batches, 25)
    page_obj    = paginator.get_page(request.GET.get("page"))

    all_datasets = DataSet.objects.filter(Active=True).order_by('DisplayName')

    return render(request, "batch_list.html", {
        'page_obj':                   page_obj,
        'search_query':               search_query,
        'status_filter':              status_filter,
        'group_filter':               group_filter,
        'dataset_id':                 dataset_id,
        'application_system':         application_system,
        'program_type':               program_type,
        'deadline_before':            deadline_before,
        'deadline_after':             deadline_after,
        'progress_filter':            progress_filter,
        'show_archived':              show_archived,
        'all_datasets':               all_datasets,
        'sort':                       sort,
        'direction':                  direction,
        'application_system_choices': DataSet.ApplicationSystem.choices,
        'program_type_choices':       DataSet.ProgramType.choices,
        'crumbs': [{'label': 'Batches', 'url': ''}],
    })

@login_required
@admin_required
def batch_create(request):
    if request.method == "POST":
        form = BatchForm(request.POST)
        if form.is_valid():
            batch = form.save()
            # Handle panel assignment
            panel_id = request.POST.get('panel')
            if panel_id:
                try:
                    from .models import ReviewPanel
                    panel = ReviewPanel.objects.get(pk=panel_id)
                    batch.panel = panel
                    batch.save()
                    batch.assigned_reviewers.set(panel.members.all())
                except ReviewPanel.DoesNotExist:
                    pass
            messages.success(request, f"Batch '{batch.DisplayName}' created successfully.")
            return redirect("batch_detail", pk=batch.pk)
    else:
        form = BatchForm()
    return render(request, "batch_form.html", {
        "form": form,
        "batch": None,
        "available_panels": ReviewPanel.objects.all(),
        "crumbs": [
            {'label': 'Batches', 'url': '/batches/'},
            {'label': 'New Batch', 'url': ''},
        ],
    })


@admin_required
@login_required
def batch_detail(request, pk):
    batch        = get_object_or_404(Batch, pk=pk)
    search_query = request.GET.get('q', '')
    status_filter       = request.GET.get('status', '')
    voted_filter        = request.GET.get('voted', '')
    flagged_only        = request.GET.get('flagged_only', '')
    filter_first_gen    = request.GET.get('first_gen', '')
    filter_re_applicant = request.GET.get('re_applicant', '')
    filter_pb_to_dmd    = request.GET.get('pb_to_dmd', '')
    filter_former_pb    = request.GET.get('former_post_bacc', '')
    filter_3p4          = request.GET.get('three_plus_four', '')

    has_advanced = any([
        filter_first_gen, filter_re_applicant, filter_pb_to_dmd,
        filter_former_pb, filter_3p4,
    ])
    has_filters = any([
        search_query, status_filter, voted_filter, flagged_only,
        filter_first_gen, filter_re_applicant, filter_pb_to_dmd,
        filter_former_pb, filter_3p4,
    ])

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
    if status_filter:
        applicants = applicants.filter(status=status_filter)
    if voted_filter == 'voted':
        applicants = applicants.filter(vote_count__gt=0)
    elif voted_filter == 'not_voted':
        applicants = applicants.filter(vote_count=0)
    if flagged_only:
        applicants = applicants.filter(flagged_by__isnull=False).distinct()
    if filter_first_gen:
        applicants = applicants.filter(first_gen=True)
    if filter_re_applicant:
        applicants = applicants.filter(re_applicant=True)
    if filter_pb_to_dmd:
        applicants = applicants.filter(pb_to_dmd=True)
    if filter_former_pb:
        applicants = applicants.filter(former_post_bacc=True)
    if filter_3p4:
        applicants = applicants.filter(three_plus_four=True)

    assigned_reviewers = batch.assigned_reviewers.all()
    reviewer_count = assigned_reviewers.count()

    # All stats and reviewer progress now reflect the filtered candidate set.
    filtered_pks = set(applicants.values_list('pk', flat=True))
    filtered_total = len(filtered_pks)

    actual_votes = Vote.objects.filter(
        applicant__pk__in=filtered_pks,
        voter__in=assigned_reviewers,
    ).count()
    potential_votes = filtered_total * reviewer_count
    progress_pct = round((actual_votes / potential_votes) * 100) if potential_votes > 0 else 0
    pending = potential_votes - actual_votes
    total = filtered_total

    reviewer_progress = []
    for reviewer in assigned_reviewers.select_related('profile'):
        votes_cast = Vote.objects.filter(
            applicant__pk__in=filtered_pks,
            voter=reviewer,
        ).count()
        pct = round((votes_cast / filtered_total) * 100) if filtered_total > 0 else 0
        reviewer_progress.append({
            'user': reviewer,
            'votes_cast': votes_cast,
            'total': filtered_total,
            'pct': pct,
        })
    reviewer_progress.sort(key=lambda r: r['pct'], reverse=True)

    from collections import OrderedDict
    grouped_applicants = OrderedDict()
    for a in applicants:
        grouped_applicants.setdefault(a.source_folder or '', []).append(a)
    has_multiple_groups = len(grouped_applicants) > 1 or (
        len(grouped_applicants) == 1 and '' not in grouped_applicants
    )

    context = {
        'batch':               batch,
        'applicants':          applicants,
        'grouped_applicants':  grouped_applicants,
        'has_multiple_groups': has_multiple_groups,
        'total':               total,
        'reviewer_count':      reviewer_count,
        'actual_votes':        actual_votes,
        'pending':             pending,
        'progress_pct':        progress_pct,
        'search_query':        search_query,
        'status_filter':       status_filter,
        'voted_filter':        voted_filter,
        'flagged_only':        flagged_only,
        'filter_first_gen':    filter_first_gen,
        'filter_re_applicant': filter_re_applicant,
        'filter_pb_to_dmd':    filter_pb_to_dmd,
        'filter_former_pb':    filter_former_pb,
        'filter_3p4':          filter_3p4,
        'has_advanced':        has_advanced,
        'has_filters':         has_filters,
        'reviewer_progress':   reviewer_progress,
        'crumbs': [
            {'label': 'Batches', 'url': '/batches/'},
            {'label': batch.DisplayName},
        ],
    }
    return render(request, 'batch_detail.html', context)

@login_required
@admin_required
def batch_edit(request, pk):
    from .models import ReviewPanel
    batch = get_object_or_404(Batch, pk=pk)
    if request.method == "POST":
        form = BatchForm(request.POST, instance=batch)
        if form.is_valid():
            batch = form.save()
            # Handle panel assignment
            panel_id = request.POST.get('panel')
            if panel_id:
                try:
                    panel = ReviewPanel.objects.get(pk=panel_id)
                    batch.panel = panel
                    batch.save()
                    batch.assigned_reviewers.set(panel.members.all())
                except ReviewPanel.DoesNotExist:
                    pass
            else:
                batch.panel = None
                batch.save()
            messages.success(request, f"Batch '{batch.DisplayName}' updated successfully.")
            return redirect("batch_detail", pk=batch.pk)
    else:
        form = BatchForm(instance=batch)
    return render(request, "batch_form.html", {
        "form": form,
        "batch": batch,
        "available_panels": ReviewPanel.objects.all(),
        "crumbs": [
            {'label': 'Batches', 'url': '/batches/'},
            {'label': batch.DisplayName, 'url': f'/batches/{batch.pk}/'},
            {'label': 'Edit', 'url': ''},
        ],
    })

@login_required
def dashboard(request):
    if request.user.profile.role != 'ADMIN':
        return redirect('committee_dashboard')
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
            action_type__in=[Activity.VOTE_CAST, Activity.COMMENT_ADDED, Activity.DECISION_MADE, Activity.FLAG_ADDED, Activity.BATCH_UPLOADED]
        ).select_related('actor', 'target_applicant', 'target_batch')[:7]

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
    if len(applicant_ids) > 4:
        messages.warning(request, "You can compare up to 4 applicants at a time. Please select fewer.")
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



@login_required
def export_applicants_csv(request):
    if request.user.profile.role != 'ADMIN':
        return HttpResponse('Exports are not available for committee members.', status=403)

    selected_dataset_id = request.GET.get('dataset')
    selected_batch_id   = request.GET.get('batch')
    search_query        = request.GET.get('q', '')
    status_filter       = request.GET.get('status', '')
    flagged_only        = request.GET.get('flagged_only', '')
    show_waitlist       = request.GET.get('show_waitlist', '')

    if request.user.profile.role == 'ADMIN':
        applicants = Applicant.objects.all().order_by("-created_at")
        if selected_batch_id:
            applicants = applicants.filter(round_id=selected_batch_id)
        elif selected_dataset_id:
            applicants = applicants.filter(dataset_id=selected_dataset_id)
        if search_query:
            applicants = applicants.filter(
                Q(first_name__icontains=search_query) |
                Q(last_name__icontains=search_query) |
                Q(external_id__icontains=search_query)
            )
        if status_filter:
            applicants = applicants.filter(status=status_filter)
    else:
        assigned_batches = request.user.assigned_batches.all()
        applicants = Applicant.objects.filter(
            round__in=assigned_batches
        ).order_by("-created_at")
        if selected_batch_id:
            applicants = applicants.filter(round_id=selected_batch_id)
        if search_query:
            applicants = applicants.filter(
                Q(first_name__icontains=search_query) |
                Q(last_name__icontains=search_query) |
                Q(external_id__icontains=search_query)
            )
        if status_filter:
            applicants = applicants.filter(status=status_filter)
        if flagged_only:
            applicants = applicants.filter(flagged_by=request.user)
        if not show_waitlist:
            applicants = applicants.exclude(
                votes__voter=request.user,
                votes__value__in=[1, -1],
            )

    response = HttpResponse(
        content_type='text/csv',
        headers={'Content-Disposition': 'attachment; filename="applicants.csv"'},
    )
    writer = csv.writer(response)
    writer.writerow([
        'First Name', 'Last Name', 'Email', 'External ID', 'Date of Birth', 'Gender',
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
    if request.GET.get('export') == 'csv':
        return _export_activity_feed_csv(request)
 
    filter_reviewer  = request.GET.get('reviewer', '')
    filter_action    = request.GET.get('action', '')
    filter_date_from = request.GET.get('date_from', '')
    filter_date_to   = request.GET.get('date_to', '')
    has_filters = any([filter_reviewer, filter_action, filter_date_from, filter_date_to])
 
    activities = Activity.objects.filter(
        action_type__in=[
            Activity.VOTE_CAST,
            Activity.COMMENT_ADDED,
            Activity.DECISION_MADE,
            Activity.FLAG_ADDED,
            Activity.BATCH_UPLOADED,
        ]
    ).select_related('actor', 'target_applicant', 'target_batch').order_by('-created_at')
 
    if filter_reviewer:
        activities = activities.filter(actor_id=filter_reviewer)
    if filter_action:
        activities = activities.filter(action_type=filter_action)
    if filter_date_from:
        activities = activities.filter(created_at__date__gte=filter_date_from)
    if filter_date_to:
        activities = activities.filter(created_at__date__lte=filter_date_to)
 
    paginator = Paginator(activities, 50)
    page_obj  = paginator.get_page(request.GET.get('page'))
 
    all_reviewers = User.objects.filter(
        Q(profile__role='COMMITTEE_MEMBER') |
        Q(profile__role='ADMIN', profile__is_reviewer=True)
    ).order_by('username')
 
    return render(request, "activity_feed.html", {
        'page_obj':         page_obj,
        'filter_reviewer':  filter_reviewer,
        'filter_action':    filter_action,
        'filter_date_from': filter_date_from,
        'filter_date_to':   filter_date_to,
        'has_filters':      has_filters,
        'all_reviewers':    all_reviewers,
        'action_choices':   Activity.ACTION_CHOICES,
        'total_count':      paginator.count,
    })
 
 
def _export_activity_feed_csv(request):
    filter_reviewer  = request.GET.get('reviewer', '')
    filter_action    = request.GET.get('action', '')
    filter_date_from = request.GET.get('date_from', '')
    filter_date_to   = request.GET.get('date_to', '')
 
    activities = Activity.objects.filter(
        action_type__in=[
            Activity.VOTE_CAST,
            Activity.COMMENT_ADDED,
            Activity.DECISION_MADE,
            Activity.FLAG_ADDED,
            Activity.BATCH_UPLOADED,
        ]
    ).select_related('actor', 'target_applicant', 'target_batch').order_by('-created_at')
 
    if filter_reviewer:
        activities = activities.filter(actor_id=filter_reviewer)
    if filter_action:
        activities = activities.filter(action_type=filter_action)
    if filter_date_from:
        activities = activities.filter(created_at__date__gte=filter_date_from)
    if filter_date_to:
        activities = activities.filter(created_at__date__lte=filter_date_to)
 
    response = HttpResponse(
        content_type='text/csv',
        headers={'Content-Disposition': 'attachment; filename="activity_feed.csv"'},
    )
    writer = csv.writer(response)
    writer.writerow(['Date', 'Reviewer', 'Action', 'Candidate / Batch', 'Details'])
    for a in activities:
        if a.action_type == Activity.BATCH_UPLOADED:
            target_label = str(a.target_batch) if a.target_batch else ''
        else:
            target_label = str(a.target_applicant) if a.target_applicant else ''
        writer.writerow([
            a.created_at.strftime('%Y-%m-%d %H:%M'),
            a.actor.get_full_name() or a.actor.username if a.actor else 'System',
            a.get_action_type_display(),
            target_label,
            a.details,
        ])
    return response
 
 
def _export_activity_feed_csv(request):
    filter_reviewer  = request.GET.get('reviewer', '')
    filter_action    = request.GET.get('action', '')
    filter_date_from = request.GET.get('date_from', '')
    filter_date_to   = request.GET.get('date_to', '')
 
    activities = Activity.objects.filter(
        action_type__in=[
            Activity.VOTE_CAST,
            Activity.COMMENT_ADDED,
            Activity.DECISION_MADE,
            Activity.FLAG_ADDED,
        ]
    ).select_related('actor', 'target_applicant').order_by('-created_at')
 
    if filter_reviewer:
        activities = activities.filter(actor_id=filter_reviewer)
    if filter_action:
        activities = activities.filter(action_type=filter_action)
    if filter_date_from:
        activities = activities.filter(created_at__date__gte=filter_date_from)
    if filter_date_to:
        activities = activities.filter(created_at__date__lte=filter_date_to)
 
    response = HttpResponse(
        content_type='text/csv',
        headers={'Content-Disposition': 'attachment; filename="activity_feed.csv"'},
    )
    writer = csv.writer(response)
    writer.writerow(['Date', 'Reviewer', 'Action', 'Candidate', 'Details'])
    for a in activities:
        writer.writerow([
            a.created_at.strftime('%Y-%m-%d %H:%M'),
            a.actor.get_full_name() or a.actor.username if a.actor else 'System',
            a.get_action_type_display(),
            str(a.target_applicant) if a.target_applicant else '',
            a.details,
        ])
    return response

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
@committee_access_required
def applicant_queue(request):
    batches = request.user.assigned_batches.all()
    selected_batch_id = request.GET.get('batch')
    search_query  = request.GET.get('q', '')
    status_filter = request.GET.get('status', '')
    flagged_only  = request.GET.get('flagged_only', '')
    show_waitlist = request.GET.get('show_waitlist', '')
 
    user_has_voted_subquery = Vote.objects.filter(
        applicant=OuterRef('pk'),
        voter=request.user
    )
 
    # Default: exclude yes/no/waitlist. With show_waitlist: only exclude yes/no.
    exclude_values = [1, -1] if show_waitlist else [1, -1, 0]
 
    applicants_list = Applicant.objects.filter(
        round__in=batches
    ).exclude(
        votes__voter=request.user,
        votes__value__in=exclude_values,
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
    if status_filter:
        applicants_list = applicants_list.filter(status=status_filter)
    if flagged_only:
        applicants_list = applicants_list.filter(flagged_by=request.user)
 
    # Progress — always based on yes/no regardless of show_waitlist
    filtered_unvoted = applicants_list.count()
    batch_filter_qs = batches.filter(pk=selected_batch_id) if selected_batch_id else batches
    voted_in_scope = Vote.objects.filter(
        voter=request.user,
        value__in=[1, -1],
        applicant__round__in=batch_filter_qs,
    )
    if search_query:
        voted_in_scope = voted_in_scope.filter(
            Q(applicant__first_name__icontains=search_query) |
            Q(applicant__last_name__icontains=search_query) |
            Q(applicant__external_id__icontains=search_query)
        )
    if status_filter:
        voted_in_scope = voted_in_scope.filter(applicant__status=status_filter)
 
    voted_count    = voted_in_scope.count()
    total_assigned = filtered_unvoted + voted_count
    progress_pct   = int((voted_count / total_assigned * 100)) if total_assigned > 0 else 0
 
    # Per-batch remaining counts for dropdown labels
    batches_with_counts = []
    for b in batches:
        b.remaining_count = Applicant.objects.filter(
            round=b
        ).exclude(
            votes__voter=request.user,
            votes__value__in=[1, -1],
        ).count()
        batches_with_counts.append(b)
 
    paginator   = Paginator(applicants_list, 25)
    page_obj    = paginator.get_page(request.GET.get("page"))
 
    context = {
        'page_obj':          page_obj,
        'batches':           batches_with_counts,
        'selected_batch_id': selected_batch_id,
        'search_query':      search_query,
        'is_queue_page':     True,
        'voted_count':       voted_count,
        'total_assigned':    total_assigned,
        'progress_pct':      progress_pct,
        'status_filter':     status_filter,
        'flagged_only':      flagged_only,
        'show_waitlist':     show_waitlist,
        'program_type':            '',
        'application_system':      '',
        'filter_first_gen':        '',
        'filter_re_applicant':     '',
        'filter_pb_to_dmd':        '',
        'filter_former_pb':        '',
        'filter_three_plus_four':  '',
        'has_advanced':            False,
        'program_type_choices':    DataSet.ProgramType.choices,
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
        elif action == 'set_status_accepted':
            count = queryset.update(status=Applicant.Status.ACCEPTED)
            messages.success(request, f"Updated {count} applicants to 'Accepted'.")
        elif action == 'set_status_waitlisted':
            count = queryset.update(status=Applicant.Status.WAITLISTED)
            messages.success(request, f"Updated {count} applicants to 'Waitlisted'.")
        elif action == 'set_status_rejected':
            count = queryset.update(status=Applicant.Status.REJECTED)
            messages.success(request, f"Updated {count} applicants to 'Rejected'.")
        else:
            messages.error(request, "No valid action selected.")

    return redirect('applicant_list')
@login_required
@admin_required
def global_search(request):
    query = request.GET.get('q', '').strip()
    dataset_id = request.GET.get('dataset', '')
    batch_id = request.GET.get('batch', '')
    status_filter = request.GET.get('status', '')
    reviewer_id = request.GET.get('reviewer', '')
    vote_value = request.GET.get('vote_value', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    has_filters = any([query, dataset_id, batch_id, status_filter, reviewer_id, vote_value, date_from, date_to])

    candidates = comments = flags = votes = batches = datasets = None

    if has_filters:
        # ── Candidates ────────────────────────────────────────────────
        candidates = Applicant.objects.select_related('dataset', 'round').annotate(
            avg_score=Avg('scores__overall_score'),
            accept_ct=Count('votes', filter=Q(votes__value=1),  distinct=True),
            deny_ct=Count('votes',   filter=Q(votes__value=-1), distinct=True),
            wait_ct=Count('votes',   filter=Q(votes__value=0),  distinct=True),
        )
        if query:
            candidates = candidates.filter(
                Q(first_name__icontains=query) |
                Q(last_name__icontains=query) |
                Q(email__icontains=query) |
                Q(external_id__icontains=query)
            )
        if dataset_id:
            candidates = candidates.filter(dataset_id=dataset_id)
        if batch_id:
            candidates = candidates.filter(round_id=batch_id)
        if status_filter:
            candidates = candidates.filter(status=status_filter)
        candidates = candidates.order_by('last_name', 'first_name')[:50]

        # ── Comments ──────────────────────────────────────────────────
        comments = Comment.objects.select_related('author', 'applicant', 'applicant__dataset', 'applicant__round')
        if query:
            comments = comments.filter(
                Q(text__icontains=query) |
                Q(author__username__icontains=query) |
                Q(author__first_name__icontains=query) |
                Q(author__last_name__icontains=query)
            )
        if dataset_id:
            comments = comments.filter(applicant__dataset_id=dataset_id)
        if batch_id:
            comments = comments.filter(applicant__round_id=batch_id)
        if reviewer_id:
            comments = comments.filter(author_id=reviewer_id)
        if date_from:
            comments = comments.filter(created_at__date__gte=date_from)
        if date_to:
            comments = comments.filter(created_at__date__lte=date_to)
        comments = comments.order_by('-created_at')[:50]

        # ── Flags ─────────────────────────────────────────────────────
        flags = Flag.objects.select_related('user', 'applicant', 'applicant__dataset', 'applicant__round')
        if query:
            flags = flags.filter(
                Q(comment__icontains=query) |
                Q(user__username__icontains=query) |
                Q(applicant__first_name__icontains=query) |
                Q(applicant__last_name__icontains=query)
            )
        if dataset_id:
            flags = flags.filter(applicant__dataset_id=dataset_id)
        if batch_id:
            flags = flags.filter(applicant__round_id=batch_id)
        if reviewer_id:
            flags = flags.filter(user_id=reviewer_id)
        if date_from:
            flags = flags.filter(created_at__date__gte=date_from)
        if date_to:
            flags = flags.filter(created_at__date__lte=date_to)
        flags = flags.order_by('-created_at')[:50]

        # ── Votes ─────────────────────────────────────────────────────
        votes = Vote.objects.select_related('voter', 'applicant', 'applicant__dataset', 'applicant__round')
        if query:
            votes = votes.filter(
                Q(voter__username__icontains=query) |
                Q(voter__first_name__icontains=query) |
                Q(voter__last_name__icontains=query) |
                Q(applicant__first_name__icontains=query) |
                Q(applicant__last_name__icontains=query) |
                Q(applicant__external_id__icontains=query)
            )
        if dataset_id:
            votes = votes.filter(applicant__dataset_id=dataset_id)
        if batch_id:
            votes = votes.filter(applicant__round_id=batch_id)
        if reviewer_id:
            votes = votes.filter(voter_id=reviewer_id)
        if vote_value != '':
            votes = votes.filter(value=vote_value)
        if date_from:
            votes = votes.filter(created_at__date__gte=date_from)
        if date_to:
            votes = votes.filter(created_at__date__lte=date_to)
        votes = votes.order_by('-created_at')[:50]

        # ── Batches ───────────────────────────────────────────────────
        batches_qs = Batch.objects.select_related('DataSet').annotate(
            applicant_count=Count('applicant', distinct=True),
        )
        if query:
            batches_qs = batches_qs.filter(
                Q(DisplayName__icontains=query) |
                Q(DataSet__DisplayName__icontains=query)
            )
        if dataset_id:
            batches_qs = batches_qs.filter(DataSet_id=dataset_id)
        batches_qs = batches_qs.order_by('DataSet__DisplayName', 'DisplayName')[:50]

        # ── Datasets ──────────────────────────────────────────────────
        datasets = DataSet.objects.annotate(
            applicant_count=Count('applicants', distinct=True),
            batch_count=Count('batches', distinct=True),
        )
        if query:
            datasets = datasets.filter(
                Q(DisplayName__icontains=query) |
                Q(Description__icontains=query)
            )
        datasets = datasets.order_by('DisplayName')[:20]

    else:
        batches_qs = Batch.objects.none()

    all_datasets = DataSet.objects.filter(Active=True).order_by('DisplayName')
    all_batches = Batch.objects.select_related('DataSet').order_by('DataSet__DisplayName', 'DisplayName')
    all_reviewers = User.objects.filter(profile__role='COMMITTEE_MEMBER').order_by('username')

    return render(request, 'global_search.html', {
        'query': query,
        'dataset_id': dataset_id,
        'batch_id': batch_id,
        'status_filter': status_filter,
        'reviewer_id': reviewer_id,
        'vote_value': vote_value,
        'date_from': date_from,
        'date_to': date_to,
        'has_filters': has_filters,
        'candidates': candidates,
        'comments': comments,
        'flags': flags,
        'votes': votes,
        'batches': batches_qs,
        'datasets': datasets,
        'all_datasets': all_datasets,
        'all_batches': all_batches,
        'all_reviewers': all_reviewers,
        'status_choices': Applicant.Status.choices,
        'vote_choices': Vote.VOTE_CHOICES,
    })
    
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
def toggle_committee_mode(request):
    """
    Toggle committee mode for sitting admin-reviewers (is_reviewer=True).
    Entering committee mode: redirects to committee dashboard.
    Leaving committee mode: redirects to admin dashboard.
    """
    profile = request.user.profile
 
    # Only admins with is_reviewer=True can toggle
    if profile.role != 'ADMIN' or not profile.is_reviewer:
        messages.error(request, "You do not have permission to switch to committee mode.")
        return redirect('dashboard')
 
    if request.session.get('committee_mode'):
        del request.session['committee_mode']
        return redirect('dashboard')
    else:
        request.session['committee_mode'] = True
        return redirect('committee_dashboard')
 
 
@login_required
@admin_required
@require_POST
def toggle_reviewer_status(request):
    """
    Toggle is_reviewer on an admin Profile.
    Called from the Manage Panels page — Admin Reviewers section.
    """
    user_id = request.POST.get('user_id')
    try:
        profile = User.objects.get(pk=user_id, profile__role='ADMIN').profile
        profile.is_reviewer = not profile.is_reviewer
        profile.save()
        status = "enabled" if profile.is_reviewer else "disabled"
        messages.success(request, f"Committee reviewer access {status} for {profile.user.username}.")
    except User.DoesNotExist:
        messages.error(request, "Admin user not found.")
    return redirect('manage_panels')


@require_POST
@login_required
def toggle_applicant_flag(request, pk):
    applicant = get_object_or_404(Applicant, pk=pk)
    
    # Preserve nav params for redirect back
    nav_params = request.POST.get('nav_params', '')
    redirect_url = f'/applicant/{pk}/?{nav_params}' if nav_params else f'/applicant/{pk}/'

    # Admin: clear all flags
    if request.POST.get('clear_all') and request.user.profile.role == 'ADMIN':
        applicant.flags.all().delete()
        applicant.flagged_by.clear()
        Activity.objects.create(
            actor=request.user,
            action_type=Activity.FLAG_ADDED,
            details="cleared all flags on",
            target_applicant=applicant,
        )
        messages.success(request, "All flags cleared.")
        return redirect(redirect_url)

    existing_flag = Flag.objects.filter(applicant=applicant, user=request.user).first()

    if existing_flag:
        existing_flag.delete()
        applicant.flagged_by.remove(request.user)
        Activity.objects.create(
            actor=request.user,
            action_type=Activity.FLAG_ADDED,
            details="removed their flag on",
            target_applicant=applicant,
        )
        messages.success(request, "Flag removed.")
    else:
        comment = request.POST.get('flag_comment', '').strip()
        if len(comment) < 10:
            messages.error(request, "Please provide a reason (at least 10 characters).")
            return redirect(redirect_url)
        Flag.objects.create(applicant=applicant, user=request.user, comment=comment)
        applicant.flagged_by.add(request.user)
        Activity.objects.create(
            actor=request.user,
            action_type=Activity.FLAG_ADDED,
            details=f"flagged: {comment[:80]}",
            target_applicant=applicant,
        )
        messages.success(request, "Candidate flagged for discussion.")

    return redirect(redirect_url)

@login_required
def help_page(request):
    return render(request, "help.html")

@login_required
@admin_required
def bulk_upload_applicants(request):
    if request.method == "POST":
        import re as _re
        files = request.FILES.getlist('folder_files')
        path_map_raw = request.POST.get('path_map')

        if path_map_raw:
            import json
            path_map = json.loads(path_map_raw)
            candidate_groups = {}
            folder_name_map = {}
            for i, f in enumerate(files):
                relative_path = path_map.get(str(i), f.name)
                parts = relative_path.replace('\\', '/').split('/')
                if len(parts) < 3:
                    continue
                if ' - ' in _normalize_dashes(parts[1]):
                    # Flat: batch_folder / candidate_folder / file(s)
                    batch_folder     = parts[0]
                    candidate_folder = parts[1]
                else:
                    # Deep: top_folder / batch_date / candidate_folder / file(s)
                    batch_folder     = parts[1]
                    candidate_folder = parts[2]
                candidate_groups.setdefault(candidate_folder, []).append(f)
                folder_name_map[candidate_folder] = batch_folder
        else:
            candidate_groups = {}
            folder_name_map = {}
            for f in files:
                parts = f.name.replace('\\', '/').split('/')
                if len(parts) < 3:
                    continue
                if ' - ' in _normalize_dashes(parts[1]):
                    batch_folder     = parts[0]
                    candidate_folder = parts[1]
                else:
                    batch_folder     = parts[1]
                    candidate_folder = parts[2]
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
        

        created_count = 0
        skipped_count = 0

        # ── Parse all candidates first ───────────────────────────────────
        parsed_candidates = []

        for folder_name, group_files in candidate_groups.items():
            # ── Parse "Last, First - UniqueID" ───────────────────────────
            import re as _re
            match = _re.match(r'^(.+?)\s*-\s*(\w+)$', _normalize_dashes(folder_name).strip())
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

            pdf_data = {}
            email = None

            # Prioritise the full application PDF (contains all structured data).
            # Look for a PDF with 'application' in the name first; fall back to
            # any PDF if none found.
            pdf_to_parse = None
            fallback_pdf = None
            for f in group_files:
                fname_lower = f.name.lower()
                if fname_lower.endswith('.db') or fname_lower.endswith('.ini'):
                    continue
                if fname_lower.endswith('.pdf'):
                    if 'application' in fname_lower:
                        pdf_to_parse = f
                        break
                    elif fallback_pdf is None:
                        fallback_pdf = f

            if pdf_to_parse is None:
                pdf_to_parse = fallback_pdf

            if pdf_to_parse is not None:
                try:
                    pdf_data = _extract_pdf_data(pdf_to_parse)
                    email = pdf_data.get('email')
                    pdf_to_parse.seek(0)
                except Exception as e:
                    print(f"PDF parse error for {pdf_to_parse.name}: {e}")

            jpg_files = [f for f in group_files if f.name.lower().endswith(('.jpg', '.jpeg'))]
            profile_pic = jpg_files[2] if len(jpg_files) >= 3 else None

            source = folder_name_map.get(folder_name, '')

            parsed_candidates.append({
                'first_name':   first_name,
                'last_name':    last_name,
                'email':        email,
                'unique_id':    unique_id,
                'profile_pic':  profile_pic,
                'group_files':  group_files,
                'source_folder': source,
                'pdf_data':     pdf_data,
            })
        
        # ── Place candidates into auto-created batches ───────────────────
        batch_placements = _assign_candidates_to_batches(
            dataset=selected_dataset,
            folder_name='Bulk Upload',
            candidates=parsed_candidates,
        )

        batch_summary = []

        for batch_obj, candidate_chunk in batch_placements:
            for cand in candidate_chunk:
                applicant = Applicant.objects.create(
                    first_name=cand['first_name'],
                    last_name=cand['last_name'],
                    email=cand['email'],
                    date_of_birth=cand['pdf_data'].get('date_of_birth'),
                    gender=cand['pdf_data'].get('gender', ''),
                    phone=cand['pdf_data'].get('phone'),
                    citizenship=cand['pdf_data'].get('citizenship'),
                    state_of_residence=cand['pdf_data'].get('state_of_residence'),
                    dataset=selected_dataset,
                    round=batch_obj,
                    external_id=cand['unique_id'] or None,
                    profile_picture=cand['profile_pic'],
                    source_folder=cand['source_folder'],
                    # GPA
                    gpa_overall=cand['pdf_data'].get('gpa_overall'),
                    gpa_science=cand['pdf_data'].get('gpa_science'),
                    gpa_bcp=cand['pdf_data'].get('gpa_bcp'),
                    # DAT
                    dat_academic_avg=cand['pdf_data'].get('dat_academic_avg'),
                    dat_perceptual_ability=cand['pdf_data'].get('dat_perceptual_ability'),
                    dat_quantitative_reasoning=cand['pdf_data'].get('dat_quantitative_reasoning'),
                    dat_reading_comp=cand['pdf_data'].get('dat_reading_comp'),
                    dat_biology=cand['pdf_data'].get('dat_biology'),
                    dat_general_chem=cand['pdf_data'].get('dat_general_chem'),
                    dat_organic_chem=cand['pdf_data'].get('dat_organic_chem'),
                    dat_total_science=cand['pdf_data'].get('dat_total_science'),
                    # Experience
                    dental_experience_hours=cand['pdf_data'].get('dental_experience_hours'),
                    shadowing_hours=cand['pdf_data'].get('shadowing_hours'),
                    # Flags
                    first_gen=cand['pdf_data'].get('first_gen', False),
                    candidate_info_imported=bool(cand['pdf_data']),
                )

                # ── Attach all files ─────────────────────────────────────
                for f in cand['group_files']:
                    # Skip junk files generated by Windows
                    skip_names = {'thumbs.db', 'desktop.ini', '.ds_store'}
                    if f.name.lower().split('/')[-1] in skip_names:
                        continue
                    ApplicantFile.objects.create(applicant=applicant, file=f)

                created_count += 1

            count_in_batch = Applicant.objects.filter(round=batch_obj).count()
            batch_summary.append(f'"{batch_obj.DisplayName}" ({count_in_batch}/{BATCH_MAX_SIZE})')

        for batch_obj, candidate_chunk in batch_placements:
            Activity.objects.create(
                actor=request.user,
                action_type=Activity.BATCH_UPLOADED,
                details=f"uploaded {len(candidate_chunk)} candidate(s) to",
                target_applicant=None,
                target_batch=batch_obj,
            )
 
        batch_details = ', '.join(batch_summary)
        messages.success(
            request,
            f"✅ Successfully imported {created_count} candidate profile(s) "
            f"into {len(batch_placements)} batch(es): {batch_details}. "
            f"Review each profile to verify the extracted details are correct."
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
            # Existing fields
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
            # New GPA fields
            'gpa_overall':      ('gpa_overall',      parse_decimal),
            'overall_gpa':      ('gpa_overall',      parse_decimal),
            'gpa_science':      ('gpa_science',      parse_decimal),
            'science_gpa':      ('gpa_science',      parse_decimal),
            'gpa_bcp':          ('gpa_bcp',          parse_decimal),
            'bcp_gpa':          ('gpa_bcp',          parse_decimal),
            # New DAT fields
            'dat_academic_avg':            ('dat_academic_avg',           lambda v: int(float(str(v).strip())) if pd.notna(v) else None),
            'dat_academic_average':        ('dat_academic_avg',           lambda v: int(float(str(v).strip())) if pd.notna(v) else None),
            'academic_average':            ('dat_academic_avg',           lambda v: int(float(str(v).strip())) if pd.notna(v) else None),
            'dat_perceptual_ability':      ('dat_perceptual_ability',     lambda v: int(float(str(v).strip())) if pd.notna(v) else None),
            'dat_quantitative_reasoning':  ('dat_quantitative_reasoning', lambda v: int(float(str(v).strip())) if pd.notna(v) else None),
            'dat_reading_comp':            ('dat_reading_comp',           lambda v: int(float(str(v).strip())) if pd.notna(v) else None),
            'dat_reading_comprehension':   ('dat_reading_comp',           lambda v: int(float(str(v).strip())) if pd.notna(v) else None),
            'dat_biology':                 ('dat_biology',                lambda v: int(float(str(v).strip())) if pd.notna(v) else None),
            'dat_general_chem':            ('dat_general_chem',           lambda v: int(float(str(v).strip())) if pd.notna(v) else None),
            'dat_general_chemistry':       ('dat_general_chem',           lambda v: int(float(str(v).strip())) if pd.notna(v) else None),
            'dat_organic_chem':            ('dat_organic_chem',           lambda v: int(float(str(v).strip())) if pd.notna(v) else None),
            'dat_organic_chemistry':       ('dat_organic_chem',           lambda v: int(float(str(v).strip())) if pd.notna(v) else None),
            'dat_total_science':           ('dat_total_science',          lambda v: int(float(str(v).strip())) if pd.notna(v) else None),
            # New experience fields
            'dental_experience_hours':     ('dental_experience_hours',    lambda v: int(float(str(v).strip())) if pd.notna(v) else None),
            'dental_hours':                ('dental_experience_hours',    lambda v: int(float(str(v).strip())) if pd.notna(v) else None),
            'shadowing_hours':             ('shadowing_hours',            lambda v: int(float(str(v).strip())) if pd.notna(v) else None),
            # Contact fields (less common in Excel but supported)
            'phone':                       ('phone',                      lambda v: str(v).strip() if pd.notna(v) and str(v).strip() else None),
            'citizenship':                 ('citizenship',                lambda v: str(v).strip() if pd.notna(v) and str(v).strip() else None),
            'state_of_residence':          ('state_of_residence',         lambda v: str(v).strip() if pd.notna(v) and str(v).strip() else None),
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
    
def _normalize_dashes(s):
    """Normalize en/em/figure/minus dash variants to a plain hyphen-minus."""
    if not s:
        return s
    for ch in ('\u2010', '\u2011', '\u2012', '\u2013', '\u2014', '\u2015', '\u2212'):
        s = s.replace(ch, '-')
    return s

_MONTHS = {
    'january': 1, 'jan': 1, 'february': 2, 'feb': 2, 'march': 3, 'mar': 3,
    'april': 4, 'apr': 4, 'may': 5, 'june': 6, 'jun': 6, 'july': 7, 'jul': 7,
    'august': 8, 'aug': 8, 'september': 9, 'sep': 9, 'sept': 9,
    'october': 10, 'oct': 10, 'november': 11, 'nov': 11, 'december': 12, 'dec': 12,
}

def _get_batch_folder_names(batch):
    """
    Extract the list of source folder names from a batch's DisplayName.

    "December 1 - December 2"  →  ["December 1", "December 2"]
    "December 1 (2)"           →  ["December 1"]
    "December 1"               →  ["December 1"]
    """
    name = batch.DisplayName
    # Strip overflow suffix like " (2)"
    base = _re.sub(r'\s*\(\d+\)\s*$', '', name)
    
    return [part.strip() for part in base.split(' - ') if part.strip()]

def _parse_date_key(folder_name):
    """
    Sortable calendar key for a date-style folder name.
    'September 3' / 'Sept 3' / 'December 1 Testing'  -> (0, month, day)
    '2024-09-03' / '09-03-2024'                       -> (0, month, day)
    Anything unparseable                              -> sorts LAST.
    """
    if not folder_name:
        return (1, 13, 32, '')
    name = folder_name.strip().lower()

    # "Month Day" (tolerates trailing words like "Testing")
    m = _re.match(r'^([a-z]+)\.?\s+(\d{1,2})\b', name)
    if m and m.group(1) in _MONTHS:
        return (0, _MONTHS[m.group(1)], int(m.group(2)))

    # Numeric date formats
    for fmt in ('%Y-%m-%d', '%m-%d-%Y', '%m/%d/%Y', '%Y/%m/%d', '%m-%d', '%m/%d'):
        try:
            d = datetime.strptime(folder_name.strip(), fmt)
            return (0, d.month, d.day)
        except ValueError:
            continue

    return (1, 13, 32, name)  # unparseable -> after all real dates

def _build_batch_display_name(folder_names):
    """
    De-duplicated, chronologically-ordered batch name.
    ['September 4', 'September 3'] -> 'September 3 - September 4'
    """
    seen = set()
    unique = []
    for n in folder_names:
        n = (n or '').strip()
        if n and n not in seen:
            seen.add(n)
            unique.append(n)
    unique.sort(key=_parse_date_key)
    return ' - '.join(unique) if unique else 'Bulk Upload'

def _assign_candidates_to_batches(dataset, folder_name, candidates):
    """
    Place candidates into batches of <= BATCH_MAX_SIZE.

    Rules:
      - Group by source date folder, process dates in CALENDAR order.
      - A date with > 10 candidates fills full batches of 10; its remainder
        (the overflow) carries FORWARD and merges into the next date's batch.
      - A date with <= 10 and no inherited overflow stands as its own batch.
      - Overflow only ever moves earlier -> later. A later date never backfills
        an earlier partial batch.
      - Multi-date batches are named "September 3 - September 4" (chronological).
      - Same-date continuation across uploads tops off an existing underfull
        batch whose latest date matches; later dates never top off earlier ones.

    Returns: list of (Batch, [candidate_dicts]) tuples.
    """
    # ── Group by date, then order dates chronologically ──────────────
    groups = OrderedDict()
    for c in candidates:
        key = (c.get('source_folder') or '').strip() or (folder_name or 'Bulk Upload')
        groups.setdefault(key, []).append(c)
    sorted_dates = sorted(groups.keys(), key=_parse_date_key)

    result = []

    # ── Same-date top-off of an existing underfull batch (optional) ──
    # Only continues a batch whose LATEST date equals our EARLIEST date,
    # so we never push later candidates back into an earlier batch.
    if sorted_dates:
        earliest = sorted_dates[0]
        underfull = (
            Batch.objects.filter(DataSet=dataset, Active=True)
            .annotate(cc=Count('applicant'))
            .filter(cc__lt=BATCH_MAX_SIZE)
            .order_by('-CreatedAt')
        )
        for b in underfull:
            existing_dates = _get_batch_folder_names(b)
            if existing_dates and existing_dates[-1] == earliest:
                slots = BATCH_MAX_SIZE - Applicant.objects.filter(round=b).count()
                if slots > 0:
                    fill = groups[earliest][:slots]
                    groups[earliest] = groups[earliest][slots:]
                    if fill:
                        result.append((b, fill))
                    if not groups[earliest]:
                        sorted_dates = sorted_dates[1:]
                break

    # ── Flow-fill, carrying overflow forward in time ─────────────────
    carry = []                 # leftover candidates spilled from an earlier date
    batches_to_create = []     # each entry is a list of candidate dicts

    for date in sorted_dates:
        pool = carry + groups[date]
        carry = []
        full_count = 0
        while len(pool) >= BATCH_MAX_SIZE:
            batches_to_create.append(pool[:BATCH_MAX_SIZE])
            pool = pool[BATCH_MAX_SIZE:]
            full_count += 1
        if pool:
            if full_count > 0:
                carry = pool          # this date overflowed -> spill forward
            else:
                batches_to_create.append(pool)  # no overflow -> own batch
    if carry:
        batches_to_create.append(carry)

    # ── Create batches with collision-safe, chronological names ──────
    used_names = set(
        Batch.objects.filter(DataSet=dataset).values_list('DisplayName', flat=True)
    )
    for chunk in batches_to_create:
        date_names = [
            (c.get('source_folder') or '').strip() or (folder_name or 'Bulk Upload')
            for c in chunk
        ]
        base = _build_batch_display_name(date_names)
        name = base
        n = 2
        while name in used_names:
            name = f"{base} ({n})"
            n += 1
        used_names.add(name)

        new_batch = Batch.objects.create(
            DataSet=dataset,
            DisplayName=name,
            Active=True,
        )
        _auto_assign_batch_to_panel(new_batch)
        result.append((new_batch, chunk))

    return result
    

def _reviewer_progress_breakdown(user):
    """
    Build a committee member's review-progress data, grouped by dataset.
    Cross-dataset aware: a reviewer may hold batches across several datasets.

    Counting rules:
      - "voted" = a Yes/No vote (value in [1, -1]); Waitlist (0) does NOT count.
      - Two denominators are exposed per group:
          * total      — every assigned candidate
          * open_total — candidates in batches whose deadline hasn't passed
                         (a batch with no VoteExpire is always considered open)

    Returns: {
        'global': {... same shape as a dataset row, minus dataset ...},
        'datasets': [ {dataset, total, voted, remaining, open_total,
                       open_remaining, flagged_by_me, next_deadline,
                       pct, open_pct}, ... ],
    }
    """
    now = timezone.now()
    batches = (
        user.assigned_batches
        .select_related('DataSet')
        .order_by('DataSet__DisplayName', 'DisplayName')
    )

    voted_applicant_ids = set(
        Vote.objects.filter(
            voter=user,
            value__in=[1, -1],
            applicant__round__in=batches,
        ).values_list('applicant_id', flat=True)
    )

    flagged_applicant_ids = set(
        Flag.objects.filter(
            user=user,
            applicant__round__in=batches,
        ).values_list('applicant_id', flat=True)
    )

    from collections import OrderedDict
    by_dataset = OrderedDict()
    for b in batches:
        ds = b.DataSet
        by_dataset.setdefault(ds.pk, {'dataset': ds, 'batches': []})['batches'].append(b)

    def blank_row():
        return {
            'total': 0, 'voted': 0, 'remaining': 0,
            'open_total': 0, 'open_remaining': 0,
            'flagged_by_me': 0, 'next_deadline': None,
        }

    rows = []
    glob = blank_row()

    # Track per-batch unvoted counts to pick a resume target.
    # Each entry: {'batch', 'unvoted', 'deadline'}
    batch_unvoted = []

    for entry in by_dataset.values():
        ds = entry['dataset']
        row = blank_row()
        row['dataset'] = ds

        for b in entry['batches']:
            is_open = (b.VoteExpire is None) or (b.VoteExpire >= now)
            if is_open and b.VoteExpire is not None:
                if row['next_deadline'] is None or b.VoteExpire < row['next_deadline']:
                    row['next_deadline'] = b.VoteExpire
                if glob['next_deadline'] is None or b.VoteExpire < glob['next_deadline']:
                    glob['next_deadline'] = b.VoteExpire

            applicant_ids = list(
                Applicant.objects.filter(round=b).values_list('pk', flat=True)
            )
            b_unvoted = 0
            for aid in applicant_ids:
                row['total'] += 1
                glob['total'] += 1
                voted = aid in voted_applicant_ids
                if voted:
                    row['voted'] += 1
                    glob['voted'] += 1
                else:
                    b_unvoted += 1
                if aid in flagged_applicant_ids:
                    row['flagged_by_me'] += 1
                    glob['flagged_by_me'] += 1
                if is_open:
                    row['open_total'] += 1
                    glob['open_total'] += 1
                    if not voted:
                        row['open_remaining'] += 1
                        glob['open_remaining'] += 1

            if b_unvoted > 0:
                batch_unvoted.append({
                    'batch': b,
                    'unvoted': b_unvoted,
                    'deadline': b.VoteExpire,
                })

        row['remaining'] = row['total'] - row['voted']
        row['pct'] = round(row['voted'] / row['total'] * 100) if row['total'] else 0
        row['open_pct'] = (
            round((row['open_total'] - row['open_remaining']) / row['open_total'] * 100)
            if row['open_total'] else 0
        )
        rows.append(row)

    glob['remaining'] = glob['total'] - glob['voted']
    glob['pct'] = round(glob['voted'] / glob['total'] * 100) if glob['total'] else 0
    glob['open_pct'] = (
        round((glob['open_total'] - glob['open_remaining']) / glob['open_total'] * 100)
        if glob['open_total'] else 0
    )

    # ── Resume target: batch with unvoted candidates and soonest deadline. ──
    # Dated deadlines sort before undated (None) ones.
    resume = None
    if batch_unvoted:
        def sort_key(item):
            d = item['deadline']
            return (d is None, d or now)
        batch_unvoted.sort(key=sort_key)
        target = batch_unvoted[0]
        target_batch = target['batch']

        target_total = Applicant.objects.filter(round=target_batch).count()
        target_voted = target_total - target['unvoted']

        resume = {
            'batch': target_batch,
            'dataset': target_batch.DataSet,
            'unvoted': target['unvoted'],
            'deadline': target['deadline'],
            'started': target_voted > 0,   # have they voted on anyone in this batch?
        }

    deadlines = []
    for item in batch_unvoted:
        d = item['deadline']
        if d is None or d < now:
            continue  # undated or already-passed deadlines don't belong in "upcoming"
        days_left = (d.date() - now.date()).days
        deadlines.append({
            'batch': item['batch'],
            'dataset': item['batch'].DataSet,
            'deadline': d,
            'unvoted': item['unvoted'],
            'days_left': days_left,
            # urgency tier drives color in the template
            'urgency': 'overdue' if days_left < 0
                       else 'critical' if days_left <= 3
                       else 'soon' if days_left <= 7
                       else 'normal',
        })
    deadlines.sort(key=lambda x: x['deadline'])

    return {'global': glob, 'datasets': rows, 'resume': resume, 'deadlines': deadlines}
 
def _get_new_batch_alert(user):
    """
    Return a list of batches uploaded since the user's previous login
    that they are assigned to review. Used to surface a one-time alert
    on the committee dashboard.
    Returns [] if previous_login is None (first ever login).
    """
    previous_login = user.profile.previous_login
    if not previous_login:
        return []
 
    # Find BATCH_UPLOADED activity entries since previous login
    recent_uploads = Activity.objects.filter(
        action_type=Activity.BATCH_UPLOADED,
        created_at__gt=previous_login,
        target_batch__isnull=False,
    ).select_related('target_batch', 'target_batch__DataSet')
 
    # Filter to batches the user is actually assigned to
    assigned_batch_ids = set(
        user.assigned_batches.values_list('pk', flat=True)
    )
 
    seen = set()
    new_batches = []
    for entry in recent_uploads:
        batch = entry.target_batch
        if batch.pk in assigned_batch_ids and batch.pk not in seen:
            seen.add(batch.pk)
            new_batches.append(batch)
 
    return new_batches
   
@login_required
@committee_access_required
def committee_dashboard(request):
    user = request.user
    
    progress = _reviewer_progress_breakdown(user)

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
        actor=user,
        action_type__in=[Activity.VOTE_CAST, Activity.COMMENT_ADDED, Activity.FLAG_ADDED]
    ).select_related('target_applicant')[:5]

    # Their total votes and scores
    total_votes = Vote.objects.filter(voter=user).count()
    total_scores = Score.objects.filter(voter=user).count()
    
    new_batch_alert = _get_new_batch_alert(user)

    return render(request, "committee_dashboard.html", {
        'progress': progress,
        'assigned_batches': assigned_batches,
        'pending_count': pending_count,
        'recent_activity': recent_activity,
        'total_votes': total_votes,
        'total_scores': total_scores,
        'new_batch_alert': new_batch_alert,
    })

@login_required
def profile_settings(request):
    from django.contrib.auth.forms import PasswordChangeForm
    from django.contrib.auth import update_session_auth_hash
 
    user = request.user
    profile = user.profile
 
    # Profile form — updates User fields + person_type
    profile_error = None
    password_error = None
    profile_success = False
    password_success = False
 
    if request.method == 'POST':
        form_type = request.POST.get('form_type')
 
        if form_type == 'profile':
            first_name  = request.POST.get('first_name', '').strip()
            last_name   = request.POST.get('last_name', '').strip()
            email       = request.POST.get('email', '').strip()
            person_type = request.POST.get('person_type', '')
 
            user.first_name = first_name
            user.last_name  = last_name
            user.email      = email
            user.save()
 
            valid_types = [c[0] for c in Profile.PersonType.choices]
            if person_type in valid_types:
                profile.person_type = person_type
                profile.save(update_fields=['person_type'])
 
            profile_success = True
 
        elif form_type == 'password':
            password_form = PasswordChangeForm(user, request.POST)
            if password_form.is_valid():
                password_form.save()
                update_session_auth_hash(request, password_form.user)
                password_success = True
            else:
                password_error = password_form.errors
 
    password_form = PasswordChangeForm(user)
 
    return render(request, 'profile_settings.html', {
        'profile':          profile,
        'password_form':    password_form,
        'person_type_choices': Profile.PersonType.choices,
        'profile_success':  profile_success,
        'password_success': password_success,
        'profile_error':    profile_error,
        'password_error':   password_error,
    })
 
@login_required
@admin_required
def analytics_overview(request):
    """
    Cross-dataset analytics overview.
    Shows review progress and DAT/GPA aggregate stats per dataset.
    """
    from django.db.models import Avg, Count, Min, Max, Q
 
    datasets = DataSet.objects.filter(Active=True).order_by('DisplayName')
 
    dataset_rows = []
    for ds in datasets:
        batches      = ds.batches.filter(Active=True)
        batch_count  = batches.count()
        candidates   = Applicant.objects.filter(dataset=ds)
        cand_count   = candidates.count()
 
        reviewer_ids = set()
        for b in batches:
            reviewer_ids.update(b.assigned_reviewers.values_list('pk', flat=True))
        reviewer_count = len(reviewer_ids)
 
        potential_votes = sum(
            b.applicant_set.count() * b.assigned_reviewers.count()
            for b in batches
        )
        actual_votes = Vote.objects.filter(
            applicant__dataset=ds
        ).count()
        progress_pct = round(actual_votes / potential_votes * 100) if potential_votes > 0 else 0
 
        # DAT/GPA aggregates
        imported = candidates.filter(candidate_info_imported=True)
        imported_count = imported.count()
        gpa_stats = imported.aggregate(
            avg_overall=Avg('gpa_overall'),
            avg_science=Avg('gpa_science'),
            avg_bcp=Avg('gpa_bcp'),
        )
        dat_stats = imported.aggregate(
            avg_aa=Avg('dat_academic_avg'),
        )
 
        dataset_rows.append({
            'dataset':         ds,
            'batch_count':     batch_count,
            'cand_count':      cand_count,
            'reviewer_count':  reviewer_count,
            'actual_votes':    actual_votes,
            'potential_votes': potential_votes,
            'progress_pct':    progress_pct,
            'imported_count':  imported_count,
            'gpa_overall_avg': round(gpa_stats['avg_overall'], 2) if gpa_stats['avg_overall'] else None,
            'gpa_science_avg': round(gpa_stats['avg_science'], 2) if gpa_stats['avg_science'] else None,
            'gpa_bcp_avg':     round(gpa_stats['avg_bcp'], 2)     if gpa_stats['avg_bcp']     else None,
            'dat_aa_avg':      round(dat_stats['avg_aa'], 1)       if dat_stats['avg_aa']       else None,
        })
 
    # Global totals
    total_candidates   = sum(r['cand_count']      for r in dataset_rows)
    total_actual       = sum(r['actual_votes']     for r in dataset_rows)
    total_potential    = sum(r['potential_votes']  for r in dataset_rows)
    global_progress    = round(total_actual / total_potential * 100) if total_potential > 0 else 0
 
    return render(request, 'analytics_overview.html', {
        'dataset_rows':     dataset_rows,
        'total_candidates': total_candidates,
        'total_actual':     total_actual,
        'total_potential':  total_potential,
        'global_progress':  global_progress,
    })
 
 
@login_required
@admin_required
def analytics_dataset(request, pk):
    """
    Per-dataset analytics: review progress breakdown + DAT/GPA distributions.
    """
    from django.db.models import Avg, Count, Min, Max, Q
    import json as _json

    dataset    = get_object_or_404(DataSet, pk=pk)
    batches    = dataset.batches.filter(Active=True).order_by('DisplayName')
    candidates = Applicant.objects.filter(dataset=dataset)
    imported   = candidates.filter(candidate_info_imported=True)

    # ── Review progress per batch ────────────────────────────────────────────
    batch_progress = []
    for b in batches:
        assigned  = b.assigned_reviewers.all()
        r_count   = assigned.count()
        c_count   = Applicant.objects.filter(round=b).count()
        potential = c_count * r_count
        actual    = Vote.objects.filter(
            applicant__round=b,
            voter__in=assigned,
        ).count()
        pct = round(actual / potential * 100) if potential > 0 else 0

        reviewer_rows = []
        for reviewer in assigned.select_related('profile'):
            rv = Vote.objects.filter(
                applicant__round=b,
                voter=reviewer,
            ).count()
            reviewer_rows.append({
                'user':       reviewer,
                'votes_cast': rv,
                'total':      c_count,
                'pct':        round(rv / c_count * 100) if c_count > 0 else 0,
            })
        reviewer_rows.sort(key=lambda x: -x['pct'])

        batch_progress.append({
            'batch':         b,
            'c_count':       c_count,
            'r_count':       r_count,
            'actual':        actual,
            'potential':     potential,
            'pct':           pct,
            'reviewer_rows': reviewer_rows,
        })

    # ── DAT/GPA aggregate stats ──────────────────────────────────────────────
    imported_count = imported.count()
    total_count    = candidates.count()

    def safe_round(val, dp=2):
        return round(val, dp) if val is not None else None

    gpa_agg = imported.aggregate(
        avg=Avg('gpa_overall'),   mn=Min('gpa_overall'),   mx=Max('gpa_overall'),
        avg_sci=Avg('gpa_science'), mn_sci=Min('gpa_science'), mx_sci=Max('gpa_science'),
        avg_bcp=Avg('gpa_bcp'),   mn_bcp=Min('gpa_bcp'),   mx_bcp=Max('gpa_bcp'),
    )
    dat_agg = imported.aggregate(
        avg_aa=Avg('dat_academic_avg'),            mn_aa=Min('dat_academic_avg'),            mx_aa=Max('dat_academic_avg'),
        avg_pat=Avg('dat_perceptual_ability'),      mn_pat=Min('dat_perceptual_ability'),      mx_pat=Max('dat_perceptual_ability'),
        avg_qr=Avg('dat_quantitative_reasoning'),   mn_qr=Min('dat_quantitative_reasoning'),   mx_qr=Max('dat_quantitative_reasoning'),
        avg_rc=Avg('dat_reading_comp'),             mn_rc=Min('dat_reading_comp'),             mx_rc=Max('dat_reading_comp'),
        avg_bio=Avg('dat_biology'),                 mn_bio=Min('dat_biology'),                 mx_bio=Max('dat_biology'),
        avg_gc=Avg('dat_general_chem'),             mn_gc=Min('dat_general_chem'),             mx_gc=Max('dat_general_chem'),
        avg_oc=Avg('dat_organic_chem'),             mn_oc=Min('dat_organic_chem'),             mx_oc=Max('dat_organic_chem'),
        avg_ts=Avg('dat_total_science'),            mn_ts=Min('dat_total_science'),            mx_ts=Max('dat_total_science'),
    )

    # ── GPA distribution bands ───────────────────────────────────────────────
    # ── Candidate label/link helper (for chart drill-down) ───────────────────
    def _cand(a, value=None):
        # NOTE: derives a display name defensively. If your Applicant model has a
        # single name field, simplify this to e.g. `name = a.full_name`.
        getfn = getattr(a, 'get_full_name', None)
        name = getfn() if callable(getfn) else ''
        if not name:
            name = ' '.join(
                str(getattr(a, f, '') or '').strip()
                for f in ('first_name', 'last_name')
            ).strip()
        if not name:
            name = str(a)
        return {
            'name': name,
            'url': reverse('applicant_detail', args=[a.pk]),
            'score': float(value) if value is not None else None,
        }

    # ── GPA distribution bands (with member lists for drill-down) ────────────
    def gpa_buckets(field):
        members = [[], [], [], [], []]
        for a in imported:
            v = getattr(a, field, None)
            if v is None:
                continue
            if   v < 3.0:  members[0].append(_cand(a, v))
            elif v < 3.25: members[1].append(_cand(a, v))
            elif v < 3.5:  members[2].append(_cand(a, v))
            elif v < 3.75: members[3].append(_cand(a, v))
            else:          members[4].append(_cand(a, v))
        return members

    gpa_band_labels     = ['< 3.00', '3.00–3.24', '3.25–3.49', '3.50–3.74', '≥ 3.75']
    gpa_overall_members = gpa_buckets('gpa_overall')
    gpa_science_members = gpa_buckets('gpa_science')
    gpa_bcp_members     = gpa_buckets('gpa_bcp')
    gpa_overall_bands   = [len(b) for b in gpa_overall_members]
    gpa_science_bands   = [len(b) for b in gpa_science_members]
    gpa_bcp_bands       = [len(b) for b in gpa_bcp_members]

    # ── DAT Academic Average distribution bands (with member lists) ──────────
    dat_band_labels = ['200–239', '240–279', '280–319', '320–359', '360–399', '400–439', '440–479', '480–519', '520–559', '560–600']
    dat_aa_members = [[] for _ in range(10)]
    for a in imported:
        v = getattr(a, 'dat_academic_avg', None)
        if v is None or v < 200 or v >= 601:
            continue
        idx = min(9, int((v - 200) // 40))   # 40-wide bands starting at 200
        dat_aa_members[idx].append(_cand(a, v))
    dat_aa_bands = [len(b) for b in dat_aa_members]

    # ── Summary table rows for template ─────────────────────────────────────
    gpa_fields = [
        ('Overall GPA', safe_round(gpa_agg['avg']),     safe_round(gpa_agg['mn']),     safe_round(gpa_agg['mx']),     'gpa_overall'),
        ('Science GPA', safe_round(gpa_agg['avg_sci']), safe_round(gpa_agg['mn_sci']), safe_round(gpa_agg['mx_sci']), 'gpa_science'),
        ('BCP GPA',     safe_round(gpa_agg['avg_bcp']), safe_round(gpa_agg['mn_bcp']), safe_round(gpa_agg['mx_bcp']), 'gpa_bcp'),
    ]

    # dat_fields: (label, avg, min, max, good_threshold)
    # good_threshold — at or above this score we colour green
    dat_fields = [
        ('Academic Average',       safe_round(dat_agg['avg_aa'],  1), dat_agg['mn_aa'],  dat_agg['mx_aa'],  400),
        ('Perceptual Ability',     safe_round(dat_agg['avg_pat'], 1), dat_agg['mn_pat'], dat_agg['mx_pat'], 380),
        ('Quantitative Reasoning', safe_round(dat_agg['avg_qr'],  1), dat_agg['mn_qr'],  dat_agg['mx_qr'],  380),
        ('Reading Comprehension',  safe_round(dat_agg['avg_rc'],  1), dat_agg['mn_rc'],  dat_agg['mx_rc'],  380),
        ('Biology',                safe_round(dat_agg['avg_bio'], 1), dat_agg['mn_bio'], dat_agg['mx_bio'], 380),
        ('General Chemistry',      safe_round(dat_agg['avg_gc'],  1), dat_agg['mn_gc'],  dat_agg['mx_gc'],  380),
        ('Organic Chemistry',      safe_round(dat_agg['avg_oc'],  1), dat_agg['mn_oc'],  dat_agg['mx_oc'],  380),
        ('Total Science',          safe_round(dat_agg['avg_ts'],  1), dat_agg['mn_ts'],  dat_agg['mx_ts'],  380),
    ]

    return render(request, 'analytics_dataset.html', {
        'dataset':        dataset,
        'batch_progress': batch_progress,
        'imported_count': imported_count,
        'total_count':    total_count,
        # GPA summary
        'gpa_fields':     gpa_fields,
        # DAT summary
        'dat_fields':     dat_fields,
        # Individual agg values (still used for the stat cards in template)
        'gpa_overall_avg': safe_round(gpa_agg['avg']),
        'gpa_overall_min': safe_round(gpa_agg['mn']),
        'gpa_overall_max': safe_round(gpa_agg['mx']),
        'gpa_science_avg': safe_round(gpa_agg['avg_sci']),
        'gpa_science_min': safe_round(gpa_agg['mn_sci']),
        'gpa_science_max': safe_round(gpa_agg['mx_sci']),
        'gpa_bcp_avg':     safe_round(gpa_agg['avg_bcp']),
        'gpa_bcp_min':     safe_round(gpa_agg['mn_bcp']),
        'gpa_bcp_max':     safe_round(gpa_agg['mx_bcp']),
        'dat_aa_avg':      safe_round(dat_agg['avg_aa'],  1),
        'dat_aa_min':      dat_agg['mn_aa'],
        'dat_aa_max':      dat_agg['mx_aa'],
        'dat_pat_avg':     safe_round(dat_agg['avg_pat'], 1),
        'dat_pat_min':     dat_agg['mn_pat'],
        'dat_pat_max':     dat_agg['mx_pat'],
        'dat_qr_avg':      safe_round(dat_agg['avg_qr'],  1),
        'dat_qr_min':      dat_agg['mn_qr'],
        'dat_qr_max':      dat_agg['mx_qr'],
        'dat_rc_avg':      safe_round(dat_agg['avg_rc'],  1),
        'dat_rc_min':      dat_agg['mn_rc'],
        'dat_rc_max':      dat_agg['mx_rc'],
        'dat_bio_avg':     safe_round(dat_agg['avg_bio'], 1),
        'dat_bio_min':     dat_agg['mn_bio'],
        'dat_bio_max':     dat_agg['mx_bio'],
        'dat_gc_avg':      safe_round(dat_agg['avg_gc'],  1),
        'dat_gc_min':      dat_agg['mn_gc'],
        'dat_gc_max':      dat_agg['mx_gc'],
        'dat_oc_avg':      safe_round(dat_agg['avg_oc'],  1),
        'dat_oc_min':      dat_agg['mn_oc'],
        'dat_oc_max':      dat_agg['mx_oc'],
        'dat_ts_avg':      safe_round(dat_agg['avg_ts'],  1),
        'dat_ts_min':      dat_agg['mn_ts'],
        'dat_ts_max':      dat_agg['mx_ts'],
        # Chart data (JSON for Chart.js)
        'gpa_band_labels':   _json.dumps(gpa_band_labels),
        'gpa_overall_bands': _json.dumps(gpa_overall_bands),
        'gpa_science_bands': _json.dumps(gpa_science_bands),
        'gpa_bcp_bands':     _json.dumps(gpa_bcp_bands),
        'dat_band_labels':   _json.dumps(dat_band_labels),
        'dat_aa_bands':      _json.dumps(dat_aa_bands),
        'gpa_overall_members': _json.dumps(gpa_overall_members),
        'gpa_science_members': _json.dumps(gpa_science_members),
        'gpa_bcp_members':     _json.dumps(gpa_bcp_members),
        'dat_aa_members':      _json.dumps(dat_aa_members),
        'crumbs': [
            {'label': 'Analytics',         'url': '/analytics/'},
            {'label': dataset.DisplayName, 'url': ''},
        ],
    })
    
@login_required
@committee_access_required
def my_reviews(request):
    user = request.user
    sort = request.GET.get('sort', 'name')
    direction = request.GET.get('dir', 'asc')
 
    filter_batch     = request.GET.get('batch', '')
    filter_vote      = request.GET.get('vote', '')   # '1', '-1', '0', 'none'
    filter_flagged   = request.GET.get('flagged_only', '')
    filter_score_min = request.GET.get('score_min', '')
    filter_score_max = request.GET.get('score_max', '')
    has_filters      = any([filter_batch, filter_vote, filter_flagged, filter_score_min, filter_score_max])
 
    # CSV export branch — triggered by ?export=csv (admin only)
    if request.GET.get('export') == 'csv':
        if user.profile.role != 'ADMIN':
            return HttpResponse('Exports are not available for committee members.', status=403)
        return _export_my_reviews_csv(request, user)
 
    assigned_batches = Batch.objects.filter(assigned_reviewers=user)
 
    votes = Vote.objects.filter(
        voter=user,
        applicant__round__in=assigned_batches,
    ).select_related('applicant', 'applicant__round', 'applicant__dataset')
 
    scores = Score.objects.filter(
        voter=user,
        applicant__round__in=assigned_batches,
    ).select_related('applicant', 'applicant__round')
 
    # Membership driven by votes only (prevents blank-Score leak)
    applicant_pks = set(votes.values_list('applicant__pk', flat=True))
    applicants = (
        Applicant.objects.filter(pk__in=applicant_pks)
        .select_related('round', 'dataset')
        .prefetch_related('flagged_by')
    )
 
    if filter_batch:
        applicants = applicants.filter(round_id=filter_batch)
    if filter_flagged:
        applicants = applicants.filter(flagged_by=user)
 
    vote_lookup  = {v.applicant_id: v for v in votes}
    score_lookup = {s.applicant_id: s for s in scores}
 
    reviews = []
    for applicant in applicants:
        vote  = vote_lookup.get(applicant.pk)
        score = score_lookup.get(applicant.pk)
 
        # Vote value filter
        if filter_vote == 'none' and vote is not None:
            continue
        if filter_vote == '1'  and (vote is None or vote.value != 1):
            continue
        if filter_vote == '-1' and (vote is None or vote.value != -1):
            continue
        if filter_vote == '0'  and (vote is None or vote.value != 0):
            continue
 
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
        return (a.last_name.lower(), a.first_name.lower())
 
    reviews.sort(key=sort_key, reverse=(direction == 'desc'))
 
    if filter_score_min:
        reviews = [r for r in reviews if r['score'] and r['score'].overall_score and r['score'].overall_score >= int(filter_score_min)]
    if filter_score_max:
        reviews = [r for r in reviews if r['score'] and r['score'].overall_score and r['score'].overall_score <= int(filter_score_max)]
 
 
    # Stats strip
    yes_count      = sum(1 for r in reviews if r['vote'] and r['vote'].value == 1)
    no_count       = sum(1 for r in reviews if r['vote'] and r['vote'].value == -1)
    waitlist_count = sum(1 for r in reviews if r['vote'] and r['vote'].value == 0)
    flagged_count  = sum(1 for r in reviews if r['is_flagged'])
    scored         = [r for r in reviews if r['score'] and r['score'].overall_score]
    avg_overall    = (
        round(sum(r['score'].overall_score for r in scored) / len(scored), 1)
        if scored else None
    )

    return render(request, "my_reviews.html", {
        'reviews':          reviews,
        'total_votes':      votes.count(),
        'total_scores':     scores.count(),
        'sort':             sort,
        'direction':        direction,
        'filter_batch':     filter_batch,
        'filter_vote':      filter_vote,
        'filter_flagged':   filter_flagged,
        'has_filters':      has_filters,
        'assigned_batches': assigned_batches,
        'yes_count':        yes_count,
        'no_count':         no_count,
        'waitlist_count':   waitlist_count,
        'flagged_count':    flagged_count,
        'avg_overall':      avg_overall,
        'reviewed_count':   len(reviews),
        'filter_score_min': filter_score_min,
        'filter_score_max': filter_score_max,
    })
 
 
def _export_my_reviews_csv(request, user):
    """Reviewer's own votes + scores exported as CSV. Called from my_reviews()."""
    filter_batch = request.GET.get('batch', '')
    assigned_batches = Batch.objects.filter(assigned_reviewers=user)
    if filter_batch:
        assigned_batches = assigned_batches.filter(pk=filter_batch)
 
    vote_lookup  = {v.applicant_id: v for v in Vote.objects.filter(voter=user, applicant__round__in=assigned_batches)}
    score_lookup = {s.applicant_id: s for s in Score.objects.filter(voter=user, applicant__round__in=assigned_batches)}
 
    applicants = (
        Applicant.objects.filter(pk__in=vote_lookup.keys())
        .select_related('round', 'dataset')
        .prefetch_related('flagged_by')
        .order_by('last_name', 'first_name')
    )
 
    response = HttpResponse(
        content_type='text/csv',
        headers={'Content-Disposition': 'attachment; filename="my_reviews.csv"'},
    )
    writer = csv.writer(response)
    writer.writerow([
        'Last Name', 'First Name', 'External ID', 'Dataset', 'Batch',
        'Vote', 'Research Score', 'Statement Score', 'Overall Score', 'Flagged by Me',
    ])
    vote_labels = {1: 'Yes', -1: 'No', 0: 'Waitlist'}
    for a in applicants:
        v = vote_lookup.get(a.pk)
        s = score_lookup.get(a.pk)
        writer.writerow([
            a.last_name,
            a.first_name,
            a.external_id or '',
            a.dataset.DisplayName if a.dataset else '',
            a.round.DisplayName if a.round else '',
            vote_labels.get(v.value, '') if v else '',
            s.research_score  if s else '',
            s.statement_score if s else '',
            s.overall_score   if s else '',
            'Yes' if user in a.flagged_by.all() else 'No',
        ])
    return response
 


@login_required
@committee_access_required
def my_activity(request):
    user = request.user
 
    # ── CSV export ─────────────────────────────────────────── (admin only)
    if request.GET.get('export') == 'csv':
        if user.profile.role != 'ADMIN':
            return HttpResponse('Exports are not available for committee members.', status=403)
        return _export_my_activity_csv(request, user)
 
    # ── Filters ──────────────────────────────────────────────────────
    filter_action    = request.GET.get('action', '')
    filter_batch     = request.GET.get('batch', '')
    filter_date_from = request.GET.get('date_from', '')
    filter_date_to   = request.GET.get('date_to', '')
    has_filters = any([filter_action, filter_batch, filter_date_from, filter_date_to])
 
    activities = Activity.objects.filter(
        actor=user,
        action_type__in=[Activity.VOTE_CAST, Activity.COMMENT_ADDED, Activity.FLAG_ADDED]
    ).select_related('target_applicant', 'target_applicant__round').order_by('-created_at')
 
    if filter_action:
        activities = activities.filter(action_type=filter_action)
    if filter_batch:
        activities = activities.filter(target_applicant__round_id=filter_batch)
    if filter_date_from:
        activities = activities.filter(created_at__date__gte=filter_date_from)
    if filter_date_to:
        activities = activities.filter(created_at__date__lte=filter_date_to)
 
    paginator = Paginator(activities, 25)
    page_obj  = paginator.get_page(request.GET.get('page'))
 
    assigned_batches = Batch.objects.filter(assigned_reviewers=user).order_by('DisplayName')
 
    return render(request, "my_activity.html", {
        'page_obj':         page_obj,
        'filter_action':    filter_action,
        'filter_batch':     filter_batch,
        'filter_date_from': filter_date_from,
        'filter_date_to':   filter_date_to,
        'has_filters':      has_filters,
        'assigned_batches': assigned_batches,
        'action_choices': [
            (Activity.VOTE_CAST,     'Vote Cast'),
            (Activity.COMMENT_ADDED, 'Comment Added'),
            (Activity.FLAG_ADDED,    'Flag Added'),
        ],
        'total_count': paginator.count,
    })
 
 
def _export_my_activity_csv(request, user):
    filter_action    = request.GET.get('action', '')
    filter_batch     = request.GET.get('batch', '')
    filter_date_from = request.GET.get('date_from', '')
    filter_date_to   = request.GET.get('date_to', '')
 
    activities = Activity.objects.filter(
        actor=user,
        action_type__in=[Activity.VOTE_CAST, Activity.COMMENT_ADDED, Activity.FLAG_ADDED]
    ).select_related('target_applicant', 'target_applicant__round').order_by('-created_at')
 
    if filter_action:
        activities = activities.filter(action_type=filter_action)
    if filter_batch:
        activities = activities.filter(target_applicant__round_id=filter_batch)
    if filter_date_from:
        activities = activities.filter(created_at__date__gte=filter_date_from)
    if filter_date_to:
        activities = activities.filter(created_at__date__lte=filter_date_to)
 
    response = HttpResponse(
        content_type='text/csv',
        headers={'Content-Disposition': 'attachment; filename="my_activity.csv"'},
    )
    writer = csv.writer(response)
    writer.writerow(['Date', 'Action', 'Candidate', 'Batch', 'Details'])
    for a in activities:
        writer.writerow([
            a.created_at.strftime('%Y-%m-%d %H:%M'),
            a.get_action_type_display(),
            str(a.target_applicant) if a.target_applicant else '',
            a.target_applicant.round.DisplayName if a.target_applicant and a.target_applicant.round else '',
            a.details,
        ])
    return response
    
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
        elif action == 'set_status_accepted':
            count = Applicant.objects.filter(round_id__in=batch_ids).update(status=Applicant.Status.ACCEPTED)
            messages.success(request, f"Updated {count} applicants to 'Accepted'.")
        elif action == 'set_status_waitlisted':
            count = Applicant.objects.filter(round_id__in=batch_ids).update(status=Applicant.Status.WAITLISTED)
            messages.success(request, f"Updated {count} applicants to 'Waitlisted'.")
        elif action == 'set_status_rejected':
            count = Applicant.objects.filter(round_id__in=batch_ids).update(status=Applicant.Status.REJECTED)
            messages.success(request, f"Updated {count} applicants to 'Rejected'.")
        else:
            messages.error(request, "No valid action selected.")
            
    return redirect('batch_list')

@login_required
@admin_required
def manage_panels(request):
    """
    Admin page to create, edit, and assign members to review panels.
    Replaces the old reviewer groups page.
    """
    from .models import ReviewPanel
    from .forms import ReviewPanelForm
 
    panels = ReviewPanel.objects.prefetch_related('members', 'batches').order_by('name')
    all_members = User.objects.filter(
        Q(profile__role='COMMITTEE_MEMBER') |
        Q(profile__role='ADMIN', profile__is_reviewer=True)
    ).select_related('profile').order_by('last_name', 'first_name', 'username')
    
    admin_users = User.objects.filter(
        profile__role='ADMIN'
    ).select_related('profile').order_by('last_name', 'first_name', 'username')
 
    # ── Handle panel creation ────────────────────────────────────────
    if request.method == 'POST':
        action = request.POST.get('action')
 
        if action == 'create_panel':
            form = ReviewPanelForm(request.POST)
            if form.is_valid():
                panel = form.save()
                messages.success(request, f'Panel "{panel.name}" created.')
            else:
                for field, errors in form.errors.items():
                    for error in errors:
                        messages.error(request, f'{error}')
            return redirect('manage_panels')
 
        elif action == 'update_members':
            panel_id = request.POST.get('panel_id')
            member_ids = request.POST.getlist('member_ids')
            try:
                panel = ReviewPanel.objects.get(pk=panel_id)
                panel.members.set(member_ids)
                # Sync assigned_reviewers on all this panel's batches
                for batch in panel.batches.all():
                    batch.assigned_reviewers.set(panel.members.all())
                messages.success(request, f'Members updated for "{panel.name}".')
            except ReviewPanel.DoesNotExist:
                messages.error(request, 'Panel not found.')
            return redirect('manage_panels')
 
        elif action == 'rename_panel':
            panel_id = request.POST.get('panel_id')
            new_name = request.POST.get('name', '').strip()
            new_desc = request.POST.get('description', '').strip()
            try:
                panel = ReviewPanel.objects.get(pk=panel_id)
                if new_name:
                    panel.name = new_name
                panel.description = new_desc
                panel.save()
                messages.success(request, f'Panel updated.')
            except ReviewPanel.DoesNotExist:
                messages.error(request, 'Panel not found.')
            return redirect('manage_panels')
 
        elif action == 'delete_panel':
            panel_id = request.POST.get('panel_id')
            try:
                panel = ReviewPanel.objects.get(pk=panel_id)
                # Unassign reviewers from all batches in this panel
                for batch in panel.batches.all():
                    batch.assigned_reviewers.clear()
                    batch.panel = None
                    batch.save()
                name = panel.name
                panel.delete()
                messages.success(request, f'Panel "{name}" deleted.')
            except ReviewPanel.DoesNotExist:
                messages.error(request, 'Panel not found.')
            return redirect('manage_panels')
 
        elif action == 'assign_batch':
            panel_id  = request.POST.get('panel_id')
            batch_ids = request.POST.getlist('batch_ids')
            try:
                panel = ReviewPanel.objects.get(pk=panel_id)
                # Remove this panel from batches it currently owns but aren't in new list
                for batch in panel.batches.exclude(pk__in=batch_ids):
                    batch.panel = None
                    batch.assigned_reviewers.clear()
                    batch.save()
                # Assign selected batches to this panel
                for batch in Batch.objects.filter(pk__in=batch_ids):
                    batch.panel = panel
                    batch.save()
                    batch.assigned_reviewers.set(panel.members.all())
                messages.success(request, f'Batch assignments updated for "{panel.name}".')
            except ReviewPanel.DoesNotExist:
                messages.error(request, 'Panel not found.')
            return redirect('manage_panels')
 
    create_form = ReviewPanelForm()
 
    # Annotate panels with batch info grouped by dataset for display
    all_batches = Batch.objects.select_related('DataSet').order_by(
        'DataSet__DisplayName', 'DisplayName'
    )
 
    # Build unassigned members set for the "unassigned" column
    assigned_member_ids = set()
    for panel in panels:
        for m in panel.members.all():
            assigned_member_ids.add(m.pk)
    unassigned_members = [m for m in all_members if m.pk not in assigned_member_ids]
 
    return render(request, 'manage_panels.html', {
        'person_type_choices': Profile.PersonType.choices,
        'panels':             panels,
        'all_members':        all_members,
        'unassigned_members': unassigned_members,
        'all_batches':        all_batches,
        'create_form':        create_form,
        'admin_users': admin_users,
    })


def _auto_assign_batch_to_panel(batch):
    """
    Assign a newly created batch to the ReviewPanel with the fewest active batches.
    Falls back gracefully if no panels exist yet.
    """
    from .models import ReviewPanel
    panels = list(ReviewPanel.objects.annotate(
        active_batch_count=Count('batches', filter=Q(batches__Active=True))
    ).order_by('active_batch_count', 'name'))
 
    if not panels:
        # No panels exist yet — leave unassigned
        return None
 
    selected_panel = panels[0]
    batch.panel = selected_panel
    batch.save()
    batch.assigned_reviewers.set(selected_panel.members.all())
    return selected_panel

@login_required
@admin_required
def send_notification(request):
    if request.method == 'POST':
        form = SendNotificationForm(request.POST, request.FILES)
        if form.is_valid():
            recipient_type = form.cleaned_data['recipient_type']
            subject = form.cleaned_data['subject']
            message = form.cleaned_data['message']
 
            recipients = User.objects.none()
 
            if recipient_type == 'dataset':
                dataset = form.cleaned_data.get('dataset')
                if dataset:
                    batch_ids = Batch.objects.filter(DataSet=dataset).values_list('pk', flat=True)
                    reviewer_ids = Batch.objects.filter(pk__in=batch_ids).values_list('assigned_reviewers', flat=True)
                    recipients = User.objects.filter(pk__in=reviewer_ids).distinct()

            elif recipient_type == 'panel':
                panel = form.cleaned_data.get('panel')
                if panel:
                    recipients = panel.members.all()

            elif recipient_type == 'batch':
                batch = form.cleaned_data.get('batch')
                if batch:
                    recipients = batch.assigned_reviewers.all()

            elif recipient_type == 'individual':
                recipients = form.cleaned_data.get('individual_reviewers', User.objects.none())
 
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
