import json
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ElderlyCareServer")

# Mock databases
MEDICATIONS = [
    {"name": "Lisinopril", "frequency": "Once daily", "purpose": "High blood pressure", "dosage": "10mg"},
    {"name": "Metformin", "frequency": "Twice daily with meals", "purpose": "Type 2 diabetes", "dosage": "500mg"},
]

CARE_PLAN = [
    {"time": "08:00 AM", "activity": "Morning stretch and walk in the garden"},
    {"time": "01:00 PM", "activity": "Cognitive puzzle or reading"},
    {"time": "06:00 PM", "activity": "Evening light stretching"},
]

WELLNESS_LOGS = []

@mcp.tool()
def get_medication_list() -> str:
    """Retrieve the list of active medications for the user, including dosage and frequency."""
    return json.dumps(MEDICATIONS, indent=2)

@mcp.tool()
def get_care_plan() -> str:
    """Retrieve the daily care plan and physical/cognitive activity guidelines for the user."""
    return json.dumps(CARE_PLAN, indent=2)

@mcp.tool()
def log_wellness_metric(metric_name: str, value: str, notes: str = "") -> str:
    """Log a wellness metric (e.g., blood pressure, glucose, weight) with a value and optional notes.
    
    Args:
        metric_name: The name of the vital metric (e.g. 'blood pressure', 'blood glucose').
        value: The logged reading value (e.g. '120/80', '95 mg/dL').
        notes: Optional comments or details.
    """
    entry = {
        "metric_name": metric_name,
        "value": value,
        "notes": notes
    }
    WELLNESS_LOGS.append(entry)
    return f"Successfully logged {metric_name}: {value}."

if __name__ == "__main__":
    mcp.run()
