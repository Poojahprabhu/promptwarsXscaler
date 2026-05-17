"""
Live end-to-end test of the 6-agent document pipeline.

Bypasses Celery + Django ORM: runs the ADK pipeline directly against
SampleContract-Shuttle.pdf and prints per-agent results from session state.

Run inside the django container:
    docker compose -f docker-compose.local.yml exec -T \
        -e GOOGLE_APPLICATION_CREDENTIALS=/app/lexguardxpromptwars-54a4f18b283b.json \
        django python /app/test_agents_e2e.py
"""
import asyncio
import os
import sys
import time
import traceback
import uuid
from pathlib import Path

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
django.setup()

from scaler.documents.agents.pipeline import run_document_pipeline  # noqa: E402

PDF_PATH = Path("/app/SampleContract-Shuttle.pdf")
USER_ID = str(uuid.uuid4())
DOC_ID = str(uuid.uuid4())


def section(title: str) -> None:
    bar = "=" * 78
    print(f"\n{bar}\n{title}\n{bar}", flush=True)


def report_profiler(state: dict) -> bool:
    plan = state.get("processing_plan")
    if not plan:
        print("FAIL: processing_plan missing from state")
        return False
    print(f"OK   processing_plan = {plan}")
    required = {"file_format", "page_count", "requires_ocr", "preliminary_doc_type"}
    missing = required - plan.keys()
    if missing:
        print(f"WARN missing keys in processing_plan: {missing}")
        return False
    return True


def report_extractor(state: dict) -> bool:
    extracted = state.get("extracted_content")
    if not extracted or "pages" not in extracted:
        print("FAIL: extracted_content missing or has no pages")
        return False
    pages = extracted["pages"]
    total_chars = sum(len(p.get("text", "")) for p in pages)
    total_tables = sum(len(p.get("tables", [])) for p in pages)
    total_images = sum(len(p.get("images", [])) for p in pages)
    print(f"OK   pages={len(pages)} chars={total_chars} tables={total_tables} images={total_images}")
    if total_chars == 0:
        print("WARN extracted_content has zero characters — extraction may have failed")
        return False
    # Show first page text preview
    if pages:
        preview = (pages[0].get("text") or "")[:200].replace("\n", " ")
        print(f"     first-page preview: {preview!r}")
    return True


def report_analyst(state: dict) -> bool:
    chunks = state.get("final_chunks")
    if not chunks:
        print("FAIL: final_chunks empty")
        return False
    doc_type = state.get("document_type", "?")
    strat = state.get("chunking_strategy", "?")
    qdrant_count = sum(1 for c in chunks if c.get("qdrant_point_id"))
    print(f"OK   chunks={len(chunks)} document_type={doc_type} strategy={strat} qdrant_upserts={qdrant_count}")
    sample = chunks[0]
    print(f"     sample chunk: id={sample.get('chunk_id')!r} section={sample.get('section')!r} "
          f"pages={sample.get('page_refs')} text_len={len(sample.get('text', ''))}")
    return qdrant_count == len(chunks)


def report_risk_detector(state: dict) -> bool:
    findings = state.get("risk_findings")
    if findings is None:
        print("FAIL: risk_findings missing from state")
        return False
    if not findings:
        print("WARN risk_findings is empty list (expected at least one per chunk)")
        return False
    by_sev: dict[str, int] = {}
    for f in findings:
        by_sev[f.get("severity", "?")] = by_sev.get(f.get("severity", "?"), 0) + 1
    print(f"OK   findings={len(findings)} severity_breakdown={by_sev}")
    sample = findings[0]
    print(f"     sample: clause={sample.get('clause_id')!r} type={sample.get('risk_type')!r} "
          f"sev={sample.get('severity')!r} conf={sample.get('confidence')}")
    return True


def report_financial_risk(state: dict) -> bool:
    risks = state.get("financial_risks")
    if risks is None:
        print("FAIL: financial_risks missing from state")
        return False
    print(f"OK   financial_risks={len(risks)}")
    if risks:
        sample = risks[0]
        print(f"     sample: clause={sample.get('clause_id')!r} "
              f"exposure={(sample.get('financial_exposure') or '')[:120]!r}")
    return True


def report_glossary(state: dict) -> bool:
    entries = state.get("glossary_entries")
    if entries is None:
        print("FAIL: glossary_entries missing from state")
        return False
    print(f"OK   glossary_entries={len(entries)}")
    if entries:
        sample = entries[0]
        refs = sample.get("external_references") or []
        print(f"     sample: term={sample.get('term')!r} "
              f"def={(sample.get('definition') or '')[:120]!r} refs={len(refs)}")
    return True


REPORTERS = [
    ("1. profiler",        report_profiler),
    ("2. extractor",       report_extractor),
    ("3. analyst",         report_analyst),
    ("4. risk_detector",   report_risk_detector),
    ("5. financial_risk",  report_financial_risk),
    ("6. glossary",        report_glossary),
]


async def main() -> int:
    if not PDF_PATH.exists():
        print(f"PDF not found at {PDF_PATH}", file=sys.stderr)
        return 2

    file_bytes = PDF_PATH.read_bytes()
    print(f"Loaded {PDF_PATH.name} ({len(file_bytes):,} bytes)")
    print(f"document_id={DOC_ID}  user_id={USER_ID}")

    section("RUNNING PIPELINE")
    started = time.time()
    try:
        state = await run_document_pipeline(
            file_bytes=file_bytes,
            document_id=DOC_ID,
            user_id=USER_ID,
            original_filename=PDF_PATH.name,
            include_external_references=True,
        )
    except Exception:
        traceback.print_exc()
        return 1
    elapsed = time.time() - started
    print(f"\nPipeline returned in {elapsed:.1f}s. state keys = {sorted(state.keys())}")

    section("PER-AGENT RESULTS")
    results: list[tuple[str, bool]] = []
    for name, reporter in REPORTERS:
        print(f"\n--- {name} ---")
        try:
            ok = reporter(state)
        except Exception:
            traceback.print_exc()
            ok = False
        results.append((name, ok))

    section("SUMMARY")
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    failed = sum(1 for _, ok in results if not ok)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
