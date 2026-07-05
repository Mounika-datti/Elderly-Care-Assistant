# ruff: noqa
# Elderly Care Assistant — Multi-Agent ADK Workflow
# -----------------------------------------------------------------------
# Architecture:
#   START → security_checkpoint → orchestrator_agent → caregiver_approval (if needed)
#                               ↓ (violation/emergency) → blocked_response
# -----------------------------------------------------------------------

import json
import logging
import os
import re
import sys

from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.agents.context import Context
from google.adk.events.request_input import RequestInput
from google.adk.tools import agent_tool
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset, StdioConnectionParams
from google.adk.workflow import FunctionNode, Workflow, node, START

load_dotenv()
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "False")

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Model & MCP config
# ─────────────────────────────────────────────────────────────────────────────
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

MCP_SERVER_PARAMS = StdioConnectionParams(
    server_params={
        "command": sys.executable,
        "args": ["-m", "app.mcp_server"],
        "cwd": os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    }
)

# ─────────────────────────────────────────────────────────────────────────────
# Security constants
# ─────────────────────────────────────────────────────────────────────────────
INJECTION_KEYWORDS = [
    "ignore previous instructions",
    "ignore all instructions",
    "system prompt",
    "disregard your instructions",
    "jailbreak",
    "forget all previous",
    "act as if you",
    "pretend you are",
    "bypass",
    "override instructions",
]

EMERGENCY_PHRASES = [
    "chest pain",
    "heart attack",
    "can't breathe",
    "stroke",
    "unconscious",
    "call 911",
    "emergency",
    "severe pain",
]

PII_PATTERNS = [
    (re.compile(r"\b\d{1,3}[A-Z]{2}\d{5}\b"), "[MEDICARE_ID_REDACTED]"),   # Medicare ID
    (re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[PHONE_REDACTED]"),  # Phone numbers
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN_REDACTED]"),                # SSN
]


# ─────────────────────────────────────────────────────────────────────────────
# Agents
# ─────────────────────────────────────────────────────────────────────────────

medication_agent = LlmAgent(
    name="medication_agent",
    model=GEMINI_MODEL,
    description="Handles medication queries, wellness metric logging, and medication safety questions.",
    instruction=(
        "You are a compassionate medication assistant for elderly users. "
        "Use the available MCP tools to: "
        "(1) Retrieve the active medication list when users ask about their medications. "
        "(2) Log wellness metrics (blood pressure, glucose, weight) when users report their vitals. "
        "Always provide clear, senior-friendly explanations. "
        "If the user wants to CHANGE or STOP their medication routine, set requires_approval=True in your response. "
        "Otherwise set requires_approval=False. "
        "Always end your response with a JSON block: {\"requires_approval\": true/false}"
    ),
)

activity_agent = LlmAgent(
    name="activity_agent",
    model=GEMINI_MODEL,
    description="Handles physical activity and care plan queries, flags strenuous activities for caregiver review.",
    instruction=(
        "You are a helpful activity coordinator for elderly users. "
        "For safe, low-intensity activities (light stretching, walking, gardening), set requires_approval=False. "
        "If the user requests STRENUOUS activities (heavy lifting, intense jogging, competitive sports), "
        "flag it for caregiver review by setting requires_approval=True. "
        "Always end your response with a JSON block: {\"requires_approval\": true/false}"
    ),
)

orchestrator_agent = LlmAgent(
    name="orchestrator_agent",
    model=GEMINI_MODEL,
    description="The main coordinator that delegates to medication or activity sub-agents.",
    instruction=(
        "You are the Elderly Care Assistant coordinator. "
        "For MEDICATION questions (drug names, dosages, side effects, vitals logging): delegate to medication_agent. "
        "For ACTIVITY questions (exercises, daily care plan, physical routines): delegate to activity_agent. "
        "After the sub-agent responds, extract the requires_approval flag from their JSON block. "
        "If requires_approval is true, respond with exactly: NEEDS_CAREGIVER_APPROVAL "
        "Otherwise respond naturally with the sub-agent's answer. "
        "Always be warm and patient with the user."
    ),
    tools=[
        agent_tool.AgentTool(agent=medication_agent),
        agent_tool.AgentTool(agent=activity_agent),
    ],
    output_key="orchestrator_output",
)


# ─────────────────────────────────────────────────────────────────────────────
# Workflow function nodes
# ─────────────────────────────────────────────────────────────────────────────

def _redact_pii(text: str) -> str:
    """Redact sensitive PII from a text string."""
    for pattern, replacement in PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _audit_log(severity: str, event_type: str, details: dict) -> None:
    """Emit a structured JSON audit log entry."""
    log_entry = {
        "severity": severity,
        "event_type": event_type,
        **details,
    }
    logger.info("AUDIT: %s", json.dumps(log_entry))


@node()
def security_checkpoint(ctx: Context, node_input: str) -> str:
    """Security checkpoint: PII scrubbing, injection detection, emergency detection."""
    raw_text = node_input if isinstance(node_input, str) else str(node_input)

    # 1. PII Redaction
    clean_text = _redact_pii(raw_text)
    if clean_text != raw_text:
        _audit_log("INFO", "PII_REDACTED", {"original_length": len(raw_text), "clean_length": len(clean_text)})

    lower_text = clean_text.lower()

    # 2. Emergency Detection (highest priority)
    for phrase in EMERGENCY_PHRASES:
        if phrase in lower_text:
            _audit_log("CRITICAL", "EMERGENCY_DETECTED", {"phrase": phrase})
            ctx.state["security_status"] = "EMERGENCY"
            ctx.state["user_message"] = clean_text
            ctx.route = "SECURITY_EVENT"
            return clean_text

    # 3. Prompt Injection Detection
    for keyword in INJECTION_KEYWORDS:
        if keyword in lower_text:
            _audit_log("WARNING", "INJECTION_DETECTED", {"keyword": keyword})
            ctx.state["security_status"] = "INJECTION_BLOCKED"
            ctx.state["user_message"] = clean_text
            ctx.route = "SECURITY_EVENT"
            return clean_text

    # 4. Clear — pass through
    _audit_log("INFO", "SECURITY_CLEAR", {"message_length": len(clean_text)})
    ctx.state["security_status"] = "CLEAR"
    ctx.state["user_message"] = clean_text
    ctx.route = "CLEAR"
    return clean_text


@node()
def blocked_response(ctx: Context) -> str:
    """Return an appropriate blocked message based on security status."""
    status = ctx.state.get("security_status", "BLOCKED")
    if status == "EMERGENCY":
        return (
            "Emergency Detected! If you are experiencing a medical emergency, "
            "please call 911 immediately or ask someone nearby for help. "
            "Your safety is the top priority."
        )
    return (
        "Security Policy Violation: Your request could not be processed "
        "due to a security policy constraint. Please rephrase your request."
    )


@node(rerun_on_resume=True)
def caregiver_approval(ctx: Context, node_input: str) -> str:
    """Pause for caregiver review. Resumes when caregiver provides input."""
    interrupt_id = "caregiver_review"

    if interrupt_id in (ctx.resume_inputs or {}):
        caregiver_decision = ctx.resume_inputs[interrupt_id]
        _audit_log("INFO", "CAREGIVER_RESUMED", {"decision": str(caregiver_decision)})
        return (
            f"Your caregiver has reviewed your request and responded: {caregiver_decision}. "
            "Your care plan has been updated accordingly."
        )

    # First time — request caregiver input
    _audit_log("INFO", "CAREGIVER_APPROVAL_REQUESTED", {})
    yield RequestInput(
        interrupt_id=interrupt_id,
        question=(
            "A caregiver review is required for this request. "
            "I need to check with your caregiver before proceeding. "
            "Please wait while your caregiver is notified."
        ),
    )

elderly_care_workflow = Workflow(
    name="elderly_care_assistant",
    description="Elderly Care Assistant: secure multi-agent care coordinator with HITL caregiver guardrails.",
    edges=[
        # START → security checkpoint (security_checkpoint is a FunctionNode via @node())
        (START, security_checkpoint),
        # Security routes: dict-based conditional routing on ctx.route
        (security_checkpoint, {
            "SECURITY_EVENT": blocked_response,
            "CLEAR": orchestrator_agent,
        }),
        # Orchestrator → caregiver approval if needed
        (orchestrator_agent, {
            "NEEDS_CAREGIVER_APPROVAL": caregiver_approval,
        }),
    ],
)

# Export as root_agent for ADK web to discover
root_agent = elderly_care_workflow
