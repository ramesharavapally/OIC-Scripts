#!/usr/bin/env python3
"""
OIC IAR Scanner – Script 2: Notification Details (From / To / CC / Subject)
=============================================================================
DROP YOUR .iar FILES INTO THE FOLDER CONFIGURED BELOW, THEN JUST RUN:

    python scan_notifications.py

No arguments needed. Results are printed to console AND written to a CSV
in the same folder as this script.

What it extracts from every notification activity in each IAR:
  - FROM    (FROM_PARAM expr.properties)
  - TO      (TO_PARAM expr.properties)
  - CC      (CC_PARAM  expr.properties)  – only if present
  - SUBJECT (SUBJECT_PARAM expr.properties)
  - Body preview (notification_body.data)

Each field is classified as:
  HARDCODED – a literal string value, e.g. 'oic-noreply@oracle.com'
  DYNAMIC   – bound to a variable/XPath, e.g. $gv_emails
  EMPTY     – explicitly set to '' or ""
  MISSING   – no PARAM file found at all
"""

import csv
import re
import sys
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

# ════════════════════════════════════════════════════════════════════════════════
# ▼▼  CONFIGURE THIS – point to the folder where you drop your .iar files  ▼▼

IAR_FOLDER = r"C:\Users\PrasadR\OneDrive - RITE\Desktop\OIC_Scripts\integration_backups_20260410_114524"

# True  → print / export only notifications that have at least one HARDCODED field
# False → show all notifications
HARDCODED_ONLY = False

# Output CSV is written alongside this script. Change name if needed.
EXTS_DIR = Path(__file__).parent / "exports"
OUTPUT_CSV = "notifications_audit.csv"

# ▲▲  END OF CONFIGURATION  ▲▲
# ════════════════════════════════════════════════════════════════════════════════


# ── Literal detection ─────────────────────────────────────────────────────────
SINGLE_QUOTE_RE = re.compile(r"^'(.*)'$")
DOUBLE_QUOTE_RE = re.compile(r'^"(.*)"$')


# ── Helpers ───────────────────────────────────────────────────────────────────

def _integration_name(zip_path: str) -> str:
    parts = Path(zip_path).parts
    try:
        idx = list(parts).index("project")
        return parts[idx + 1]
    except (ValueError, IndexError):
        return "UNKNOWN"


def _processor_id(zip_path: str) -> str:
    for p in Path(zip_path).parts:
        if p.startswith("processor_"):
            return p
    return "unknown_processor"


def _resourcegroup_id(zip_path: str) -> str:
    for p in Path(zip_path).parts:
        if p.startswith("resourcegroup_"):
            return p
    return "unknown_resourcegroup"


def _parse_props(content: str) -> dict:
    props = {}
    for line in content.splitlines():
        if " : " in line:
            k, _, v = line.partition(" : ")
            props[k.strip()] = v.strip()
    return props


def _classify(text_expr: str) -> Tuple[str, str]:
    """Return (kind, value)  kind = HARDCODED | DYNAMIC | EMPTY."""
    t = text_expr.strip()
    if not t:
        return "EMPTY", ""
    for pat in (SINGLE_QUOTE_RE, DOUBLE_QUOTE_RE):
        m = pat.match(t)
        if m:
            inner = m.group(1).strip()
            return ("EMPTY" if not inner else "HARDCODED"), inner
    return "DYNAMIC", t


def _body_preview(content: str, max_chars: int = 150) -> str:
    text = re.sub(r"<[^>]+>", " ", content)       # strip HTML tags
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars] + ("…" if len(text) > max_chars else "")


# ── Data model ────────────────────────────────────────────────────────────────

@dataclass
class NotificationRecord:
    iar_file: str
    integration_name: str
    processor_id: str
    resourcegroup_id: str
    from_kind: str = "MISSING"
    from_value: str = ""
    to_kind: str = "MISSING"
    to_value: str = ""
    cc_kind: str = "N/A"
    cc_value: str = ""
    subject_kind: str = "MISSING"
    subject_value: str = ""
    body_preview: str = ""

    @property
    def has_hardcoded(self) -> bool:
        return any(
            k == "HARDCODED"
            for k in (self.from_kind, self.to_kind, self.cc_kind, self.subject_kind)
        )


# ── IAR scanner ───────────────────────────────────────────────────────────────

def scan_iar(iar_path: str) -> List[NotificationRecord]:
    iar_name = Path(iar_path).name

    # Load entire IAR into memory index: lower_path → (orig_path, bytes)
    index: Dict[str, Tuple[str, bytes]] = {}
    try:
        with zipfile.ZipFile(iar_path, "r") as zf:
            for entry in zf.namelist():
                try:
                    data = zf.read(entry)
                except Exception:
                    continue
                index[entry.lower()] = (entry, data)
    except zipfile.BadZipFile:
        print(f"  [WARN] Skipping invalid IAR: {iar_path}")
        return []

    # ── Step 1: find all notification_*.data files → one group per resourcegroup
    # NOTE: zip entries always use forward slashes; avoid Path() for dir
    # operations here to prevent Windows backslash/forward-slash mismatches.
    notif_groups: Dict[str, Dict[str, Tuple[str, bytes]]] = defaultdict(dict)
    for lpath, (orig, data) in index.items():
        fname = lpath.rsplit("/", 1)[-1]          # last segment, forward-slash safe
        parent = lpath.rsplit("/", 1)[0] if "/" in lpath else ""
        if fname.startswith("notification_") and fname.endswith(".data"):
            notif_groups[parent][fname] = (orig, data)

    records: List[NotificationRecord] = []

    for parent_dir, notif_files in notif_groups.items():
        # Derive metadata from any file path in the group
        some_orig = next(iter(notif_files.values()))[0]
        rec = NotificationRecord(
            iar_file=iar_name,
            integration_name=_integration_name(some_orig),
            processor_id=_processor_id(some_orig),
            resourcegroup_id=_resourcegroup_id(some_orig),
        )

        # ── Body preview ──────────────────────────────────────────────────────
        body_entry = notif_files.get("notification_body.data")
        if body_entry:
            try:
                rec.body_preview = _body_preview(
                    body_entry[1].decode("utf-8", errors="replace")
                )
            except Exception:
                pass

        # ── Find sibling PARAM expr.properties under the same processor ───────
        # notification_*.data  → .../processor_XXX/resourcegroup_YYY/
        # PARAM files          → .../processor_XXX/resourcegroup_ZZZ/
        # Use forward-slash rsplit to avoid Windows Path() backslash issues.
        processor_dir = parent_dir.rsplit("/", 1)[0] if "/" in parent_dir else parent_dir
        processor_prefix = processor_dir + "/"

        param_map: Dict[str, dict] = {}   # FROM | TO | CC | SUBJECT → props

        for lpath, (orig, data) in index.items():
            if not lpath.startswith(processor_prefix):
                continue
            fname = lpath.rsplit("/", 1)[-1]
            fname_up = fname.upper()
            if "PARAM" not in fname_up or not fname.endswith("expr.properties"):
                continue
            try:
                content = data.decode("utf-8", errors="replace")
            except Exception:
                continue
            props = _parse_props(content)
            if "FROM_PARAM" in fname_up:
                param_map["FROM"] = props
            elif "TO_PARAM" in fname_up:
                param_map["TO"] = props
            elif "CC_PARAM" in fname_up:
                param_map["CC"] = props
            elif "SUBJECT_PARAM" in fname_up:
                param_map["SUBJECT"] = props

        # ── Fallback: read notification_*.data directly if still MISSING ──────
        # Some IARs store the value directly in the .data file rather than
        # via a data-stitched {PARAM} reference + separate expr.properties.
        def _data_file_value(fname: str) -> Tuple[str, str]:
            entry = notif_files.get(fname)
            if not entry:
                return "MISSING", ""
            raw = entry[1].decode("utf-8", errors="replace").strip()
            # Skip pure data-stitch placeholders like {FROM_PARAM_1}
            if re.fullmatch(r"\{[^}]+\}", raw):
                return "MISSING", ""
            return _classify(raw)

        if "FROM" not in param_map:
            kind, val = _data_file_value("notification_from.data")
            if kind != "MISSING":
                param_map["FROM"] = {"TextExpression": f"'{val}'" if kind == "HARDCODED" else val}
        if "TO" not in param_map:
            kind, val = _data_file_value("notification_to.data")
            if kind != "MISSING":
                param_map["TO"] = {"TextExpression": f"'{val}'" if kind == "HARDCODED" else val}
        if "SUBJECT" not in param_map:
            kind, val = _data_file_value("notification_subject.data")
            if kind != "MISSING":
                param_map["SUBJECT"] = {"TextExpression": f"'{val}'" if kind == "HARDCODED" else val}
        if "CC" not in param_map:
            kind, val = _data_file_value("notification_cc.data")
            if kind != "MISSING":
                param_map["CC"] = {"TextExpression": f"'{val}'" if kind == "HARDCODED" else val}

        # ── Populate record fields ────────────────────────────────────────────
        def _fill(key: str) -> Tuple[str, str]:
            if key not in param_map:
                return "MISSING", ""
            return _classify(param_map[key].get("TextExpression", ""))

        rec.from_kind,    rec.from_value    = _fill("FROM")
        rec.to_kind,      rec.to_value      = _fill("TO")
        rec.subject_kind, rec.subject_value = _fill("SUBJECT")

        if "CC" in param_map:
            rec.cc_kind, rec.cc_value = _fill("CC")

        records.append(rec)

    return records


# ── Console output ────────────────────────────────────────────────────────────

def print_console(records: List[NotificationRecord]):
    shown = [r for r in records if not HARDCODED_ONLY or r.has_hardcoded]

    if not shown:
        msg = "No notifications with hardcoded values found." if HARDCODED_ONLY \
              else "No notification activities found."
        print(f"\n✅  {msg}\n")
        return

    print(f"\n{'='*90}")
    print(f"  NOTIFICATION DETAILS  –  {len(shown)} notification(s)")
    print("=" * 90)

    for r in shown:
        flag = "  ⚠️  HARDCODED VALUE(S) PRESENT" if r.has_hardcoded else ""
        print(f"\n  IAR File        : {r.iar_file}")
        print(f"  Integration     : {r.integration_name}")
        print(f"  Processor       : {r.processor_id}  /  {r.resourcegroup_id}{flag}")
        print(f"  FROM            : [{r.from_kind:9}]  {r.from_value}")
        print(f"  TO              : [{r.to_kind:9}]  {r.to_value}")
        if r.cc_kind != "N/A":
            print(f"  CC              : [{r.cc_kind:9}]  {r.cc_value}")
        print(f"  SUBJECT         : [{r.subject_kind:9}]  {r.subject_value}")
        if r.body_preview:
            print(f"  Body (preview)  : {r.body_preview}")
        print()


def write_csv(records: List[NotificationRecord], csv_path: Path):
    fieldnames = [
        "iar_file", "integration_name", "processor_id", "resourcegroup_id",
        "from_kind", "from_value",
        "to_kind",   "to_value",
        "cc_kind",   "cc_value",
        "subject_kind", "subject_value",
        "has_hardcoded", "body_preview",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for r in records:
            w.writerow({
                "iar_file":         r.iar_file,
                "integration_name": r.integration_name,
                "processor_id":     r.processor_id,
                "resourcegroup_id": r.resourcegroup_id,
                "from_kind":        r.from_kind,
                "from_value":       r.from_value,
                "to_kind":          r.to_kind,
                "to_value":         r.to_value,
                "cc_kind":          r.cc_kind,
                "cc_value":         r.cc_value,
                "subject_kind":     r.subject_kind,
                "subject_value":    r.subject_value,
                "has_hardcoded":    r.has_hardcoded,
                "body_preview":     r.body_preview,
            })
    print(f"\n📄  CSV written → {csv_path}  ({len(records)} rows)")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    folder = Path(IAR_FOLDER)
    if not folder.exists():
        print(f"\n❌  IAR folder not found: {folder}")
        print("    → Edit the IAR_FOLDER variable at the top of this script.")
        sys.exit(1)

    iar_files = sorted(folder.rglob("*.iar"))
    if not iar_files:
        print(f"\n⚠️   No .iar files found in: {folder}")
        sys.exit(0)

    filter_mode = "hardcoded only" if HARDCODED_ONLY else "all notifications"
    print(f"\n{'='*80}")
    print(f"  OIC IAR NOTIFICATION SCANNER")
    print(f"  Folder : {folder}")
    print(f"  Filter : {filter_mode}")
    print(f"  Files  : {len(iar_files)} IAR file(s)")
    print(f"  Run at : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*80}\n")

    all_records: List[NotificationRecord] = []
    for iar in iar_files:
        print(f"  Scanning → {iar.name}")
        recs = scan_iar(str(iar))
        if recs:
            print(f"             └─ {len(recs)} notification(s) found")
        all_records.extend(recs)

    total_hc = sum(1 for r in all_records if r.has_hardcoded)

    print(f"\n{'─'*80}")
    print(f"  SUMMARY")
    print(f"{'─'*80}")
    print(f"  Total notifications found  : {len(all_records)}")
    print(f"  ⚠️   With hardcoded values  : {total_hc}")
    print(f"  ✅  Fully dynamic           : {len(all_records) - total_hc}")
    print(f"{'─'*80}")

    print_console(all_records)

    EXTS_DIR.mkdir(exist_ok=True)
    csv_path = EXTS_DIR / OUTPUT_CSV
    write_csv(all_records, csv_path)
    print(f"\n✅  Done.\n")


if __name__ == "__main__":
    main()
