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


# ───────────────────────── Styrke ─────────────────────────
# Garmin har ingen typed strength-workout i biblioteket, så vi bygger rå-JSON
# selv og poster til /workout-service/workout. Øvelsesnavn (category/exerciseName)
# kommer fra strength/garmin_exercises.json via 'exmap'.

_STRENGTH_SPORT = {"sportTypeId": 5, "sportTypeKey": "strength_training", "displayOrder": 5}
_NO_TARGET = {"workoutTargetTypeId": 1, "workoutTargetTypeKey": "no.target", "displayOrder": 1}


def build_strength(data: dict, exmap: dict) -> dict:
    """Bygg Garmins rå workout-JSON for en styrkeøkt.

    data: { "navn", "sport":"styrke", "ovelser": [
              { "ovelse": <vennlig navn>, "sett": n, "reps": n,
                "vekt": <kg valgfri>, "pause": <"90 s"/"2 min" valgfri> }, ... ] }
    exmap: vennlig navn -> { "category", "exerciseName" }
    """
    ovelser = data.get("ovelser") or []
    if not ovelser:
        raise ValueError("Styrkeøkt mangler 'ovelser'.")

    steps = []
    counter = [1]
    def nxt():
        o = counter[0]; counter[0] += 1; return o

    def make_exercise(m, reps, vekt):
        s = {
            "type": "ExecutableStepDTO", "stepOrder": nxt(),
            "stepType": {"stepTypeId": 3, "stepTypeKey": "interval", "displayOrder": 3},
            "endCondition": {"conditionTypeId": 10, "conditionTypeKey": "reps",
                             "displayOrder": 10, "displayable": True},
            "endConditionValue": float(reps),
            "category": m["category"], "exerciseName": m["exerciseName"],
            "targetType": _NO_TARGET,
        }
        if vekt:
            try:
                s["weightValue"] = float(vekt) * 1000.0   # Garmin: gram
                s["weightDisplayUnit"] = {"unitId": 8, "unitKey": "kilogram", "factor": 1000.0}
            except (TypeError, ValueError):
                pass
        return s

    def make_rest(pause):
        return {
            "type": "ExecutableStepDTO", "stepOrder": nxt(),
            "stepType": {"stepTypeId": 5, "stepTypeKey": "rest", "displayOrder": 5},
            "endCondition": {"conditionTypeId": 2, "conditionTypeKey": "time",
                             "displayOrder": 2, "displayable": True},
            "endConditionValue": _seconds(pause),
            "targetType": _NO_TARGET,
        }

    for ov in ovelser:
        friendly = str(ov.get("ovelse", "")).strip().lower()
        m = exmap.get(friendly)
        if not m:
            raise ValueError(f"Ukjent øvelse: {ov.get('ovelse')!r}.")
        sett = int(ov.get("sett") or 1)
        reps = int(ov.get("reps") or 10)
        vekt = ov.get("vekt")
        pause = ov.get("pause")

        if sett > 1:
            grp_order = nxt()
            children = [make_exercise(m, reps, vekt)]
            if pause:
                children.append(make_rest(pause))
            steps.append({
                "type": "RepeatGroupDTO", "stepOrder": grp_order,
                "stepType": {"stepTypeId": 6, "stepTypeKey": "repeat", "displayOrder": 6},
                "numberOfIterations": sett, "smartRepeat": False,
                "workoutSteps": children,
            })
        else:
            steps.append(make_exercise(m, reps, vekt))
            if pause:
                steps.append(make_rest(pause))

    return {
        "workoutName": data.get("navn") or "Styrkeøkt",
        "sportType": _STRENGTH_SPORT,
        "workoutSegments": [{
            "segmentOrder": 1, "sportType": _STRENGTH_SPORT, "workoutSteps": steps,
        }],
    }
