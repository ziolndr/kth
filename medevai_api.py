"""
MEDEVAC API v2 - Field Hospital Medical Equipment Allocation
FastAPI wrapper for real combat medical protocols
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Optional
import httpx

from doctrine_service_medical import (
    FieldHospitalDoctrine,
    Scenario,
    Casualty,
    Severity,
    MedicalStrategy
)

app = FastAPI(title="MEDEVAC API", version="2.0.0")

# Enable CORS for local development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize doctrine service
doctrine_service = FieldHospitalDoctrine()

# Request/Response models
class CasualtyInput(BaseModel):
    severity: str  # "critical", "urgent", "delayed", "minimal"
    count: int
    injuries: Optional[List[str]] = None

class ScenarioRequest(BaseModel):
    casualties: List[CasualtyInput]
    equipment_inventory: Dict[str, int]
    hours_until_resupply: float
    expected_incoming_casualties: int
    medevac_available: bool = True
    surgical_capability: bool = False

class StrategyResponse(BaseModel):
    name: str
    description: str
    total_cost: float
    estimated_survival_rate: float
    equipment_preserved: Dict[str, int]
    rationale: str
    coherence_score: float

@app.get("/")
def root():
    return {
        "service": "MEDEVAC Field Hospital Protocol API",
        "version": "2.0.0",
        "status": "operational",
        "protocols": [
            "MARCH Immediate Intervention",
            "Golden Hour Stabilization",
            "Prolonged Field Care (Swedish/NATO)",
            "NATO Role 2 Surgical",
            "Mass Casualty Protocol",
            "Siege Medicine"
        ]
    }

@app.post("/evaluate", response_model=List[StrategyResponse])
async def evaluate_scenario(request: ScenarioRequest):
    """
    Evaluate field hospital scenario using real combat medical protocols
    Returns ARBITER-ranked strategies with geometric coherence scores
    """

    try:
        # Convert request to internal scenario format
        casualties_list = []
        for casualty_input in request.casualties:
            severity = Severity(casualty_input.severity.lower())

            # Generate casualties of this type
            for _ in range(casualty_input.count):
                injuries = casualty_input.injuries or _get_injuries_for_severity(severity)
                survival_prob = _get_survival_probability(severity)
                casualties_list.append(
                    Casualty(severity, injuries, survival_prob)
                )

        scenario = Scenario(
            casualties=casualties_list,
            equipment_inventory=request.equipment_inventory,
            hours_until_resupply=request.hours_until_resupply,
            expected_incoming_casualties=request.expected_incoming_casualties,
            medevac_available=request.medevac_available,
            surgical_capability=request.surgical_capability
        )

        # Generate strategies using real medical protocols
        strategies = doctrine_service.generate_strategies(scenario)

        # Get ARBITER coherence scores
        coherence_scores = await _get_arbiter_coherence(scenario, strategies)

        # Convert to response format with ARBITER scores
        responses = []
        for i, strategy in enumerate(strategies):
            responses.append(StrategyResponse(
                name=strategy.name,
                description=strategy.description,
                total_cost=strategy.total_cost,
                estimated_survival_rate=strategy.estimated_survival_rate,
                equipment_preserved=strategy.equipment_preserved,
                rationale=strategy.rationale,
                coherence_score=coherence_scores.get(i, 0.5)
            ))

        # Sort by ARBITER coherence score (highest first)
        responses.sort(key=lambda x: x.coherence_score, reverse=True)

        return responses

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def _get_arbiter_coherence(scenario: Scenario, strategies: List[MedicalStrategy]) -> Dict[int, float]:
    """
    Call ARBITER API to get geometric coherence scores for medical strategies
    Returns dict mapping strategy index to coherence score
    """

    # Count casualties by severity
    critical_count = sum(1 for c in scenario.casualties if c.severity == Severity.CRITICAL)
    urgent_count = sum(1 for c in scenario.casualties if c.severity == Severity.URGENT)
    delayed_count = sum(1 for c in scenario.casualties if c.severity == Severity.DELAYED)
    minimal_count = sum(1 for c in scenario.casualties if c.severity == Severity.MINIMAL)

    # Build rich contextual query for ARBITER
    query = _build_medical_query(scenario, critical_count, urgent_count, delayed_count, minimal_count)

    # Extract strategy descriptions for ARBITER - CLEAN, no rationales
    candidates = []
    for strategy in strategies:
        # Send ONLY the protocol description
        # Do NOT include rationale (it references query constraints, creates entanglement)
        candidates.append(strategy.description)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.arbiter.traut.ai/v1/compare",
                json={"query": query, "candidates": candidates}
            )
            response.raise_for_status()
            data = response.json()

            # Map results back to strategy indices
            scores = {}
            for i, result in enumerate(data.get("all", [])):
                scores[i] = result.get("score", 0.5)

            return scores

    except Exception as e:
        print(f"ARBITER API error: {e}")
        # Fallback to simulated scores based on scenario constraints
        return _simulate_coherence_fallback(scenario, strategies)

def _build_medical_query(scenario: Scenario, critical: int, urgent: int,
                        delayed: int, minimal: int) -> str:
    """
    Build constraint-only query for ARBITER

    CRITICAL: Describe CONSTRAINTS, not ACTIONS
    - State what exists (casualties, equipment, timeline)
    - State what limits you (resupply delay, incoming surge, capabilities)
    - Do NOT describe what's being done or should be done

    This avoids semantic entanglement where query pre-supposes an answer
    """

    total_patients = len(scenario.casualties)

    # Situation statement - NEUTRAL, no action verbs
    query = f"Swedish field hospital. Current casualty status: "
    query += f"{critical} critical severity, "
    query += f"{urgent} urgent severity, "
    query += f"{delayed} delayed severity, "
    query += f"{minimal} minimal severity. "

    # Equipment constraint - CRITICAL: Calculate against TOTAL load, not just current
    total_equipment = sum(scenario.equipment_inventory.values())
    equipment_per_current = total_equipment / total_patients if total_patients > 0 else 0

    # Calculate total potential patient load INCLUDING incoming surge
    total_potential = total_patients + scenario.expected_incoming_casualties
    equipment_per_total = total_equipment / total_potential if total_potential > 0 else 0

    query += f"Equipment availability: {int(total_equipment)} total items across {len(scenario.equipment_inventory)} categories. "
    query += f"Current patients: {total_patients}. "

    # Surge constraint - state expectation BEFORE discussing ratios
    query += f"Intelligence estimate: {scenario.expected_incoming_casualties} additional casualties expected. "
    query += f"Total potential casualty load: {total_potential} patients ({total_patients} current + {scenario.expected_incoming_casualties} incoming). "

    # CRITICAL CONSTRAINT: Equipment per TOTAL load
    query += f"Equipment-to-patient ratio across total load: {equipment_per_total:.1f} items per patient. "

    # Emphasize resource adequacy relative to TOTAL burden
    if equipment_per_total < 2:
        query += "Catastrophic supply deficit - insufficient equipment exists to provide standard care across total casualty burden. "
    elif equipment_per_total < 5:
        query += "Critical supply shortage - standard treatment protocols will exhaust supplies before completing care for total load. "
    elif equipment_per_total < 10:
        query += "Constrained supply situation - resource allocation must prioritize across total casualty burden. "

    # Time constraint - state the timeline
    query += f"Resupply timeline: {scenario.hours_until_resupply:.1f} hours. "

    # Time pressure assessment
    if scenario.hours_until_resupply < 3:
        time_pressure = "short resupply window"
    elif scenario.hours_until_resupply < 8:
        time_pressure = "moderate resupply delay"
    else:
        time_pressure = "extended resupply delay with high uncertainty"

    query += f"Operational tempo: {time_pressure}. "

    # Capability constraints - state what exists
    capabilities = []
    if scenario.surgical_capability:
        capabilities.append("surgical intervention")
    if scenario.medevac_available:
        capabilities.append("medical evacuation")

    if capabilities:
        query += f"Available capabilities: {', '.join(capabilities)}. "
    else:
        query += "Available capabilities: stabilization only, no surgery or evacuation. "

    # Surge pressure - assess relative to TOTAL load and supplies
    surge_ratio = scenario.expected_incoming_casualties / total_patients if total_patients > 0 else 0

    if surge_ratio >= 1.0 and equipment_per_total < 3:
        surge_assessment = "major casualty surge matching or exceeding current load with insufficient supplies for total burden"
    elif surge_ratio >= 1.0:
        surge_assessment = "major casualty surge matching or exceeding current load"
    elif surge_ratio > 0.5:
        surge_assessment = "significant incoming casualties requiring resource preservation"
    else:
        surge_assessment = "manageable incoming volume"

    query += f"Casualty flow: {surge_assessment}. "

    # Critical temporal constraint - strategies must account for BOTH waves
    if surge_ratio >= 0.5:
        query += f"Constraint: Resource allocation must sustain medical capability across both current patient load and incoming surge. "
        query += f"Strategies that deplete supplies treating current patients will leave facility unable to treat incoming casualties. "

    # Implicit question (never state explicitly)
    # "What resource allocation strategy is geometrically coherent with these constraints?"
    # ARBITER measures coherence of candidates against this constraint geometry

    return query

def _get_injuries_for_severity(severity: Severity) -> List[str]:
    """Map severity to typical combat injuries"""
    injury_map = {
        Severity.CRITICAL: ["massive_hemorrhage", "tension_pneumothorax", "airway_compromise"],
        Severity.URGENT: ["compound_fracture", "abdominal_trauma", "significant_hemorrhage"],
        Severity.DELAYED: ["simple_fracture", "soft_tissue_injury", "minor_hemorrhage"],
        Severity.MINIMAL: ["minor_laceration", "contusion", "minor_burn"]
    }
    return injury_map.get(severity, [])

def _get_survival_probability(severity: Severity) -> float:
    """Base survival probability by severity"""
    prob_map = {
        Severity.CRITICAL: 0.65,
        Severity.URGENT: 0.85,
        Severity.DELAYED: 0.95,
        Severity.MINIMAL: 0.98,
        Severity.EXPECTANT: 0.10
    }
    return prob_map.get(severity, 0.90)

def _simulate_coherence_fallback(scenario: Scenario, strategies: List[MedicalStrategy]) -> Dict[int, float]:
    """
    Fallback coherence simulation if ARBITER API fails
    Uses scenario constraints to estimate coherence
    """
    scores = {}

    for i, strategy in enumerate(strategies):
        # Base score from survival rate
        score = strategy.estimated_survival_rate * 0.4

        # Adjust for equipment preservation vs incoming surge
        preservation_ratio = sum(strategy.equipment_preserved.values()) / sum(scenario.equipment_inventory.values())

        if scenario.expected_incoming_casualties > 20:
            # High incoming surge - reward preservation
            score += preservation_ratio * 0.3
        else:
            # Low incoming surge - reward aggressive treatment
            score += (1 - preservation_ratio) * 0.3

        # Adjust for resupply timeline
        if scenario.hours_until_resupply < 3:
            # Short resupply - can be more aggressive
            if preservation_ratio < 0.4:
                score += 0.2
        elif scenario.hours_until_resupply > 10:
            # Long resupply - need conservation
            if preservation_ratio > 0.6:
                score += 0.2

        # Cost efficiency consideration
        cost_per_survival = strategy.total_cost / (strategy.estimated_survival_rate * len(scenario.casualties))
        if cost_per_survival < 5000:
            score += 0.1

        scores[i] = min(1.0, max(0.0, score))

    return scores

@app.get("/health")
def health_check():
    return {"status": "healthy", "service": "medevac-api", "version": "2.0.0"}

@app.get("/protocols")
def list_protocols():
    """List available medical protocols"""
    return {
        "protocols": [
            {
                "name": "MARCH Immediate Intervention",
                "description": "Massive hemorrhage, Airway, Respiration, Circulation, Hypothermia",
                "use_case": "Immediate trauma response, stop the dying"
            },
            {
                "name": "Golden Hour Stabilization",
                "description": "Stabilize for evacuation within 60 minutes",
                "use_case": "When medevac available, no surgery needed"
            },
            {
                "name": "Prolonged Field Care",
                "description": "Extended treatment in place with monitoring",
                "use_case": "Swedish/NATO protocols for delayed evacuation"
            },
            {
                "name": "NATO Role 2 Surgical",
                "description": "Damage control surgery with full capability",
                "use_case": "When surgical capability available"
            },
            {
                "name": "Mass Casualty Protocol",
                "description": "Expectant triage for overwhelming casualties",
                "use_case": "When casualties exceed capacity"
            },
            {
                "name": "Siege Medicine",
                "description": "Extreme conservation for prolonged isolation",
                "use_case": "No resupply, no evacuation, extended operations"
            }
        ]
    }

if __name__ == "__main__":
    import uvicorn
    print("Starting MEDEVAC API v2.0 on http://localhost:8001")
    print("Documentation available at http://localhost:8001/docs")
    print("")
    print("Real combat medical protocols:")
    print("  - MARCH Immediate Intervention")
    print("  - Golden Hour Stabilization")
    print("  - Prolonged Field Care (Swedish/NATO)")
    print("  - NATO Role 2 Surgical")
    print("  - Mass Casualty Protocol")
    print("  - Siege Medicine")
    print("")
    print("ARBITER coherence ranking: https://api.arbiter.traut.ai")
    uvicorn.run(app, host="0.0.0.0", port=8001)
