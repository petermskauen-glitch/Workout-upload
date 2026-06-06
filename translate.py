"""Oversetter fra det enkle øktformatet til Garmins workout-objekter.

Bruker python-garminconnect sine egne hjelpefunksjoner for tidsbaserte steg,
og bygger distanse-baserte steg manuelt (det har ikke biblioteket hjelper for).
"""
from __future__ import annotations

from garminconnect.workout import (
    RunningWorkout, CyclingWorkout, SwimmingWorkout, WalkingWorkout, HikingWorkout,
    WorkoutSegment, ExecutableStep,
    SportType, StepType, ConditionType, TargetType,
    create_warmup_step, create_interval_step, create_recovery_step,
    create_cooldown_step, create_repeat_group,
)

SPORT = {
    "løping":   (RunningWorkout, SportType.RUNNING,  "running",  1),
    "sykkel":   (CyclingWorkout, SportType.CYCLING,  "cycling",  2),
    "svømming": (SwimmingWorkout, SportType.SWIMMING, "swimming", 3),
    "gange":    (WalkingWorkout, SportType.WALKING,  "walking",  4),
}

# Hvilken hjelpefunksjon hver stegtype bruker
STEP_HELPER = {
    "oppvarming": create_warmup_step,
    "rolig":      create_interval_step,
    "intervall":  create_interval_step,
    "pause":      create_recovery_step,
    "nedjogg":    create_cooldown_step,
}
# (stepTypeId, key, displayOrder) for manuell bygging av distanse-steg
STEP_META = {
    "oppvarming": (StepType.WARMUP,   "warmup",   1),
    "rolig":      (StepType.INTERVAL, "interval", 3),
    "intervall":  (StepType.INTERVAL, "interval", 3),
    "pause":      (StepType.RECOVERY, "recovery", 4),
    "nedjogg":    (StepType.COOLDOWN, "cooldown", 2),
}
INTENSITY_ZONE = {"rolig": 2, "moderat": 3, "terskel": 4, "maks": 5}


def _seconds(s: str) -> float:
    s = s.strip().lower()
    if s.endswith("min"): return float(s[:-3].strip()) * 60
    if s.endswith("s"):   return float(s[:-1].strip())
    raise ValueError(f"Ugyldig varighet: {s!r} (bruk f.eks. '15 min' eller '90 s')")


def _meters(s: str) -> float:
    s = s.strip().lower()
    if s.endswith("km"): return float(s[:-2].strip()) * 1000
    if s.endswith("m"):  return float(s[:-1].strip())
    raise ValueError(f"Ugyldig distanse: {s!r} (bruk f.eks. '1000 m' eller '5 km')")


def _hr_target(intensitet: str | None):
    """Returnerer (target_dict, zone_number eller None)."""
    if not intensitet or intensitet == "ingen":
        return None, None
    zone = INTENSITY_ZONE.get(intensitet)
    if zone is None:
        raise ValueError(f"Ukjent intensitet: {intensitet!r}")
    target = {
        "workoutTargetTypeId": int(TargetType.HEART_RATE),
        "workoutTargetTypeKey": "heart.rate.zone",
        "displayOrder": 4,
    }
    return target, zone


def _build_step(step: dict, order: int) -> ExecutableStep:
    stype = step.get("type")
    if stype not in STEP_HELPER:
        raise ValueError(f"Ukjent stegtype: {stype!r}")
    target, zone = _hr_target(step.get("intensitet"))

    if "distanse" in step:
        # Manuelt distanse-steg (biblioteket har bare tidsbaserte hjelpere)
        type_id, type_key, type_order = STEP_META[stype]
        es = ExecutableStep(
            stepOrder=order,
            stepType={"stepTypeId": int(type_id), "stepTypeKey": type_key, "displayOrder": type_order},
            endCondition={"conditionTypeId": int(ConditionType.DISTANCE), "conditionTypeKey": "distance",
                          "displayOrder": 3, "displayable": True},
            endConditionValue=_meters(step["distanse"]),
            targetType=target or {"workoutTargetTypeId": int(TargetType.NO_TARGET),
                                  "workoutTargetTypeKey": "no.target", "displayOrder": 1},
        )
    elif "varighet" in step:
        es = STEP_HELPER[stype](_seconds(step["varighet"]), order, target_type=target)
    else:
        raise ValueError(f"Steg mangler 'varighet' eller 'distanse': {step}")

    if zone is not None:
        es.zoneNumber = zone  # ekstra felt Garmin bruker til HR-sone
    return es


def build(data: dict):
    sport = data.get("sport")
    if sport not in SPORT:
        raise ValueError(f"Sporten '{sport}' støttes ikke ennå (styrke kommer i neste runde).")
    cls, sport_id, sport_key, sport_order = SPORT[sport]
    sport_dict = {"sportTypeId": int(sport_id), "sportTypeKey": sport_key, "displayOrder": sport_order}

    steps, order, est = [], 1, 0.0
    for node in data.get("steg", []):
        if "gjenta" in node:
            kids = []
            for sub in node["steg"]:
                kids.append(_build_step(sub, order)); order += 1
                if "varighet" in sub:
                    est += _seconds(sub["varighet"]) * node["gjenta"]
            steps.append(create_repeat_group(node["gjenta"], kids, order)); order += 1
        else:
            steps.append(_build_step(node, order)); order += 1
            if "varighet" in node:
                est += _seconds(node["varighet"])

    return cls(
        workoutName=data["navn"],
        estimatedDurationInSecs=int(est) or 1800,
        workoutSegments=[WorkoutSegment(segmentOrder=1, sportType=sport_dict, workoutSteps=steps)],
    )
