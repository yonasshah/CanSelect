# CanSelect — Kornberg Admissions Portal

A Django-based admissions review platform built for Temple University's Kornberg School of Dentistry. CanSelect streamlines the graduate applicant evaluation process by providing role-based workflows for administrators and committee members to manage, score, and vote on candidates.

---

## Overview

CanSelect organizes applicants into **DataSets** (programs) and **Batches** (review rounds), then routes them through a structured evaluation pipeline. Committee members score applicants on research, personal statements, and overall fit, while administrators oversee the full lifecycle from intake to final decision.

### Key Features

- **Role-Based Access Control** — Two roles (Admin, Committee Member) with distinct permissions. Admins manage all data and see all comments; committee members only see their own.
- **Applicant Review Queue** — Committee members get a personalized queue showing only applicants in their assigned batches that they haven't yet reviewed.
- **Scoring Rubric** — Three-dimensional scoring (Research, Statement, Overall) on a 1–5 scale, with averaged scores visible to admins.
- **Voting System** — Accept / Deny / Waitlist votes per applicant, with vote summaries displayed throughout the UI.
- **Flagging for Discussion** — Any reviewer can flag an applicant for committee discussion; flags are visible to all reviewers and admins.
- **Side-by-Side Comparison** — Select multiple applicants from the list view and compare demographics, votes, and comments in a multi-column layout.
- **File & Video Management** — Upload resumes, transcripts, video interviews, and other documents per applicant. Videos include playback speed controls.
- **Bulk Upload** — Upload an entire folder of candidate materials; the system parses PDFs for names and emails and creates applicant records automatically.
- **Activity Feed** — Tracks votes and comments in a chronological feed visible to admins.
- **Dashboard Analytics** — Admin dashboard with Chart.js visualizations for vote distribution and gender demographics, filterable by DataSet.
- **CSV Export** — Export filtered applicant lists to CSV.
- **Batch Reviewer Assignment** — Admins assign committee members to specific batches, controlling who reviews which candidates.
- **Status Workflow** — Applicants progress through New → Under Review → Interview → Decision Made.

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | Django 5.2.10, Python 3.10+ |
| Database | SQLite (default, swappable) |
| Frontend | Django Templates, Bootstrap 5.3, Bootstrap Icons |
| Charts | Chart.js |
| Template Helpers | django-widget-tweaks 1.5.1 |
| PDF Parsing | pypdf 6.6.2 (for bulk upload) |
| Image Handling | Pillow 12.1.0 |

---

## Project Structure

```
gradapp/
├── gradapp/                  # Project configuration
│   ├── settings.py
│   ├── urls.py
│   ├── wsgi.py
│   └── asgi.py
├── applicants/               # Main application
│   ├── models.py             # Applicant, DataSet, Batch, Vote, Score, Comment, Activity, Profile
│   ├── views.py              # All view functions
│   ├── forms.py              # ModelForms and custom forms
│   ├── urls.py               # URL routing
│   ├── admin.py              # Django admin configuration
│   ├── signals.py            # Auto-create Activity records on vote/comment
│   ├── decorators.py         # @admin_required decorator
│   ├── apps.py               # AppConfig with signal registration
│   └── templates/
│       ├── base.html                         # Master layout with navbar and modals
│       ├── login.html
│       ├── dashboard.html
│       ├── applicant_list.html
│       ├── applicant_detail.html
│       ├── applicant_create.html
│       ├── applicant_edit.html
│       ├── compare_applicants.html
│       ├── dataset_list.html / _detail / _create / _edit
│       ├── batch_list.html / _detail / _create / _edit
│       ├── batch_assign_reviewers.html
│       ├── bulk_upload.html
│       ├── activity_feed.html
│       └── partials/
│           └── applicant_profile_partial.html  # AJAX-loaded modal content
├── templates/
│   ├── base.html
│   └── registration/
│       └── login.html
├── static/
│   └── temple_logo.png
└── media/                    # Uploaded files (gitignored)
```

---

## Data Model

**DataSet** — Represents a program or admissions cycle. Contains metadata like display name, admin notes, public visibility, and active status.

**Batch** — A review round within a DataSet. Batches have vote expiration dates, assigned reviewers, and can be marked active/inactive.

**Applicant** — The core record. Linked to a DataSet and optionally to a Batch (round). Tracks demographics, status, uploaded files, profile picture, and flags.

**Vote** — One per applicant per user. Values: Accept (1), Deny (-1), Waitlist (0).

**Score** — One per applicant per user. Three criteria scored 1–5: Research, Statement, Overall.

**Comment** — Free-text notes attached to applicants. Committee members see only their own; admins see all.

**Activity** — Automatically logged via Django signals when votes are cast or comments are posted.

**Profile** — Extends Django's User model with a role field (Admin or Committee Member). Auto-created via signal on user creation.

---

## Getting Started

### Prerequisites

- Python 3.10+
- pip

### Installation

```bash
# Clone the repository
git clone <repository-url>
cd gradapp

# Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS/Linux

# Install dependencies
pip install -r requirements.txt

Then continue setup:

```bash
# Run migrations
python manage.py migrate

# Create a superuser
python manage.py createsuperuser

# Start the development server
python manage.py runserver
```

### Initial Setup

1. Log in to `/admin/` with your superuser account.
2. Set your Profile role to **ADMIN** in the admin panel.
3. Create a **DataSet** (e.g., "Fall 2026 Admissions").
4. Create one or more **Batches** under that DataSet using bulk upload.
6. Create committee member accounts and assign them to batches.

---

## Authentication

The app supports two login methods:

- **Standard Django auth** — Username/password login at `/login/`.
- **Email login** — A simplified flow at the app's `/login/` route where entering an email auto-creates a user account (intended for development/demo use).

---

## User Roles

| Capability | Admin | Committee Member |
|------------|-------|------------------|
| View applicant list | Yes | Yes |
| View applicant detail | Yes | Yes |
| Vote on applicants | Yes | Yes |
| Score applicants | Yes | Yes |
| Post comments | Yes | Yes |
| See all comments | Yes | Own only |
| Create/edit applicants | Yes | No |
| Manage DataSets & Batches | Yes | No |
| Update applicant status | Yes | No |
| Assign reviewers to batches | Yes | No |
| View activity feed | Yes | No |
| Bulk upload | Yes | No |
| Batch status actions | Yes | No |
| View dashboard analytics | Yes | Limited |

---

## Configuration

Key settings in `gradapp/settings.py`:

- `DEBUG = True` — Set to `False` in production.
- `SECRET_KEY` — Replace the dev key for production.
- `DATABASES` — SQLite by default; .
- `MEDIA_ROOT` / `MEDIA_URL` — Controls where uploaded files are stored and served.
- `LOGIN_REDIRECT_URL` — Defaults to the applicant list after login.

---

## License

This project is proprietary software developed for Temple University Kornberg School of Dentistry.
