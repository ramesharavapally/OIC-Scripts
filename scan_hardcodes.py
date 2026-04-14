#!/usr/bin/env python3
"""
OIC IAR Scanner – Script 1: Hardcoded Values in Assign Variables & Mapper Files
================================================================================
DROP YOUR .iar FILES INTO THE FOLDER CONFIGURED BELOW, THEN JUST RUN:

    python scan_hardcodes.py

No arguments needed. Results are printed to console AND written to a CSV
in the same folder as this script.

What it detects:
  1. REPORT_ASSIGN  – Assign variable (expr.properties) whose value is a
                      hardcoded BIP/BI Publisher report path:
                        • starts with /Custom or /custom
                        • ends with .xdo
  2. ASSIGN_LITERAL – Assign variable whose value is any other hardcoded
                      string literal (paths, labels, zip names, etc.)
  3. REPORT_MAPPER  – XSL mapper file that contains a hardcoded report
                      path string inline inside the XSLT.
"""

import csv
import json
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional

# ════════════════════════════════════════════════════════════════════════════════
# ▼▼  CONFIGURE THIS – point to the folder where you drop your .iar files  ▼▼

IAR_FOLDER = r"C:\Users\PrasadR\OneDrive - RITE\Desktop\OIC_Scripts\integration_backups_20260410_114524"                                      

# True  → only flag hardcoded BIP report paths (/Custom… or …xdo)
# False → also flag ALL other string-literal assigns (SFTP paths, labels…)
REPORTS_ONLY = False

# Output CSV is written alongside this script. Change name if needed.
EXTS_DIR = Path(__file__).parent / "exports"
OUTPUT_CSV = "hardcodes_audit.csv"

# ▲▲  END OF CONFIGURATION  ▲▲
# ════════════════════════════════════════════════════════════════════════════════


# ── Detection patterns ───────────────────────────────────────────────────────
LITERAL_RE        = re.compile(r"^'(.+)'$")
DOUBLE_LITERAL_RE = re.compile(r'^"(.*)"$')      # stitch.json XPath literals use double quotes
REPORT_PATH_RE    = re.compile(r"(?i)(^/custom|\.xdo$)")
XSL_REPORT_RE     = re.compile(r"""(?i)['"](/custom[^'"]*|[^'"]*\.xdo)['"]""")

# Trivial values skipped in ASSIGN_LITERAL mode (counter inits, boolean flags)
SKIP_TRIVIALS = {"0", "1", "2", "3", "4", "5", "true", "false", "yes", "no", "", " "}


@dataclass
class Finding:
    iar_file: str
    integration_name: str
    file_inside_iar: str
    category: str          # REPORT_ASSIGN | ASSIGN_LITERAL | REPORT_MAPPER
    variable_name: str
    hardcoded_value: str
    expression_type: str   # TextExpression | XSL inline
    context: str = ""


# ── Helpers ──────────────────────────────────────────────────────────────────

def _integration_name(zip_path: str) -> str:
    parts = Path(zip_path).parts
    try:
        idx = list(parts).index("project")
        return parts[idx + 1]
    except (ValueError, IndexError):
        return "UNKNOWN"


def _parse_props(content: str) -> dict:
    props = {}
    for line in content.splitlines():
        if " : " in line:
            k, _, v = line.partition(" : ")
            props[k.strip()] = v.strip()
    return props


def _literal(text_expr: str) -> Optional[str]:
    """Return inner value if TextExpression is a single-quoted literal, else None."""
    m = LITERAL_RE.match(text_expr.strip())
    return m.group(1) if m else None


def _is_report(value: str) -> bool:
    return bool(REPORT_PATH_RE.search(value))


# ── Per-file scanners ────────────────────────────────────────────────────────

def scan_expr(iar_name: str, zip_path: str, content: str) -> List[Finding]:
    # PARAM files belong to notifications → handled by scan_notifications.py
    if "PARAM" in zip_path.upper():
        return []

    props    = _parse_props(content)
    text_expr = props.get("TextExpression", "").strip()
    var_name  = props.get("VariableName", "").strip()
    literal   = _literal(text_expr)

    if literal is None:
        return []  # dynamic XPath – not a hardcode

    integration = _integration_name(zip_path)

    if _is_report(literal):
        return [Finding(
            iar_file=iar_name,
            integration_name=integration,
            file_inside_iar=zip_path,
            category="REPORT_ASSIGN",
            variable_name=var_name,
            hardcoded_value=literal,
            expression_type="TextExpression",
        )]

    if not REPORTS_ONLY and literal.strip() not in SKIP_TRIVIALS:
        # Suppress logger variables and empty variable names
        if not var_name or "logger" in var_name.lower():
            return []
        return [Finding(
            iar_file=iar_name,
            integration_name=integration,
            file_inside_iar=zip_path,
            category="ASSIGN_LITERAL",
            variable_name=var_name,
            hardcoded_value=literal,
            expression_type="TextExpression",
        )]

    return []


def scan_xsl(iar_name: str, zip_path: str, content: str) -> List[Finding]:
    findings = []
    integration = _integration_name(zip_path)
    for lineno, line in enumerate(content.splitlines(), 1):
        for m in XSL_REPORT_RE.finditer(line):
            findings.append(Finding(
                iar_file=iar_name,
                integration_name=integration,
                file_inside_iar=zip_path,
                category="REPORT_MAPPER",
                variable_name="(XSL mapper)",
                hardcoded_value=m.group(1),
                expression_type="XSL inline",
                context=f"Line {lineno}: {line.strip()[:120]}",
            ))
    return findings


def scan_stitch(iar_name: str, zip_path: str, content: str) -> List[Finding]:
    """Scan a stitch.json file for hardcoded literals in Assign activities."""
    findings = []
    try:
        data = json.loads(content)
    except Exception:
        return []

    integration = _integration_name(zip_path)

    for stitch in data.get("stitches", []):
        if stitch.get("@type") != "Assign":
            continue

        from_node = stitch.get("from", {})
        if from_node.get("@type") != "XPathExpression":
            continue

        expression = from_node.get("expression", "").strip()

        # Stitch XPath literals are wrapped in double quotes: "value"
        m = DOUBLE_LITERAL_RE.match(expression)
        if not m:
            continue  # dynamic XPath, not a literal

        literal = m.group(1)
        if literal.strip() in SKIP_TRIVIALS:
            continue

        # Variable name is the 'path' in 'to', e.g. "$gv_emails" → "gv_emails"
        to_node = stitch.get("to", {})
        var_name = to_node.get("path", "").lstrip("$")

        if not var_name or "logger" in var_name.lower():
            continue

        if _is_report(literal):
            category = "REPORT_ASSIGN"
        elif REPORTS_ONLY:
            continue
        else:
            category = "STITCH_ASSIGN"

        findings.append(Finding(
            iar_file=iar_name,
            integration_name=integration,
            file_inside_iar=zip_path,
            category=category,
            variable_name=var_name,
            hardcoded_value=literal,
            expression_type="Data Stitch (XPathExpression)",
        ))

    return findings


# ── IAR scanner ──────────────────────────────────────────────────────────────

def scan_iar(iar_path: str) -> List[Finding]:
    findings = []
    iar_name = Path(iar_path).name
    try:
        with zipfile.ZipFile(iar_path, "r") as zf:
            for entry in zf.namelist():
                try:
                    raw = zf.read(entry)
                except Exception:
                    continue
                lentry = entry.lower()
                try:
                    content = raw.decode("utf-8", errors="replace")
                except Exception:
                    continue

                if lentry.endswith("expr.properties"):
                    findings.extend(scan_expr(iar_name, entry, content))
                elif lentry.endswith(".xsl"):
                    findings.extend(scan_xsl(iar_name, entry, content))
                elif lentry.endswith("stitch.json"):
                    findings.extend(scan_stitch(iar_name, entry, content))

    except zipfile.BadZipFile:
        print(f"  [WARN] Skipping invalid IAR: {iar_path}")
    return findings


# ── Console output ───────────────────────────────────────────────────────────

CATEGORY_LABELS = {
    "REPORT_ASSIGN":  "🔴  REPORT PATH in Assign Variable (expr.properties)",
    "ASSIGN_LITERAL": "🟡  Hardcoded String Literal in Assign Variable",
    "REPORT_MAPPER":  "🔴  REPORT PATH in Mapper / XSL File",
    "STITCH_ASSIGN":  "🟠  Hardcoded String Literal in Data Stitch Assign",
}


def print_console(findings: List[Finding]):
    if not findings:
        print("\n✅  No hardcoded values found.\n")
        return

    groups: dict = {}
    for f in findings:
        groups.setdefault(f.category, []).append(f)

    for cat, label in CATEGORY_LABELS.items():
        items = groups.get(cat, [])
        if not items:
            continue
        print(f"\n{'='*80}")
        print(f"{label}  [{len(items)} finding(s)]")
        print("=" * 80)
        for f in items:
            print(f"  IAR File        : {f.iar_file}")
            print(f"  Integration     : {f.integration_name}")
            print(f"  Internal File   : {f.file_inside_iar}")
            print(f"  Variable Name   : {f.variable_name or '(none)'}")
            print(f"  Hardcoded Value : {f.hardcoded_value}")
            if f.context:
                print(f"  Context         : {f.context}")
            print()


def write_csv(findings: List[Finding], csv_path: Path):
    fieldnames = [
        "category", "iar_file", "integration_name",
        "file_inside_iar", "variable_name",
        "hardcoded_value", "expression_type", "context",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames)
        w.writeheader()
        for f in findings:
            w.writerow({
                "category":         f.category,
                "iar_file":         f.iar_file,
                "integration_name": f.integration_name,
                "file_inside_iar":  f.file_inside_iar,
                "variable_name":    f.variable_name,
                "hardcoded_value":  f.hardcoded_value,
                "expression_type":  f.expression_type,
                "context":          f.context,
            })
    print(f"\n📄  CSV written → {csv_path}  ({len(findings)} rows)")


# ── Main ─────────────────────────────────────────────────────────────────────

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

    mode = "report paths only" if REPORTS_ONLY else "all hardcoded literals"
    print(f"\n{'='*80}")
    print(f"  OIC IAR HARDCODE SCANNER")
    print(f"  Folder : {folder}")
    print(f"  Mode   : {mode}")
    print(f"  Files  : {len(iar_files)} IAR file(s)")
    print(f"  Run at : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*80}\n")

    all_findings: List[Finding] = []
    for iar in iar_files:
        print(f"  Scanning → {iar.name}")
        found = scan_iar(str(iar))
        if found:
            print(f"             └─ {len(found)} finding(s)")
        all_findings.extend(found)

    # Summary
    report_count  = sum(1 for f in all_findings if f.category == "REPORT_ASSIGN")
    literal_count = sum(1 for f in all_findings if f.category == "ASSIGN_LITERAL")
    mapper_count  = sum(1 for f in all_findings if f.category == "REPORT_MAPPER")
    stitch_count  = sum(1 for f in all_findings if f.category == "STITCH_ASSIGN")

    print(f"\n{'─'*80}")
    print(f"  SUMMARY")
    print(f"{'─'*80}")
    print(f"  Total findings             : {len(all_findings)}")
    print(f"  🔴  Report paths (assign)  : {report_count}")
    print(f"  🟡  Other literals (assign): {literal_count}")
    print(f"  🔴  Report paths (mapper)  : {mapper_count}")
    print(f"  🟠  Data stitch literals   : {stitch_count}")
    print(f"{'─'*80}")

    print_console(all_findings)

    EXTS_DIR.mkdir(exist_ok=True)
    csv_path = EXTS_DIR / OUTPUT_CSV
    write_csv(all_findings, csv_path)
    print(f"\n✅  Done.\n")


if __name__ == "__main__":
    main()
