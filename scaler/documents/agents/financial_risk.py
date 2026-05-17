"""
Agent — FinancialRisk (Agent 5 from spec)

Responsibilities:
  - Extract and evaluate financial obligations, fees, and penalties per clause
  - Compute worst-case cost exposure for the user

Session state read:   final_chunks
Session state written: financial_risks: [{
    clause_id, financial_exposure, conditions, worst_case_cost
}]
"""
import json
import logging

from django.conf import settings
from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools import ToolContext

logger = logging.getLogger(__name__)


def get_document_chunks(tool_context: ToolContext) -> dict:
    """Retrieve all document chunks for financial risk analysis."""
    final_chunks: list[dict] = tool_context.state.get("final_chunks", [])
    return {
        "chunk_count": len(final_chunks),
        "chunks": [
            {
                "chunk_id": c.get("chunk_id"),
                "section": c.get("section"),
                "text": c.get("text"),
                "has_table": c.get("has_table", False),
                "metadata": c.get("metadata", {}),
            }
            for c in final_chunks
        ],
    }


def store_financial_risks(financial_risks_json: str, tool_context: ToolContext) -> dict:
    """
    Persist financial risk findings to session state.

    Args:
        financial_risks_json: JSON array of financial risk objects:
            [{
              "clause_id": "<chunk_id>",
              "financial_exposure": "<what money/value is at stake>",
              "conditions": "<under what conditions this triggers>",
              "worst_case_cost": "<maximum possible financial impact to the user>"
            }, ...]
    """
    try:
        risks: list[dict] = json.loads(financial_risks_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"financial_risks_json is not valid JSON: {exc}") from exc

    tool_context.state["financial_risks"] = risks
    logger.info("Financial risks stored: %d findings", len(risks))
    return {"risks_stored": len(risks)}


_INSTRUCTION = """
You are a financial risk analyst specializing in contract review. Your job is to identify every
clause that creates a financial obligation, penalty, fee, or exposure for the user — and to
frame the worst-case cost in plain, quantified terms.

STEP 1: Call get_document_chunks() to retrieve all clauses.

STEP 2: Scan for clauses containing any of:
- Monetary amounts, fees, penalties, fines (explicit or implied)
- Payment schedules, late fees, interest rates
- Liquidated damages, indemnification obligations
- Auto-renewal pricing, cancellation or early-termination fees
- Revenue sharing, royalties, commissions owed to the other party
- Expense reimbursement the user must pay
- Deposit requirements, security bonds, holdbacks
- Clawback provisions, repayment of signing bonus or relocation
- Bonus or compensation that can be forfeited under conditions
- Currency risk or FX-denominated obligations

Skip clauses with no financial content (procedural, definitional, governing law, etc.).

STEP 3: For each financially relevant clause produce:
{
  "clause_id": "<chunk_id from the chunks>",
  "financial_exposure": "<specific description of what money or value is at stake>",
  "conditions": "<precise conditions under which this financial obligation triggers>",
  "worst_case_cost": "<the maximum realistic financial impact to the user in plain language>"
}

STEP 4: Call store_financial_risks(financial_risks_json=<JSON array>) with only the
financially relevant clauses. Do NOT include clauses with zero financial content.

Return a one-line summary, e.g.:
"Financial analysis complete: 5 clauses with financial exposure identified."
"""

financial_risk_agent = LlmAgent(
    name="financial_risk",
    model=LiteLlm(
        model=f"mistral/{settings.MISTRAL_FLASH_MODEL}",
        max_tokens=settings.LLM_MAX_TOKENS,
    ),
    tools=[get_document_chunks, store_financial_risks],
    output_key="financial_risk_output",
    instruction=_INSTRUCTION,
)
