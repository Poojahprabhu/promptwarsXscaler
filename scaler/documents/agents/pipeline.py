"""
ADK pipeline: Stage 1 (Profiler → Extractor → Analyst) → Stage 2 (RiskDetector ‖ FinancialRisk).

Usage (from Celery task):
    import asyncio
    from scaler.documents.agents.pipeline import run_document_pipeline

    result = asyncio.run(run_document_pipeline(
        file_bytes=b"...",
        document_id="uuid",
        user_id="uuid",
        original_filename="contract.pdf",
    ))
    # result["final_chunks"]    → list of chunk dicts
    # result["document_type"]   → confirmed doc type string
    # result["chunking_strategy"] → strategy used
    # result["risk_findings"]   → list of per-clause risk dicts
    # result["financial_risks"] → list of financial exposure dicts
"""
import logging

from google.adk.agents import ParallelAgent
from google.adk.agents import SequentialAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai.types import Content
from google.genai.types import Part

from .analyst import analyst_chunker
from .extractor import content_extractor
from .financial_risk import financial_risk_agent
from .glossary import glossary_agent
from .profiler import document_profiler
from .risk_detector import risk_detector

logger = logging.getLogger(__name__)

_APP_NAME = "document_processor"

_stage1 = SequentialAgent(
    name="document_processing",
    sub_agents=[document_profiler, content_extractor, analyst_chunker],
)

_stage2 = ParallelAgent(
    name="risk_analysis",
    sub_agents=[risk_detector, financial_risk_agent, glossary_agent],
)

_pipeline = SequentialAgent(
    name="document_pipeline",
    sub_agents=[_stage1, _stage2],
)


async def run_document_pipeline(
    file_bytes: bytes,
    document_id: str,
    user_id: str,
    original_filename: str,
    include_external_references: bool = True,
) -> dict:
    """
    Run the full 3-agent pipeline for a document.

    Returns the final session state dict containing:
      processing_plan, extracted_content, final_chunks,
      document_type, chunking_strategy.
    """
    session_service = InMemorySessionService()
    runner = Runner(
        agent=_pipeline,
        app_name=_APP_NAME,
        session_service=session_service,
    )

    # Seed session state with document data
    initial_state = {
        "file_bytes": file_bytes,
        "document_id": document_id,
        "user_id": user_id,
        "original_filename": original_filename,
        "include_external_references": include_external_references,
    }
    session = await session_service.create_session(
        app_name=_APP_NAME,
        user_id=user_id,
        state=initial_state,
    )

    trigger_message = Content(
        role="user",
        parts=[Part(text=f"Process document '{original_filename}' (id={document_id}).")],
    )

    async for event in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=trigger_message,
    ):
        if event.is_final_response():
            logger.info("Pipeline final response for document %s: %s", document_id, event.content)

    final_session = await session_service.get_session(
        app_name=_APP_NAME,
        user_id=user_id,
        session_id=session.id,
    )
    return dict(final_session.state)
