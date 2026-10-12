"""Final Report section builders — pure functions, no database.

Rules from Documents/Anava_Final_Report_Plan_v1.md: every anamnesis question
is printed, PRS shows the last 5 per disease with a per-sitting aggregate,
medication is full history, only prescribed protocols appear, session details
carry counts and deviations but no CA notes, and `last_consultation` covers
previous consultation -> last consultation."""

from datetime import datetime

import pytest

from app.modules.final_reports import assembler as a

NOW = datetime(2026, 10, 12, 12, 0)


def _instance(n, *, disease="DEP", day, **extra):
    return {
        "instance_id": f"i{n}",
        "disease_id": disease,
        "disease_name": "Depression",
        "completed_at": datetime(2026, 10, day),
        "assessment_stage": "main",
        "appointment_type": "follow_up",
        "session_number": None,
        "ds_protocol_id": None,
        "protocol_id": None,
        "device_name": None,
        **extra,
    }


def _scale(instance_id, code, pct):
    return {
        "instance_id": instance_id,
        "scale_code": code,
        "scale_name": code,
        "calculated_value": pct / 10,
        "max_possible": 10,
        "percentage": pct,
        "direction_corrected_percentage": pct,
        "severity_label": "Moderate",
    }


# -- window ------------------------------------------------------------------


def test_open_window_keeps_everything_even_without_a_timestamp():
    assert a.in_window(None, None, None)


def test_window_start_is_exclusive_and_end_inclusive():
    start, end = datetime(2026, 10, 1), datetime(2026, 10, 5)
    assert not a.in_window(start, start, end)
    assert a.in_window(end, start, end)
    assert not a.in_window(datetime(2026, 10, 6), start, end)
    assert not a.in_window(None, start, end)


def test_window_compares_aware_and_naive_timestamps():
    assert a.in_window("2026-10-03T10:00:00+00:00", datetime(2026, 10, 1), datetime(2026, 10, 5))


def test_last_consultation_window_runs_from_the_previous_one():
    first = {"appointment_id": "c1", "started_at": datetime(2026, 9, 1), "completed_at": datetime(2026, 9, 1, 1)}
    last = {"appointment_id": "c2", "started_at": datetime(2026, 10, 1), "completed_at": datetime(2026, 10, 1, 1)}
    window = a.consultation_window([first, last], NOW)
    assert window == {"consultation": last, "start": first["started_at"], "end": last["completed_at"]}


def test_open_consultation_ends_now_and_first_one_has_no_start():
    only = {"appointment_id": "c1", "started_at": datetime(2026, 10, 1), "completed_at": None}
    assert a.consultation_window([only], NOW) == {"consultation": only, "start": None, "end": NOW}
    assert a.consultation_window([], NOW)["consultation"] is None


# -- anamnesis ---------------------------------------------------------------


def test_anamnesis_prints_every_question_and_resolves_option_labels():
    assessment = {"anamnesis_id": "ANA-1", "assessment_stage": "main", "status": "completed", "created_at": NOW}
    questions = [
        {"question_id": "q2", "section_number": 2, "section_title": "History", "question_text": "Sleep?", "response_value": "poor"},
        {"question_id": "q1", "section_number": 1, "section_title": "Complaint", "question_text": "Why?", "response_value": None},
        {
            "question_id": "q3",
            "section_number": 2,
            "section_title": "History",
            "question_text": "Symptoms?",
            "response_value": None,
            "response_values": ["a", "b"],
        },
    ]
    built = a.build_anamnesis(assessment, questions, {("q2", "poor"): "Poor sleep", ("q3", "a"): "Anxiety"})
    assert [s["title"] for s in built["sections"]] == ["Complaint", "History"]
    assert built["sections"][0]["items"] == [{"question": "Why?", "answer": None}]
    assert built["sections"][1]["items"] == [
        {"question": "Sleep?", "answer": "Poor sleep"},
        {"question": "Symptoms?", "answer": "Anxiety, b"},
    ]


# -- PRS ---------------------------------------------------------------------


def test_prs_keeps_the_last_five_per_disease_with_a_per_sitting_aggregate():
    instances = [_instance(n, day=n) for n in range(7, 0, -1)]  # newest first
    scales = [_scale(f"i{n}", "PHQ9", n * 10) for n in range(1, 8)]
    prs = a.build_prs(instances, scales, {"DEP": {"PHQ9": 100}}, {}, now=NOW)
    entries = prs["diseases"][0]["entries"]
    assert [e["instance_id"] for e in entries] == ["i7", "i6", "i5", "i4", "i3"]
    # Each row is its own sitting's score, not one blended number repeated.
    assert [e["score"] for e in entries] == [70.0, 60.0, 50.0, 40.0, 30.0]
    assert entries[0]["scales"][0]["scale_code"] == "PHQ9"


def test_prs_weights_scales_and_ignores_unweighted_ones_in_the_aggregate():
    instances = [_instance(1, day=5)]
    scales = [_scale("i1", "PHQ9", 80), _scale("i1", "GAD7", 40), _scale("i1", "EXTRA", 0)]
    entry = a.build_prs(instances, scales, {"DEP": {"PHQ9": 75, "GAD7": 25}}, {}, now=NOW)["diseases"][0]["entries"][0]
    assert entry["score"] == 70.0  # (80*75 + 40*25) / 100
    assert [s["scale_code"] for s in entry["scales"]] == ["EXTRA", "GAD7", "PHQ9"]  # still listed


def test_prs_without_any_weight_has_no_aggregate_but_keeps_its_scales():
    entry = a.build_prs([_instance(1, day=5)], [_scale("i1", "PHQ9", 80)], {}, {}, now=NOW)["diseases"][0]["entries"][0]
    assert entry["score"] is None
    assert len(entry["scales"]) == 1


def test_device_session_prs_carries_device_and_condition_others_do_not():
    session_prs = _instance(1, day=6, appointment_type=None, ds_protocol_id="p1", device_name="Sooma tDCS", session_number=4)
    consult_prs = _instance(2, day=5, protocol_id="p1", device_name="Sooma tDCS")
    prs = a.build_prs([session_prs, consult_prs], [], {}, {"p1": ["Depression"]}, now=NOW)
    first, second = prs["diseases"][0]["entries"]
    assert (first["origin"], first["device_name"], first["conditions"], first["session_number"]) == (
        "device_session",
        "Sooma tDCS",
        ["Depression"],
        4,
    )
    assert (second["origin"], second["device_name"], second["conditions"]) == ("follow_up", None, [])


def test_prs_repeats_per_disease_and_respects_the_window():
    instances = [_instance(1, day=9), _instance(2, day=8, disease="ANX", disease_name="Anxiety"), _instance(3, day=2)]
    prs = a.build_prs(instances, [], {}, {}, window_start=datetime(2026, 10, 5), window_end=NOW, now=NOW)
    assert [d["disease_name"] for d in prs["diseases"]] == ["Anxiety", "Depression"]
    assert [e["instance_id"] for e in prs["diseases"][1]["entries"]] == ["i1"]  # i3 is before the window


def test_registration_prs_without_a_disease_is_listed_as_a_general_assessment():
    # prs_assessment_instances.disease_id is NULL for the general registration
    # PRS; sorting the diseases by name used to crash on it (500 on preview).
    general = _instance(1, day=3, disease=None, disease_name=None, appointment_type=None, assessment_stage="general_registration")
    prs = a.build_prs([_instance(2, day=5), general], [_scale("i1", "EQ5D", 40)], {"DEP": {"PHQ9": 100}}, {}, now=NOW)
    assert [(d["disease_id"], d["disease_name"]) for d in prs["diseases"]] == [("DEP", "Depression"), (None, a.GENERAL_PRS_NAME)]
    entry = prs["diseases"][1]["entries"][0]
    assert (entry["origin"], entry["score"], len(entry["scales"])) == ("general_registration", None, 1)


def test_a_scale_missing_from_the_catalogue_falls_back_to_its_id():
    orphan = {**_scale("i1", None, 50), "scale_id": "OLD9/2026", "scale_name": None}
    entry = a.build_prs([_instance(1, day=5)], [orphan], {}, {}, now=NOW)["diseases"][0]["entries"][0]
    assert (entry["scales"][0]["scale_code"], entry["scales"][0]["scale_name"]) == ("OLD9", "OLD9")


def test_weekly_progress_is_five_weeks_and_carries_the_last_score_forward():
    instances = [_instance(1, day=1)]  # Thu 1 Oct 2026; NOW is Mon 12 Oct
    progress = a.build_prs(instances, [_scale("i1", "PHQ9", 60)], {"DEP": {"PHQ9": 100}}, {}, now=NOW)["diseases"][0]["weekly_progress"]
    assert len(progress["weeks"]) == a.PROGRESS_WEEKS == len(progress["scores"])
    assert progress["scores"] == [None, None, 60.0, 60.0, 60.0]


# -- notes, medication, protocols, sessions ----------------------------------


def test_only_ticked_notes_are_included():
    notes = [{"note_id": n, "category": "General", "note_text": n, "created_at": NOW} for n in ("n1", "n2", "n3")]
    assert [n["note_id"] for n in a.select_notes(notes, ["n3", "n1"])] == ["n1", "n3"]
    assert a.select_notes(notes, []) == []


def test_a_note_of_another_patient_is_refused_not_dropped():
    with pytest.raises(KeyError):
        a.select_notes([{"note_id": "n1", "category": "General", "note_text": "x", "created_at": NOW}], ["n1", "foreign"])


def test_medication_is_full_history_active_first():
    rows = [
        {"medicine_name": "Old", "status": "stopped", "started_at": NOW},
        {"medicine_name": "Current", "status": "active", "started_at": NOW},
    ]
    assert [(m["medicine_name"], m["status"]) for m in a.build_medications(rows)] == [("Current", "active"), ("Old", "stopped")]


def test_only_protocols_that_are_or_were_the_plan_are_listed():
    protocols = [{"protocol_id": n, "status": s} for n, s in enumerate(["draft", "active", "cancelled", "superseded", "completed"])]
    assert [p["status"] for p in a.pick_protocols(protocols, "overall")] == ["active", "completed"]


def test_last_consultation_shows_active_protocols_else_the_latest_completed():
    parallel = [{"protocol_id": 1, "status": "active"}, {"protocol_id": 2, "status": "active"}, {"protocol_id": 3, "status": "completed"}]
    assert [p["protocol_id"] for p in a.pick_protocols(parallel, "last_consultation")] == [1, 2]
    finished = [{"protocol_id": 1, "status": "draft"}, {"protocol_id": 2, "status": "completed"}, {"protocol_id": 3, "status": "completed"}]
    assert [p["protocol_id"] for p in a.pick_protocols(finished, "last_consultation")] == [2]


def test_treatment_plan_has_placement_dosage_and_schedule():
    detail = {
        "protocol_id": "p1",
        "status": "active",
        "version_major": 1,
        "version_minor": 2,
        "device_name": "Sooma tDCS",
        "modality": "tDCS",
        "placement_summary": "Anode F3 / Cathode F4",
        "custom_montage": None,
        "prescribed_current_ma": 2,
        "prescribed_duration_min": 30,
        "prescribed_tvns_pattern": None,
        "session_count": 20,
        "sessions_per_week": 5,
        "conditions": [{"condition_name": "Depression"}, {"condition_name": None, "other_text": "Tinnitus"}],
        "sessions": [{"session_number": 1, "appointment_date": "2026-10-01", "status": "completed"}],
        "follow_ups": [{"session_number": 5, "appointment_date": "2026-10-08", "status": "planned"}],
    }
    plan = a.build_treatment_plan(detail)
    assert plan["version"] == "1.2"
    assert plan["placement"] == "Anode F3 / Cathode F4"
    assert plan["dosage"] == {"prescribed_current_ma": 2, "prescribed_duration_min": 30}  # unset fields omitted
    assert plan["conditions"] == ["Depression", "Tinnitus"]
    assert plan["schedule"][0]["session_number"] == 1
    assert plan["follow_ups"][0]["after_session_number"] == 5


def test_custom_montage_name_is_the_placement():
    detail = {"protocol_id": "p1", "status": "active", "custom_montage": {"montage_name": "Custom F3"}, "placement_summary": None}
    assert a.build_treatment_plan(detail)["placement"] == "Custom F3"


def test_icd_codes_are_deduplicated_across_protocols():
    details = [
        {"diagnoses": [{"icd10_code": "F32.1", "icd10_description": "MDD, moderate"}]},
        {"diagnoses": [{"icd10_code": "F32.1", "icd10_description": "MDD, moderate"}, {"icd10_code": "F41.1", "icd10_description": "GAD"}]},
    ]
    assert [d["icd10_code"] for d in a.diagnosis_codes(details)] == ["F32.1", "F41.1"]


def test_sessions_list_deviations_and_counts_and_no_notes():
    row = {
        "session_number": 3,
        "appointment_date": "2026-10-06",
        "session_status": "stopped_early",
        "completed_at": None,
        "stopped_at": datetime(2026, 10, 6, 10),
        "device_name": "Sooma tDCS",
        "prescribed_current_ma": 2,
        "actual_intensity_ma": 1.5,
        "intensity_deviates": True,
        "duration_deviates": False,
        "ramp_up_deviates": True,
        "pause_stop_reason": "patient_discomfort",
        "symptom_count": 2,
        "adverse_event_count": 1,
        "note_text": "internal CA note",
    }
    (session,) = a.build_sessions([row])
    assert session["deviations"] == ["intensity", "ramp up"]
    assert session["stop_reason"] == "patient_discomfort"
    assert (session["symptom_count"], session["adverse_event_count"]) == (2, 1)
    assert "note_text" not in session


def test_sessions_outside_the_window_are_left_out():
    rows = [
        {"session_number": n, "appointment_date": f"2026-10-0{n}", "session_status": "completed", "completed_at": datetime(2026, 10, n)}
        for n in (1, 2, 3)
    ]
    kept = a.build_sessions(rows, window_start=datetime(2026, 10, 1), window_end=datetime(2026, 10, 2))
    assert [s["session_number"] for s in kept] == [2]


# -- hash --------------------------------------------------------------------


def test_content_hash_is_stable_and_changes_with_any_signed_part():
    snapshot, signature = {"b": 1, "a": [1, 2]}, {"typed_name": "Dr A"}
    base = a.content_hash("MDD", snapshot, signature)
    assert base == a.content_hash("MDD", {"a": [1, 2], "b": 1}, signature)  # key order does not matter
    assert base != a.content_hash("GAD", snapshot, signature)
    assert base != a.content_hash("MDD", {**snapshot, "b": 2}, signature)
    assert base != a.content_hash("MDD", snapshot, {"typed_name": "Dr B"})
