"""
Run this from your gradapp directory:
    python create_test_batch.py

It will create a folder called "December 1 Testing" on your Desktop
with fake candidate subfolders, PDFs, and dummy video files —
matching exactly what your real interview batches look like.
"""

import os
import pathlib

# ── Where to create the test batch ──────────────────────────────────────────
DESKTOP = pathlib.Path.home() / "Desktop"
BATCH_FOLDER = DESKTOP / "December 1 Testing"

# ── Fake candidates (LastName, FirstName - UniqueID) ────────────────────────
CANDIDATES = [
    ("Ali, Huzaifa",        "6815031005"),
    ("Lam, Grace",          "6726243347"),
    ("Marrocco, Kristina",  "2486633842"),
    ("McPhillips, Alexandria", "2921168421"),
    ("Mistry, Fiona",       "5086122885"),
    ("Okan, Fatma",         "8934550961"),
]

def make_fake_pdf(path: pathlib.Path, candidate_name: str, uid: str, doc_type: str):
    """Write a minimal valid PDF with some candidate info embedded."""
    content = f"""%PDF-1.4
1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj
2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj
3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]
  /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>
endobj
4 0 obj << /Length 200 >>
stream
BT
/F1 14 Tf
50 750 Td
({doc_type}) Tj
0 -30 Td
(Name: {candidate_name}) Tj
0 -30 Td
(ID: {uid}) Tj
0 -30 Td
(Email: {uid}@testschool.edu) Tj
ET
endstream
endobj
5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj
xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000058 00000 n
0000000115 00000 n
0000000274 00000 n
0000000528 00000 n
trailer << /Size 6 /Root 1 0 R >>
startxref
625
%%EOF"""
    path.write_text(content)

def make_fake_video(path: pathlib.Path):
    """Write a tiny dummy MP4 (not a real video, but correct extension for testing file grouping)."""
    # Just write some bytes — enough to create the file
    path.write_bytes(b'\x00' * 1024)

def make_fake_jpg(path: pathlib.Path):
    """Write a minimal JPEG header."""
    # Minimal valid JPEG bytes
    path.write_bytes(bytes([
        0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01,
        0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00, 0xFF, 0xD9
    ]))

def build_test_batch():
    print(f"\n📁 Creating test batch at: {BATCH_FOLDER}\n")
    BATCH_FOLDER.mkdir(parents=True, exist_ok=True)

    # Also add the Excel schedule file in the batch root (like in your screenshots)
    (BATCH_FOLDER / "12.1.26 Interview Schedule.xlsx").write_bytes(b'\x00' * 512)
    print(f"  ✅ Created: 12.1.26 Interview Schedule.xlsx (batch root, should be skipped by uploader)")

    for name, uid in CANDIDATES:
        folder_name = f"{name} - {uid}"
        candidate_dir = BATCH_FOLDER / folder_name
        candidate_dir.mkdir(exist_ok=True)

        # 1. Full application PDF (named like your real files)
        app_pdf = candidate_dir / f"{uid}_{name.replace(', ', '_').replace(' ', '_')}_Full_Application.pdf"
        make_fake_pdf(app_pdf, name, uid, "Full Application")

        # 2. Interview evaluation PDF
        eval_pdf = candidate_dir / f"{name.replace(', ', '_')}_Interview_Evaluation_{uid}.pdf"
        make_fake_pdf(eval_pdf, name, uid, "Interview Evaluation")

        # 3. Interview video (MP4)
        video1 = candidate_dir / f"GMT20251201-181047_Recording_1280x720.mp4"
        make_fake_video(video1)

        # 4. Second video clip
        video2 = candidate_dir / f"GMT20251201-181047_Recording_1280x720_part2.mp4"
        make_fake_video(video2)

        # 5. Screenshot JPGs (like in your real folders)
        for i in range(1, 4):
            jpg = candidate_dir / f"zz{i}.GMT20251201-181047_Recording_1280x72_{i:04d}.jpg"
            make_fake_jpg(jpg)

        file_count = len(list(candidate_dir.iterdir()))
        print(f"  ✅ {folder_name}/ — {file_count} files")

    print(f"\n🎉 Done! Your test batch folder is ready on your Desktop.")
    print(f"   Open your bulk upload page and select:\n   {BATCH_FOLDER}")
    print(f"\n   The uploader should detect {len(CANDIDATES)} candidates")
    print(f"   and skip the Excel schedule file in the batch root.\n")

if __name__ == "__main__":
    build_test_batch()