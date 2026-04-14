"""
generate_sample_pdfs.py
-----------------------
Generates sample PDF files of approximately 40 MB, 50 MB, and 60 MB.

Strategy:
  - Uses reportlab to create a proper PDF with text content per page.
  - Embeds a large raw binary stream (random bytes wrapped in a PDF stream object)
    to pad each file to the target size accurately.
  - Output files are valid, openable PDFs.

Requirements:
    pip install reportlab

Usage:
    python generate_sample_pdfs.py
"""

import os
import io
import struct
import random
import string
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak
from reportlab.lib.units import cm



# ── Target sizes ──────────────────────────────────────────────────────────────
TARGET_SIZES_MB = [40, 50, 60]
OUTPUT_DIR = "."          # change to your preferred output directory


# ── Helpers ───────────────────────────────────────────────────────────────────

def random_text(n_chars: int) -> str:
    """Generate a block of random readable text."""
    words = []
    pool = string.ascii_letters + "     "          # spaces make it word-like
    chunk = 80
    while n_chars > 0:
        take = min(chunk, n_chars)
        words.append("".join(random.choices(pool, k=take)))
        n_chars -= take
    return " ".join(words)


def build_base_pdf(n_pages: int = 5) -> bytes:
    """
    Build a small but valid multi-page PDF with real text content.
    Returns the PDF bytes.
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    story = []

    for page_num in range(1, n_pages + 1):
        story.append(Paragraph(f"Sample Document — Page {page_num}", styles["Title"]))
        story.append(Spacer(1, 0.4 * cm))
        story.append(
            Paragraph(
                "This is a simulated sample PDF generated for testing purposes. "
                "It contains multiple pages of placeholder content and is padded "
                "to an exact target file size using an embedded binary stream.",
                styles["Normal"],
            )
        )
        story.append(Spacer(1, 0.3 * cm))
        # Fill the page with random-looking body text
        body = random_text(1200)
        story.append(Paragraph(body, styles["Normal"]))
        if page_num < n_pages:
            story.append(PageBreak())

    doc.build(story)
    return buf.getvalue()


def embed_padding_stream(pdf_bytes: bytes, pad_size: int) -> bytes:
    """
    Append a raw (non-rendering) PDF stream object to an existing PDF
    that carries `pad_size` bytes of random data, then re-write the
    cross-reference table and trailer so the file remains valid.

    Approach:
      1. Strip the existing %%EOF.
      2. Append a new indirect object containing a FlateDecode-less stream
         of the exact required size.
      3. Append an incremental xref + trailer pointing to this new object.
    """
    # Remove trailing whitespace / %%EOF
    core = pdf_bytes.rstrip()
    if core.endswith(b"%%EOF"):
        core = core[: -len(b"%%EOF")].rstrip()

    # Determine the next free object number by scanning existing objects
    # (simple heuristic: count "obj" keywords)
    obj_count = pdf_bytes.count(b" obj\n") + pdf_bytes.count(b" obj\r\n")
    new_obj_id = obj_count + 1

    # Random binary payload (compressible padding would shrink — use pseudo-random)
    payload = bytes(random.getrandbits(8) for _ in range(pad_size))

    # Build the new stream object
    stream_obj = (
        f"{new_obj_id} 0 obj\n"
        f"<< /Type /EmbeddedFile /Length {pad_size} >>\n"
        f"stream\n"
    ).encode("latin-1")
    stream_end = b"\nendstream\nendobj\n"

    new_obj_offset = len(core) + 1  # +1 for the \n we'll add

    # Incremental xref
    xref_offset = new_obj_offset + len(stream_obj) + pad_size + len(stream_end)

    xref_section = (
        f"xref\n"
        f"{new_obj_id} 1\n"
        f"{new_obj_offset:010d} 00000 n \n"
        f"trailer\n"
        f"<< /Size {new_obj_id + 1} >>\n"
        f"startxref\n"
        f"{xref_offset}\n"
        f"%%EOF\n"
    ).encode("latin-1")

    return b"\n".join([core, stream_obj, payload, stream_end, xref_section])


def generate_pdf(target_mb: int, output_path: str) -> None:
    target_bytes = target_mb * 1024 * 1024
    print(f"  Building base PDF …", end=" ", flush=True)
    base = build_base_pdf(n_pages=6)
    base_size = len(base)
    print(f"base = {base_size / 1024:.1f} KB")

    overhead = 300          # approximate bytes added by xref / stream headers
    pad_needed = target_bytes - base_size - overhead

    if pad_needed < 0:
        raise ValueError(
            f"Base PDF ({base_size} B) already exceeds target ({target_bytes} B). "
            "Increase target size."
        )

    print(f"  Embedding {pad_needed / 1024 / 1024:.2f} MB padding stream …", end=" ", flush=True)
    final_pdf = embed_padding_stream(base, pad_needed)

    # Fine-tune: trim or pad trailing bytes to hit target exactly
    delta = target_bytes - len(final_pdf)
    if delta > 0:
        # append a PDF comment to fill the gap
        final_pdf += b"%" + b"X" * (delta - 2) + b"\n"
    elif delta < 0:
        # over-shot: reduce pad by |delta| and regenerate
        pad_needed += delta          # delta is negative
        final_pdf = embed_padding_stream(base, max(pad_needed, 0))

    with open(output_path, "wb") as f:
        f.write(final_pdf)

    actual_mb = os.path.getsize(output_path) / 1024 / 1024
    print(f"done → {actual_mb:.2f} MB  [{output_path}]")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("  Sample PDF Generator")
    print("=" * 60)

    for size_mb in TARGET_SIZES_MB:
        filename = f"sample_{size_mb}mb.pdf"
        output_path = os.path.join(OUTPUT_DIR, filename)
        print(f"\n[{size_mb} MB] → {filename}")
        generate_pdf(size_mb, output_path)

    print("\n" + "=" * 60)
    print("  All files generated successfully.")
    print("=" * 60)


if __name__ == "__main__":
    main()
