"""
Management command: generate_test_candidates

Creates a folder of fake candidate subfolders mimicking real AADSAS upload structure.
Designed to test smart batching and overflow naming (e.g. "September 3 - September 4").

Usage:
    python manage.py generate_test_candidates
    python manage.py generate_test_candidates --out-dir C:/Users/you/Desktop/sandbox
    python manage.py generate_test_candidates --config "4yr DMD:September 3:13,September 4:8"

DEFAULT_CONFIG produces overflow scenarios:
  - September 3:  13 candidates  → batch "September 3" (10) + overflow into Sept 4
  - September 4:   6 candidates  → batch "September 3 - September 4" (3 overflow + 6 = 9)
  - October 1:    11 candidates  → batch "October 1" (10) + overflow into Oct 15
  - October 15:    4 candidates  → batch "October 1 - October 15" (1 overflow + 4 = 5)
  - November 7:   10 candidates  → batch "November 7" (exactly 10, no overflow)
  - December 2:    3 candidates  → batch "December 2" (3, standalone)
"""

import os
import random
import shutil
from datetime import date, timedelta
from io import BytesIO

from django.core.management.base import BaseCommand

try:
    from faker import Faker
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib import colors
    from PIL import Image as PILImage
except ImportError as e:
    raise ImportError(
        f"Missing dependency: {e}. "
        "Run: pip install faker reportlab pillow"
    )

fake = Faker()

# ---------------------------------------------------------------------------
# Default config — designed to trigger overflow on multiple dates
# Format: "DatasetName:DateFolder:count,DateFolder:count|DatasetName:..."
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = (
    "4yr DMD AADSAS:"
    "September 3:13,"   # overflows 3 into September 4
    "September 4:6,"    # receives 3 overflow → batch of 9
    "October 1:11,"     # overflows 1 into October 15
    "October 15:4,"     # receives 1 overflow → batch of 5
    "November 7:10,"    # exactly 10, clean batch
    "December 2:3"      # standalone small batch
)


def _random_gpa():
    return round(random.uniform(2.80, 4.00), 2)


def _random_dat():
    return random.randint(170, 300)


def _random_dat_sub():
    return random.randint(150, 300)


def _random_hours(low, high):
    return random.randint(low, high)


def _make_application_pdf(first_name, last_name, unique_id, dob, gender,
                           state, citizenship, gpa_bcp, gpa_sci, gpa_tot,
                           dat_aa, dat_pat, dat_qr, dat_rc, dat_bio,
                           dat_gc, dat_oc, dat_ts, dental_hrs, shadow_hrs,
                           first_gen, email):
    """Generate a minimal AADSAS-style application PDF that _extract_pdf_data can parse."""
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter,
                            topMargin=0.5*inch, bottomMargin=0.5*inch,
                            leftMargin=0.75*inch, rightMargin=0.75*inch)
    styles = getSampleStyleSheet()
    normal = styles['Normal']
    bold_style = ParagraphStyle('bold', parent=normal, fontName='Helvetica-Bold')
    story = []

    def p(text, style=normal):
        story.append(Paragraph(text, style))
        story.append(Spacer(1, 4))

    # Header
    p(f"<b>AADSAS Application — {last_name}, {first_name}</b>", bold_style)
    p(f"Application ID: {unique_id}")
    story.append(Spacer(1, 8))

    # Personal info block — parsed by regex in _extract_pdf_data
    dob_str = dob.strftime('%m-%d-%Y')
    p(f"Date of Birth: {dob_str}")
    sex_str = "MALE" if gender.lower() == "male" else "FEMALE"
    p(f"\nSex: {sex_str}")
    p(f"Email: {email}")
    p(f"Citizenship Status: {citizenship}")
    p(f"State of Residence: {state}")
    story.append(Spacer(1, 8))

    # GPA table — matches regex: UnderGraduate  BCP  _  SCI  _  _  _  TOT
    p("<b>Academic Record</b>", bold_style)
    gpa_data = [
        ['Level', 'BCP GPA', 'BCP HRS', 'SCI GPA', 'SCI HRS', 'NSCI GPA', 'NSCI HRS', 'TOT GPA', 'TOT HRS'],
        ['UnderGraduate',
         str(gpa_bcp), str(random.randint(30, 60)),
         str(gpa_sci), str(random.randint(40, 80)),
         str(round(random.uniform(2.8, 4.0), 2)), str(random.randint(20, 50)),
         str(gpa_tot), str(random.randint(100, 140))],
    ]
    t = Table(gpa_data, hAlign='LEFT')
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
    ]))
    story.append(t)
    story.append(Spacer(1, 12))

    # DAT scores — matches regex: OFFICIAL DAT ... date  AA PAT QR RC Bio GC OC TS
    p("<b>DAT Scores</b>", bold_style)
    dat_date = (date.today() - timedelta(days=random.randint(180, 730))).strftime('%m-%d-%Y')
    dat_data = [
        ['', 'Date', 'Acad Avg', 'PAT', 'Quant', 'Reading', 'Biology', 'Gen Chem', 'Org Chem', 'Tot Sci'],
        ['OFFICIAL DAT', dat_date,
         str(dat_aa), str(dat_pat), str(dat_qr), str(dat_rc),
         str(dat_bio), str(dat_gc), str(dat_oc), str(dat_ts)],
    ]
    t2 = Table(dat_data, hAlign='LEFT')
    t2.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
        ('BACKGROUND', (0, 0), (-1, 0), colors.lightgrey),
    ]))
    story.append(t2)
    story.append(Spacer(1, 12))

    # Experience hours
    p("<b>Experience</b>", bold_style)
    p(f"DENTAL RELATED EXPERIENCE TOTAL HOURS: {dental_hrs}")
    p(f"SHADOWING EXPERIENCE TOTAL HOURS: {shadow_hrs}")
    story.append(Spacer(1, 8))

    # First-gen flag
    p("<b>Background Information</b>", bold_style)
    fg_answer = "Yes" if first_gen else "No"
    p(f"Are you a first-generation college student (neither parent has a 4-year degree)? Answer: {fg_answer}")

    doc.build(story)
    buf.seek(0)
    return buf.read()


def _make_placeholder_pdf(label="Evaluation Document"):
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter)
    styles = getSampleStyleSheet()
    doc.build([
        Paragraph(f"<b>{label}</b>", styles['Heading1']),
        Spacer(1, 12),
        Paragraph(fake.paragraph(nb_sentences=6), styles['Normal']),
    ])
    buf.seek(0)
    return buf.read()


def _make_placeholder_jpg(width=200, height=250, label="photo"):
    """Create a small solid-colour JPEG as a stand-in for a candidate photo."""
    colour = (
        random.randint(100, 220),
        random.randint(100, 220),
        random.randint(100, 220),
    )
    img = PILImage.new('RGB', (width, height), colour)
    buf = BytesIO()
    img.save(buf, format='JPEG', quality=70)
    buf.seek(0)
    return buf.read()


def _generate_candidate_folder(base_dir, first_name, last_name, unique_id):
    """
    Create one candidate subfolder with:
      - <unique_id>_application.pdf   (parseable AADSAS-style)
      - <date>_evaluation.pdf         (placeholder)
      - photo1.jpg / photo2.jpg / photo3.jpg  (third one used as profile pic)
    """
    folder_name = f"{last_name}, {first_name} - {unique_id}"
    folder_path = os.path.join(base_dir, folder_name)
    os.makedirs(folder_path, exist_ok=True)

    gender    = random.choice(['Male', 'Female'])
    dob       = fake.date_of_birth(minimum_age=22, maximum_age=35)
    state     = fake.state_abbr()
    citizenship = random.choice(['U.S. Citizen', 'Permanent Resident', 'International'])
    email     = fake.email()
    first_gen = random.random() < 0.25

    gpa_bcp = _random_gpa()
    gpa_sci = _random_gpa()
    gpa_tot = round((gpa_bcp + gpa_sci + _random_gpa()) / 3, 2)

    dat_aa  = _random_dat()
    dat_pat = _random_dat_sub()
    dat_qr  = _random_dat_sub()
    dat_rc  = _random_dat_sub()
    dat_bio = _random_dat_sub()
    dat_gc  = _random_dat_sub()
    dat_oc  = _random_dat_sub()
    dat_ts  = _random_dat_sub()

    dental_hrs = _random_hours(50, 2000)
    shadow_hrs = _random_hours(20, 500)

    # Application PDF
    app_pdf = _make_application_pdf(
        first_name, last_name, unique_id, dob, gender,
        state, citizenship, gpa_bcp, gpa_sci, gpa_tot,
        dat_aa, dat_pat, dat_qr, dat_rc, dat_bio,
        dat_gc, dat_oc, dat_ts, dental_hrs, shadow_hrs,
        first_gen, email,
    )
    app_filename = f"{unique_id}_application.pdf"
    with open(os.path.join(folder_path, app_filename), 'wb') as f:
        f.write(app_pdf)

    # Evaluation PDF (date-named so it gets categorised as evaluation, not application)
    eval_date = date.today().strftime('%m_%d_%Y')
    eval_pdf = _make_placeholder_pdf("Interview Evaluation")
    eval_filename = f"{unique_id}_{eval_date}_evaluation.pdf"
    with open(os.path.join(folder_path, eval_filename), 'wb') as f:
        f.write(eval_pdf)

    # Three placeholder JPEGs (third = profile picture in upload logic)
    for i in range(1, 4):
        jpg_bytes = _make_placeholder_jpg(label=f"photo{i}")
        with open(os.path.join(folder_path, f"photo{i}.jpg"), 'wb') as f:
            f.write(jpg_bytes)

    return folder_name


def _parse_config(config_str):
    """
    Parse config string into:
      [ (dataset_name, [ (date_folder, count), ... ]), ... ]
    """
    result = []
    for dataset_block in config_str.split('|'):
        dataset_block = dataset_block.strip()
        if ':' not in dataset_block:
            continue
        parts = dataset_block.split(':')
        dataset_name = parts[0].strip()
        batches = []
        # remaining parts alternate: date_folder, count, date_folder, count ...
        # but they were split on ':', so pairs are joined with ','
        # re-join and re-split on commas for the batch entries
        batch_str = ':'.join(parts[1:])
        for entry in batch_str.split(','):
            entry = entry.strip()
            if not entry:
                continue
            # last token is the count, everything before is the folder name
            tokens = entry.rsplit(':', 1)
            if len(tokens) == 2:
                folder, count_str = tokens
                try:
                    batches.append((folder.strip(), int(count_str.strip())))
                except ValueError:
                    pass
        if batches:
            result.append((dataset_name, batches))
    return result


class Command(BaseCommand):
    help = (
        "Generate fake candidate folders for bulk-upload testing. "
        "Default config creates intentional overflow scenarios to test smart batch naming."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--out-dir',
            default=None,
            help='Output directory (default: sandbox_candidates/ next to manage.py)',
        )
        parser.add_argument(
            '--config',
            default=DEFAULT_CONFIG,
            help=(
                'Batch config string. '
                'Format: "DatasetName:DateFolder:count,DateFolder:count|DatasetName:..."'
            ),
        )

    def handle(self, *args, **options):
        out_dir = options['out_dir'] or os.path.join(os.getcwd(), 'sandbox_candidates')
        config  = _parse_config(options['config'])

        if not config:
            self.stderr.write("Could not parse --config. Check the format.")
            return

        if os.path.exists(out_dir):
            shutil.rmtree(out_dir)
        os.makedirs(out_dir)

        self.stdout.write(f"\nOutput directory: {out_dir}\n")
        self.stdout.write("=" * 60)

        total_candidates = 0
        used_ids = set()

        for dataset_name, date_batches in config:
            dataset_dir = os.path.join(out_dir, dataset_name)
            os.makedirs(dataset_dir, exist_ok=True)

            self.stdout.write(f"\nDataset: {dataset_name}")
            self.stdout.write("-" * 40)

            for date_folder, count in date_batches:
                batch_dir = os.path.join(dataset_dir, date_folder)
                os.makedirs(batch_dir, exist_ok=True)

                self.stdout.write(f"  {date_folder}: {count} candidate(s)")

                for _ in range(count):
                    first_name = fake.first_name()
                    last_name  = fake.last_name()

                    # Generate unique 10-digit AADSAS-style ID
                    while True:
                        uid = str(random.randint(1_000_000_000, 9_999_999_999))
                        if uid not in used_ids:
                            used_ids.add(uid)
                            break

                    folder = _generate_candidate_folder(
                        batch_dir, first_name, last_name, uid
                    )
                    self.stdout.write(f"    + {folder}")
                    total_candidates += 1

        self.stdout.write("\n" + "=" * 60)
        self.stdout.write(f"✅  Generated {total_candidates} candidate(s) in {out_dir}\n")

        # Print expected batch outcome — mirrors _assign_candidates_to_batches exactly
        self.stdout.write("\nExpected batch naming (with BATCH_MAX_SIZE=10):")
        self.stdout.write("-" * 60)
        for dataset_name, date_batches in config:
            self.stdout.write(f"\n  {dataset_name}:")
            groups = {d: list(range(c)) for d, c in date_batches}
            sorted_dates = [d for d, _ in date_batches]

            carry = []
            carry_dates = []
            preview_batches = []

            for date_folder, _ in date_batches:
                pool = carry + groups[date_folder]
                current_dates = carry_dates + [date_folder]
                carry = []
                carry_dates = []
                full_count = 0
                while len(pool) >= 10:
                    preview_batches.append((list(current_dates), len(pool[:10])))
                    pool = pool[10:]
                    current_dates = [date_folder]
                    full_count += 1
                if pool:
                    if full_count > 0:
                        carry = pool
                        carry_dates = [date_folder]
                    else:
                        preview_batches.append((list(current_dates), len(pool)))

            if carry:
                preview_batches.append((carry_dates, len(carry)))

            for dates, count in preview_batches:
                seen = set()
                unique = []
                for d in dates:
                    if d not in seen:
                        seen.add(d)
                        unique.append(d)
                name = ' - '.join(unique)
                self.stdout.write(f"    → '{name}': {count} candidate(s)")

        self.stdout.write(
            "\nTo use: select the dataset in Bulk Upload, then upload each date folder.\n"
        )