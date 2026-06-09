"""
Management command: generate_test_candidates

Generates realistic fake candidate folders that can be uploaded via Bulk Upload.
Supports multiple datasets, each with multiple batches of configurable sizes.

Usage:
    # Default: 2 datasets, 3 batches each, 10 candidates per batch
    python manage.py generate_test_candidates

    # Custom output directory
    python manage.py generate_test_candidates --out-dir C:/Users/you/Desktop/sandbox

    # Specific structure
    python manage.py generate_test_candidates --out-dir ./sandbox --config "4yr DMD:Dec 1:12,Dec 2:8|PostBacc:Round 1:10"

    # Also zip everything
    python manage.py generate_test_candidates --out-dir ./sandbox --zip

Config format:  "DatasetName:BatchName:Count,BatchName:Count|DatasetName:BatchName:Count"
  |  separates datasets
  ,  separates batches within a dataset

Output structure:
    sandbox/
      4yr DMD/
        Dec 1/
          Doe, John - 1234567890/
            1234567890_John_Doe_Full Application_...pdf
            1234567890_06_08_2026_John Doe.pdf
            Photos/
              John_Doe_1234567890_01.jpg
              ...
        Dec 2/
          ...
      PostBacc/
        Round 1/
          ...
"""

import os
import random
import zipfile
from datetime import date
from io import BytesIO
from pathlib import Path

from django.core.management.base import BaseCommand

# ── Content pools ─────────────────────────────────────────────────────────────

MAJORS = [
    "Biology", "Biochemistry", "Neuroscience", "Chemistry",
    "Microbiology", "Psychology", "Public Health", "Kinesiology",
    "Molecular Biology", "Cell Biology", "Human Physiology",
]

UNIVERSITIES = [
    "University of Florida", "Temple University", "Penn State University",
    "University of Pennsylvania", "Drexel University", "Rutgers University",
    "University of Michigan", "Ohio State University", "NYU", "Boston University",
    "University of Maryland", "Georgetown University", "Villanova University",
    "University of Pittsburgh", "Case Western Reserve University",
    "Fordham University", "University of Delaware", "Lehigh University",
]

DENTAL_ORGS = [
    "Bright Smiles Dental", "Family Dental Care", "Community Health Center",
    "University Dental Clinic", "Sunrise Dental Group", "City Dental Associates",
    "Northeast Dental Partners", "Lakeside Oral Health", "Metro Dental Group",
]

SHADOWING_ORGS = [
    "Great Expressions Dental Centers", "Aspen Dental",
    "General Dentistry of Philadelphia", "University Hospital Dental",
    "North Shore Oral Surgery", "Pediatric Dental Associates",
    "Regional Dental Specialists", "Downtown Family Dental",
]

ACHIEVEMENTS = [
    ("Honors", "Dean's List", "College of Liberal Arts and Sciences"),
    ("Scholarships", "Academic Excellence Award", "State Dental Foundation"),
    ("Honors", "Phi Beta Kappa", "National Honor Society"),
    ("Awards", "Community Service Award", "City Health Department"),
    ("Honors", "Summa Cum Laude", "University Academic Affairs"),
    ("Research", "Undergraduate Research Award", "University Research Office"),
    ("Scholarships", "Merit Scholarship", "University Financial Aid"),
]

PERSONAL_STATEMENTS = [
    (
        "Growing up watching my grandmother struggle with tooth pain that went untreated for years "
        "because of cost and access, I understood early that oral health is inseparable from overall "
        "wellbeing. That experience planted a seed. Years of shadowing, volunteering, and research "
        "have grown it into a clear calling: I want to be a dentist who makes quality care accessible "
        "and human. I am ready to bring my full self — my curiosity, my hands, and my commitment — "
        "to this program and to the patients who will one day trust me with their care."
    ),
    (
        "The first time I assisted chairside, I noticed something the textbooks had not prepared me "
        "for: the patient's breathing slowed the moment the dentist spoke calmly to her. Dentistry "
        "is not just precision with instruments — it is precision with people. That insight has "
        "shaped every hour I have spent in clinics and labs since. I want to train where clinical "
        "rigor and patient-centered care are treated as the same thing, because I believe they are."
    ),
    (
        "My path to dentistry runs through service. Through free clinic volunteering, RAM missions, "
        "and community health fairs, I have seen how much a healthy smile matters to a person's "
        "confidence and quality of life. I have also seen how rarely it is prioritized in "
        "underserved communities. I intend to close that gap — one patient at a time, starting "
        "with the training I will receive here."
    ),
    (
        "Precision and patience are values I learned long before I ever held a dental instrument. "
        "Growing up in a household where both parents worked in education, I came to understand "
        "that the best teachers — and the best clinicians — never stop learning. Every rotation, "
        "every shadowing experience, every volunteer shift has reinforced that dentistry is where "
        "I am supposed to be. I bring technical aptitude, genuine empathy, and the resilience to "
        "see a long training through to its end."
    ),
]

MANUAL_DEXTERITY = [
    (
        "Playing classical piano for twelve years has trained my hands for precision and control "
        "under pressure. I also enjoy woodworking, where measuring and cutting to tight tolerances "
        "is non-negotiable. Rock climbing has developed my grip strength and spatial awareness."
    ),
    (
        "I have been drawing and painting since childhood, which developed fine motor control and "
        "an eye for detail. I also build scale models, assemble electronics, and have recently "
        "taken up calligraphy — all activities that demand steady, deliberate movement."
    ),
    (
        "Suturing practice kits, origami, and competitive table tennis have all sharpened my "
        "hand-eye coordination. I also worked summers in carpentry, where precision measurement "
        "and tool control are daily requirements."
    ),
    (
        "Years of competitive swimming developed my body awareness and fine motor stamina. "
        "Outside of athletics I enjoy jewelry making, knitting, and assembling LEGO Technic "
        "sets — all of which require sustained dexterity and attention to detail."
    ),
]


# ── PDF and image generators ──────────────────────────────────────────────────

def _make_application_pdf(candidate):
    """Build an AADSAS-style application PDF with all parseable fields."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.units import inch
    except ImportError:
        return b"%PDF-1.4 stub"

    buf  = BytesIO()
    doc  = SimpleDocTemplate(buf, pagesize=letter,
                             leftMargin=0.75*inch, rightMargin=0.75*inch,
                             topMargin=0.75*inch, bottomMargin=0.75*inch)
    styl = getSampleStyleSheet()
    N    = styl['Normal']
    B    = ParagraphStyle('B', parent=N, fontName='Helvetica-Bold')
    H    = ParagraphStyle('H', parent=N, fontName='Helvetica-Bold', fontSize=12, spaceAfter=4)
    S    = ParagraphStyle('S', parent=N, fontSize=8)

    c   = candidate
    dat = c['dat']
    gpa = c['gpa']
    dob = c['dob'].strftime('%m-%d-%Y')

    story = []
    def p(text, style=S):  story.append(Paragraph(text, style))
    def sp(h=6):           story.append(Spacer(1, h))

    # Summary / page 1
    p(f"Phone: {c['phone']} Type: Mobile*")
    p(f"Email: {c['email']} Type: Home*")
    p(f"DENTPIN: {random.randint(10000000, 99999999)}")
    p(f"Date of Birth: {dob}")
    p("Birth Country: United States")
    p(f"Sex: {c['gender'].upper()} Citizenship Status: U.S. Citizen")
    p(f"State of Residence: {c['state']}")
    sp()
    p("APPLICANT SUMMARY", H)
    p("BIOGRAPHIC INFORMATION", H)
    p("COLLEGES ATTENDED", B)
    p(f"Institution: {c['university']}")
    p(f"Major: {c['major']}  Degree: Bachelor of Science  Degree Date: 05-2026")
    sp()
    p("CALCULATED GPA", B)
    p("Title BCP GPA BCP HRS SCI GPA SCI HRS NSCI GPA NSCI HRS TOT GPA TOT HRS")
    p(
        f"UnderGraduate {gpa['bcp']} 64.00 {gpa['science']} 102.00 "
        f"3.86 58.00 {gpa['overall']} 160.00"
    )
    sp()
    p("OFFICIAL DAT (After Mar 1 2025)", B)
    p("Date Academic Avg PAT Quantative Reas Reading Comp Bio General Chem Organic Chem Total Sci")
    p(
        f"05-22-2025 {dat['academic_avg']} {dat['pat']} {dat['quant']} "
        f"{dat['reading']} {dat['bio']} {dat['gen_chem']} {dat['org_chem']} {dat['total_sci']}"
    )
    sp(12)

    # Biographic detail
    p("BIOGRAPHIC INFORMATION", H)
    p("PROFILE", B)
    p(f"Legal First Name: {c['first_name']}")
    p(f"Last Name: {c['last_name']}")
    p(f"Sex: {c['gender'].upper()}")
    sp()
    p("BIRTH INFORMATION", B)
    p(f"Date of Birth: {dob}")
    p(f"State: {c['state']}")
    p("Country: United States")
    sp()
    p("CONTACT INFORMATION", B)
    p(f"Preferred Phone Number {c['phone']} Type: Mobile")
    p(f"Email: {c['email']} Type: Home")
    sp()
    p("CITIZENSHIP STATUS AND RESIDENCY INFORMATION", B)
    p("Citizenship Status: U.S. Citizen")
    p(f"State of Residence: {c['state']}")
    sp(12)

    # Experiences
    p("SUPPORTING INFORMATION", H)
    p("EXPERIENCE", B)
    p(f"DENTAL RELATED EXPERIENCE TOTAL HOURS: {c['dental_hours']}", B)
    p("* Experience Type: Dental Experience")
    p(f"Title: Dental Assistant")
    p(f"Employer: {random.choice(DENTAL_ORGS)}")
    p(f"Total Hours: {c['dental_hours']}")
    sp()
    p(f"SHADOWING EXPERIENCE TOTAL HOURS: {c['shadow_hours']}", B)
    p("* Experience Type: Dental Shadowing (In-Person)")
    p(f"Title: Shadowing")
    p(f"Employer: {random.choice(SHADOWING_ORGS)}")
    p(f"Total Hours: {c['shadow_hours']}")
    sp(12)

    # Achievements
    achievement = random.choice(ACHIEVEMENTS)
    p("ACHIEVEMENTS", H)
    p(f"* {achievement[0].upper()}")
    p(f"Name: {achievement[1]}")
    p(f"Organization: {achievement[2]}")
    sp(12)

    # Personal statement
    p("PERSONAL STATEMENT", H)
    p("What motivated you to pursue a career in oral health?")
    p(f"Answer: {random.choice(PERSONAL_STATEMENTS)}")
    sp(12)

    # Custom questions
    p("CUSTOM QUESTIONS", H)
    p("FIRST-GENERATION COLLEGE STUDENT", B)
    p("* 1. Are you a first-generation college student?")
    p(f"Answer: {'Yes' if c['first_gen'] else 'No'}")
    sp()
    p("MANUAL DEXTERITY", B)
    p("1. Describe any activities requiring manual dexterity.")
    p(f"Answer: {random.choice(MANUAL_DEXTERITY)}")
    sp()
    p("PREVIOUS APPLICATIONS TO US DENTAL SCHOOLS", B)
    p("* 1. Have you ever applied to US dental school prior to the present application cycle?")
    p(f"Answer: {'Yes' if c['re_applicant'] else 'No'}")
    sp(12)

    p(f"ADEA AADSAS 2025-2026 {c['last_name']}, {c['first_name']}")
    p(f"Applicant ID: {c['aadsas_id']}  Application Status: Verified")
    p("The Maurice H. Kornberg School of Dentistry Temple University Doctor of Dental Medicine")

    doc.build(story)
    return buf.getvalue()


def _make_evaluation_pdf(candidate):
    """Minimal evaluation sheet — date-named so it categorises correctly."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from reportlab.lib.units import inch
    except ImportError:
        return b"%PDF-1.4 eval"

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            leftMargin=inch, rightMargin=inch,
                            topMargin=inch, bottomMargin=inch)
    styl = getSampleStyleSheet()
    story = [
        Paragraph("Candidate Evaluation Summary", styl['Heading1']),
        Spacer(1, 12),
        Paragraph(f"Name: {candidate['last_name']}, {candidate['first_name']}", styl['Normal']),
        Paragraph(f"Applicant ID: {candidate['aadsas_id']}", styl['Normal']),
        Paragraph("Program: Doctor of Dental Medicine", styl['Normal']),
        Spacer(1, 24),
        Paragraph("Interview Notes", styl['Heading2']),
        Spacer(1, 12),
        Paragraph(
            "Candidate presented professionally and demonstrated strong motivation. "
            "Communication skills clear and thoughtful. Recommend for committee review.",
            styl['Normal']
        ),
    ]
    doc.build(story)
    return buf.getvalue()


def _make_placeholder_jpg():
    """Small solid-colour JPEG headshot placeholder."""
    try:
        from PIL import Image, ImageDraw
        colours = [
            (200, 210, 230), (210, 225, 210), (230, 215, 200),
            (220, 200, 225), (200, 225, 225), (225, 215, 205),
        ]
        bg  = random.choice(colours)
        img = Image.new('RGB', (200, 200), color=bg)
        draw = ImageDraw.Draw(img)
        # Simple silhouette shape
        draw.ellipse([65, 20, 135, 90], fill=(190, 170, 155))
        draw.rectangle([50, 95, 150, 175], fill=(160, 150, 165))
        buf = BytesIO()
        img.save(buf, format='JPEG', quality=75)
        return buf.getvalue()
    except Exception:
        import base64
        return base64.b64decode(
            "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8U"
            "HRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAAR"
            "CAABAAEDASIAAhEBAxEB/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/"
            "xAAUAQEAAAAAAAAAAAAAAAAAAAAA/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAwDAQACEQMRAD8AJQAB/9k="
        )


# ── Stat generators ───────────────────────────────────────────────────────────

def _random_dat():
    base = random.randint(380, 480)
    return {
        'academic_avg': min(600, max(200, base + random.randint(-20, 20))),
        'pat':          min(600, max(200, base + random.randint(-40, 30))),
        'quant':        min(600, max(200, base + random.randint(-30, 30))),
        'reading':      min(600, max(200, base + random.randint(-20, 40))),
        'bio':          min(600, max(200, base + random.randint(-20, 30))),
        'gen_chem':     min(600, max(200, base + random.randint(-30, 20))),
        'org_chem':     min(600, max(200, base + random.randint(-40, 30))),
        'total_sci':    min(600, max(200, base + random.randint(-20, 20))),
    }


def _random_gpa():
    overall = round(random.uniform(3.1, 4.0), 2)
    science = round(max(2.5, min(4.0, overall + random.uniform(-0.3, 0.1))), 2)
    bcp     = round(max(2.5, min(4.0, science + random.uniform(-0.2, 0.1))), 2)
    return {'overall': overall, 'science': science, 'bcp': bcp}


# ── Config parser ─────────────────────────────────────────────────────────────

DEFAULT_CONFIG = [
    {
        'dataset': '4yr DMD 2025-2026',
        'batches': [
            ('September 5',  10),
            ('October 3',    10),
            ('November 7',   10),
        ],
    },
    {
        'dataset': 'Post-Bacc 2025-2026',
        'batches': [
            ('October 10',   10),
            ('November 14',   8),
        ],
    },
    {
        'dataset': '3+4 DMD 2025-2026',
        'batches': [
            ('September 20',  6),
        ],
    },
]


def _parse_config(raw):
    """
    Parse --config string into the same structure as DEFAULT_CONFIG.
    Format: "DatasetName:BatchName:Count,BatchName:Count|DatasetName:BatchName:Count"
    """
    result = []
    for ds_block in raw.split('|'):
        parts = ds_block.strip().split(':')
        if len(parts) < 3:
            continue
        dataset_name = parts[0].strip()
        batches = []
        # remaining tokens come in pairs: name, count
        batch_tokens = ':'.join(parts[1:]).split(',')
        for token in batch_tokens:
            token = token.strip()
            # last word should be the count
            pieces = token.rsplit(':', 1) if ':' in token else token.rsplit(' ', 1)
            if len(pieces) == 2:
                try:
                    batches.append((pieces[0].strip(), int(pieces[1].strip())))
                except ValueError:
                    pass
        if batches:
            result.append({'dataset': dataset_name, 'batches': batches})
    return result or DEFAULT_CONFIG


# ── Management command ────────────────────────────────────────────────────────

class Command(BaseCommand):
    help = "Generate fake candidate folders for sandbox upload testing"

    def add_arguments(self, parser):
        parser.add_argument(
            '--out-dir', type=str, default='',
            help='Output directory (default: ./sandbox_candidates)'
        )
        parser.add_argument(
            '--config', type=str, default='',
            help=(
                'Dataset/batch structure. '
                'Format: "DatasetName:BatchName:Count,BatchName:Count|DatasetName:..."  '
                'Default: 3 datasets, multiple batches each.'
            )
        )
        parser.add_argument(
            '--zip', action='store_true',
            help='Create a .zip archive alongside each dataset folder'
        )

    def handle(self, *args, **options):
        try:
            from faker import Faker
        except ImportError:
            self.stderr.write(self.style.ERROR(
                "faker is required. Run: pip install faker"
            ))
            return

        fake    = Faker()
        out_dir = Path(options['out_dir']) if options['out_dir'] else Path.cwd() / 'sandbox_candidates'
        config  = _parse_config(options['config']) if options['config'] else DEFAULT_CONFIG
        make_zip = options['zip']

        out_dir.mkdir(parents=True, exist_ok=True)
        used_ids = set()
        today    = date.today()
        total_candidates = 0

        self.stdout.write(self.style.MIGRATE_HEADING(
            f"\nGenerating sandbox candidates → {out_dir}\n"
        ))

        for ds_cfg in config:
            dataset_name = ds_cfg['dataset']
            self.stdout.write(self.style.MIGRATE_LABEL(f"\n  Dataset: {dataset_name}"))

            for batch_name, count in ds_cfg['batches']:
                self.stdout.write(f"    Batch: {batch_name}  ({count} candidates)")

                batch_dir = out_dir / dataset_name / batch_name
                batch_dir.mkdir(parents=True, exist_ok=True)

                for i in range(count):
                    # Unique AADSAS ID
                    while True:
                        aadsas_id = str(random.randint(1000000000, 9999999999))
                        if aadsas_id not in used_ids:
                            used_ids.add(aadsas_id)
                            break

                    first_name = fake.first_name()
                    last_name  = fake.last_name()

                    candidate = {
                        'first_name':    first_name,
                        'last_name':     last_name,
                        'aadsas_id':     aadsas_id,
                        'gender':        random.choice(['Male', 'Female']),
                        'dob':           fake.date_of_birth(minimum_age=20, maximum_age=32),
                        'email':         fake.email(),
                        'phone':         f"+1{random.randint(2000000000, 9999999999)}",
                        'state':         fake.state(),
                        'university':    random.choice(UNIVERSITIES),
                        'major':         random.choice(MAJORS),
                        'dat':           _random_dat(),
                        'gpa':           _random_gpa(),
                        'first_gen':     random.random() < 0.25,
                        're_applicant':  random.random() < 0.15,
                        'dental_hours':  random.randint(80, 600),
                        'shadow_hours':  random.randint(50, 500),
                    }

                    # Candidate folder: "Last, First - AADSASID"
                    folder_name = f"{last_name}, {first_name} - {aadsas_id}"
                    cand_dir    = batch_dir / folder_name
                    photos_dir  = cand_dir / "Photos"
                    cand_dir.mkdir(exist_ok=True)
                    photos_dir.mkdir(exist_ok=True)

                    # Full application PDF
                    app_pdf_name = (
                        f"{aadsas_id}_{first_name}_{last_name}_"
                        f"Full Application_{random.randint(400000,500000)}_"
                        f"Doctor of Dental Medicine.pdf"
                    )
                    (cand_dir / app_pdf_name).write_bytes(_make_application_pdf(candidate))

                    # Evaluation PDF (date-named)
                    eval_date = today.strftime('%m_%d_%Y')
                    eval_name = f"{aadsas_id}_{eval_date}_{first_name} {last_name}.pdf"
                    (cand_dir / eval_name).write_bytes(_make_evaluation_pdf(candidate))

                    # 3 placeholder headshot JPGs
                    for j in range(1, 4):
                        jpg_name = f"{first_name}_{last_name}_{aadsas_id}_{j:02d}.jpg"
                        (photos_dir / jpg_name).write_bytes(_make_placeholder_jpg())

                    self.stdout.write(
                        f"      [{i+1:2d}/{count}] {last_name}, {first_name}  "
                        f"GPA {candidate['gpa']['overall']}  "
                        f"DAT {candidate['dat']['academic_avg']}"
                    )
                    total_candidates += 1

                # Optional zip per batch
                if make_zip:
                    zip_path = out_dir / dataset_name / f"{batch_name}.zip"
                    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                        for f in batch_dir.rglob('*'):
                            zf.write(f, f.relative_to(batch_dir.parent))
                    self.stdout.write(f"      → zipped: {zip_path.name}")

        self.stdout.write(self.style.SUCCESS(
            f"\n✅  {total_candidates} candidates across "
            f"{sum(len(d['batches']) for d in config)} batches in "
            f"{len(config)} datasets.\n"
            f"    Output: {out_dir}\n\n"
            f"HOW TO UPLOAD:\n"
            f"  For each dataset, go to Admin → Bulk Upload, select the dataset,\n"
            f"  then select the DATASET folder (e.g. '4yr DMD 2025-2026').\n"
            f"  All date-named batch subfolders will be uploaded at once and\n"
            f"  each date folder becomes its own batch automatically.\n"
        ))