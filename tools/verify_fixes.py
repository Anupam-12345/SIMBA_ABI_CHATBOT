"""
Runtime verification for the SOP chatbot accuracy fixes.

Run from the project root, with the same interpreter/venv the Flask app uses:

    python tools/verify_fixes.py

This imports the live modules and exercises them. It does not read source text,
so it cannot be fooled by edits made to commented-out copies of a function
(build_index.py contains 4 copies of heading_level; app.py contains 3 copies of
build_smart_fallback).

Exit code 0 = everything applied. Non-zero = number of failed checks.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

PASS, FAIL, INFO = "PASS", "FAIL", "    "
failures = []


def check(name, condition, detail=""):
    status = PASS if condition else FAIL
    print(f"[{status}] {name}" + (f"  -> {detail}" if detail else ""))
    if not condition:
        failures.append(name)
    return condition


def section(title):
    print(f"\n--- {title} " + "-" * max(0, 58 - len(title)))


# ---------------------------------------------------------------- environment
section("Which files are actually being imported")
try:
    import config
    print(f"{INFO}config.py          {config.__file__}")
    print(f"{INFO}VECTORSTORE_DIR    {os.path.abspath(config.VECTORSTORE_DIR)}")
    print(f"{INFO}DOCS_DIR           {os.path.abspath(getattr(config, 'DOCS_DIR', 'docs'))}")
except Exception as exc:
    print(f"[{FAIL}] cannot import config: {exc}")
    sys.exit(99)

print(f"{INFO}python             {sys.executable}")
print(f"{INFO}cwd                {os.getcwd()}")
print(INFO + "If any path above is not the project you are editing, that is the bug.")


# ------------------------------------------------------------------- config
section("config.py values")
check("PARENT_TOPIC_LEVEL == 3", getattr(config, "PARENT_TOPIC_LEVEL", None) == 3,
      f"got {getattr(config, 'PARENT_TOPIC_LEVEL', 'MISSING')}")
check("MAX_PARENT_INLINE_CHARS present", hasattr(config, "MAX_PARENT_INLINE_CHARS"),
      str(getattr(config, "MAX_PARENT_INLINE_CHARS", "MISSING")))
check("MIN_ANSWER_CONFIDENCE present", hasattr(config, "MIN_ANSWER_CONFIDENCE"),
      str(getattr(config, "MIN_ANSWER_CONFIDENCE", "MISSING")))
check("DOCUMENT_ALIASES populated", bool(getattr(config, "DOCUMENT_ALIASES", None)))
check("MAX_RESPONSE_TOKENS lowered", getattr(config, "MAX_RESPONSE_TOKENS", 6000) <= 2500,
      str(getattr(config, "MAX_RESPONSE_TOKENS", "MISSING")))


# ------------------------------------------------------- heading_level (live)
section("ingestion/build_index.py -> heading_level (the LIVE definition)")
try:
    from ingestion import build_index as BI

    print(f"{INFO}module file        {BI.__file__}")

    try:
        BI.heading_level("test", "Normal", trust_style=True, is_bold=True)
        has_is_bold = True
    except TypeError:
        has_is_bold = False
    check("heading_level accepts is_bold=", has_is_bold,
          "you may have edited a commented-out copy" if not has_is_bold else "")

    if has_is_bold:
        cases = [
            ("O/S 1234567-89", True, 0, "bold example value must not be a heading"),
            ("- DMRS", True, 0, "bullet must not be a heading"),
            ("• IEHP", True, 0, "bullet must not be a heading"),
            ("D: 630-285-4037", True, 0, "phone number must not be a heading"),
            ("CD", True, 0, "too short / not word-like"),
            ("Step 9 - Apply the Final Status", True, 4, "in-section label = level 4"),
            ("Required Documents", True, 4, "in-section label = level 4"),
            ("2.10 Offsite Processing", True, 3, "numbered subsection = level 3"),
            ("20. ROI'S", True, 2, "numbered section = level 2"),
            ("**20. ROI'S**", False, 2, "markdown-bold numbered section"),
            ("### **WEST REGION **", False, 1, "markdown document title"),
            ("26. Open a new email to confirm your signature", False, 0, "procedure step"),
            ("Purpose", True, 0, "field label"),
        ]
        for text, bold, expected, why in cases:
            got = BI.heading_level(text, "Normal", trust_style=True, is_bold=bold)
            check(f"heading_level({text[:34]!r}) == {expected}", got == expected,
                  f"got {got} — {why}")

    check("normalize_heading_text strips markers",
          hasattr(BI, "normalize_heading_text")
          and BI.normalize_heading_text("### **WEST REGION **")[0] == "WEST REGION")
    check("is_word_like present", hasattr(BI, "is_word_like"))
    check("SYMBOL_BULLET_RE present", hasattr(BI, "SYMBOL_BULLET_RE"))

except Exception as exc:
    check("import ingestion.build_index", False, repr(exc))


# ------------------------------------------------------------------- app.py
section("app.py")
try:
    import app as APP

    print(f"{INFO}module file        {APP.__file__}")

    check("strip_context_artifacts exists", hasattr(APP, "strip_context_artifacts"))
    if hasattr(APP, "strip_context_artifacts"):
        dirty = "=== DOCUMENT 1: West_FAQ ===\nSection: 2.6 VTC\nPurpose\nUse VTC."
        check("strip_context_artifacts removes scaffolding",
              "=== DOCUMENT" not in APP.strip_context_artifacts(dirty)
              and "Purpose" in APP.strip_context_artifacts(dirty))

    check("answer_is_complete exists", hasattr(APP, "answer_is_complete"))

    if hasattr(APP, "fix_llm_hallucinations"):
        got = APP.fix_llm_hallucinations(
            "Use Solcom - Retrieval Note and Solcom Retrieval Note.", "")
        check("fix_llm_hallucinations does not double 'Solcom'",
              "Solcom - Solcom" not in got and "Solcom – Solcom" not in got, got)

except Exception as exc:
    check("import app", False, repr(exc))


# --------------------------------------------------------------- index state
section("vectorstore state (was the reindex actually written?)")
vs = config.VECTORSTORE_DIR
parent_path = os.path.join(vs, "docstore.json").replace("docstore", "parentstore")
doc_path = os.path.join(vs, "docstore.json")

if not os.path.exists(parent_path):
    check("parentstore.json exists", False, parent_path)
else:
    age_h = (time.time() - os.path.getmtime(parent_path)) / 3600
    print(f"{INFO}parentstore.json last written {age_h:.1f} hours ago")

    try:
        src_mtime = max(
            os.path.getmtime(p) for p in ["ingestion/build_index.py", "config.py"]
            if os.path.exists(p)
        )
        fresh = os.path.getmtime(parent_path) > src_mtime
        check("index is NEWER than your source edits", fresh,
              "" if fresh else "reindex predates your edits — run: python -m ingestion.build_index")
    except ValueError:
        pass

    parents = json.load(open(parent_path, encoding="utf-8"))
    docs = json.load(open(doc_path, encoding="utf-8")) if os.path.exists(doc_path) else []
    print(f"{INFO}{len(parents)} parent topics, {len(docs)} child chunks")

    bad_markers = [p["topic_path"] for p in parents
                   if "###" in p["topic_path"] or "**" in p["topic_path"]]
    check("no markdown markers in topic paths", not bad_markers,
          f"{len(bad_markers)} found, e.g. {bad_markers[:2]}")

    bad_topics = [p["topic_path"] for p in parents
                  if p["topic_path"].split(" > ")[-1].strip()
                  in ("- DMRS", "O/S 1234567-89", "- MOD", "• IEHP", "CD",
                      "AT&T", "D: 630-285-4037", "- MRO", "- VRC")]
    check("no bullet/code fragments promoted to topics", not bad_topics,
          f"{len(bad_topics)} found, e.g. {bad_topics[:2]}")

    def find(needle):
        return [p for p in parents if needle.lower() in p["topic_path"].lower()]

    offsite = find("2.10 Offsite Processing")
    if check("topic '2.10 Offsite Processing' exists", bool(offsite)):
        text = offsite[0]["text"]
        check("  ...contains Step 9", "Step 9" in text, f"topic is {len(text)} chars")
        check("  ...contains 'Sent – Offsite Review'",
              "Sent – Offsite Review" in text or "Sent - Offsite Review" in text)
        check("  ...contains 'File Naming Convention'", "File Naming Convention" in text)

    roi = [p for p in parents if p["topic_path"].split(" > ")[-1].strip()
           in ("20. ROI'S", "20. ROI’S")]
    if check("topic '20. ROI'S' exists", bool(roi)):
        check("  ...contains DMRS (full list)", "DMRS" in roi[0]["text"],
              f"topic is {len(roi[0]['text'])} chars (expect ~2182)")

    vtc = find("2.6 VTC")
    if check("topic '2.6 VTC – Copying Records' exists", bool(vtc)):
        check("  ...contains 'Image Requirement'", "Image Requirement" in vtc[0]["text"])


# ------------------------------------------------------------------ summary
section("Summary")
if failures:
    print(f"{len(failures)} check(s) FAILED:\n")
    for name in failures:
        print(f"  - {name}")
    print("\nFix these top-down: config values first, then heading_level, then")
    print("app.py helpers, then rebuild the index and restart Flask.")
else:
    print("All checks passed. If answers are still incomplete, the remaining")
    print("cause is generation, not ingestion — check the verbatim path in chat().")

sys.exit(len(failures))
