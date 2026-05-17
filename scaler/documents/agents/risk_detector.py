"""
Agent — RiskDetector (merges Agent 3 + Agent 4 from spec)

Responsibilities:
  - Analyze each clause for known risk patterns (adversarial legal review)
  - Flag vague, undefined, or subjectively interpreted terms (ambiguity)
  - Produce per-clause risk findings with severity, confidence, explanation, action

Session state read:   final_chunks
Session state written: risk_findings: [{
    clause_id, risk_type, severity, confidence,
    raw_explanation, action, ambiguous_terms, risk_of_interpretation
}]
"""
import json
import logging

from django.conf import settings
from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools import ToolContext

logger = logging.getLogger(__name__)

_VALID_SEVERITIES = frozenset({"critical", "high", "medium", "low", "safe"})


def get_document_chunks(tool_context: ToolContext) -> dict:
    """Retrieve all document chunks for risk analysis."""
    final_chunks: list[dict] = tool_context.state.get("final_chunks", [])
    return {
        "chunk_count": len(final_chunks),
        "chunks": [
            {
                "chunk_id": c.get("chunk_id"),
                "section": c.get("section"),
                "text": c.get("text"),
                "has_table": c.get("has_table", False),
                "has_signature": c.get("has_signature", False),
                "metadata": c.get("metadata", {}),
            }
            for c in final_chunks
        ],
    }


def store_risk_findings(risk_findings_json: str, tool_context: ToolContext) -> dict:
    """
    Persist risk findings for all analyzed clauses to session state.

    Args:
        risk_findings_json: JSON array of risk finding objects:
            [{
              "clause_id": "<chunk_id>",
              "risk_type": "<label>",
              "severity": "critical|high|medium|low|safe",
              "confidence": 0.0-1.0,
              "raw_explanation": "<plain-English explanation>",
              "action": "<what the user should do or negotiate>",
              "ambiguous_terms": ["<term1>", ...],
              "risk_of_interpretation": "<how the other party could exploit ambiguity>"
            }, ...]
    """
    try:
        findings: list[dict] = json.loads(risk_findings_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"risk_findings_json is not valid JSON: {exc}") from exc

    for finding in findings:
        if finding.get("severity") not in _VALID_SEVERITIES:
            finding["severity"] = "medium"
        finding["confidence"] = max(0.0, min(1.0, float(finding.get("confidence", 0.5))))
        if not isinstance(finding.get("ambiguous_terms"), list):
            finding["ambiguous_terms"] = []

    tool_context.state["risk_findings"] = findings
    logger.info("Risk findings stored: %d findings", len(findings))
    return {"findings_stored": len(findings)}


_INSTRUCTION = """
You are an adversarial legal reviewer. Your job is to protect the user by finding every clause
that could harm them, expose them to liability, or contain unfair, vague, or one-sided terms.

STEP 1: Call get_document_chunks() to retrieve all clauses.

STEP 2: For EVERY clause, analyze it for the following:

RISK PATTERNS to flag (severity: critical or high):
- Unilateral rights: company can change terms, cancel, or act at "sole discretion"
- Broad IP assignment: work created outside hours, personal projects, pre-existing IP
- Non-compete / non-solicitation: overly broad scope, geography, duration
- Mandatory arbitration or class-action waivers
- Indemnification obligations placed on the user
- Penalty clauses, liquidated damages, interest on late payments
- Auto-renewal traps with punitive cancellation terms
- Limitation of liability that heavily favors the other party
- One-sided amendment rights ("we may update these terms at any time")
- Data or privacy rights assignment without compensation

AMBIGUITY to flag (severity: medium or low):
- Hedging language: "reasonable efforts", "best efforts", "commercially reasonable", "may"
- Undefined terms referenced throughout the document
- "May" vs "shall" inconsistency that creates optional obligations
- Vague duration, scope, or geography

SEVERITY GUIDE:
- critical: User could suffer major financial loss, career damage, or lose fundamental rights
- high: Significant one-sided terms with real legal exposure
- medium: Noteworthy risk, common in contracts but worth flagging
- low: Minor concern, slight edge to the other party but normal boilerplate
- safe: Balanced, standard, nothing unusual

STEP 3: For EVERY clause (including safe ones), produce a JSON object:
{
  "clause_id": "<chunk_id from the chunks>",
  "risk_type": "<short snake_case label, e.g.: broad_ip_assignment, mandatory_arbitration, safe>",
  "severity": "<critical|high|medium|low|safe>",
  "confidence": <0.0-1.0 float>,
  "raw_explanation": "<2-4 sentences in plain English explaining the specific risk>",
  "action": "<specific action the user should take, or null if severity is safe>",
  "ambiguous_terms": ["<term1>", "<term2>"],
  "risk_of_interpretation": "<how the other party could exploit vagueness, or empty string>"
}

STEP 4: Call store_risk_findings(risk_findings_json=<JSON array of ALL clause objects>).
You MUST include every clause — even ones rated safe — so the full picture is captured.

Return a one-line summary, e.g.:
"Risk analysis complete: 14 clauses — 2 critical, 3 high, 4 medium, 2 low, 3 safe."
"""

risk_detector = LlmAgent(
    name="risk_detector",
    model=LiteLlm(
        model=f"mistral/{settings.MISTRAL_PRO_MODEL}",
        max_tokens=settings.LLM_MAX_TOKENS,
    ),
    tools=[get_document_chunks, store_risk_findings],
    output_key="risk_detector_output",
    instruction=_INSTRUCTION,
)
