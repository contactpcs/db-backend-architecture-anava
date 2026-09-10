from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


class ReportsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def doctor_patients_overview(self, doctor_profile_id, disease_id: str) -> list[dict]:
        """One row per (patient, disease_composite_scores row) for every
        patient currently assigned to this doctor, for one disease. A
        patient with no composite computed yet for this disease still gets
        one row (all composite columns NULL via the LEFT JOIN) so they show
        up as "0 assessments" rather than vanishing.

        doctor_patient_assignments is the ONLY doctor-scoping mechanism in
        this system (Documents/Anava_Doctor_Portal_Analytics_Dashboard_
        Backend_Design_v1.docx Section 3 — RLS is bypassed at runtime, this
        WHERE clause is the real access control). doctor_id/patient_id are
        both profile ids, same id space as disease_composite_scores.
        patient_id, so no lookup through `doctors`/`patients` is needed.
        """
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT p.id AS patient_id, p.first_name, p.last_name, "
                        "dcs.composite_id, dcs.calculated_value, dcs.severity_level, "
                        "dcs.severity_label, dcs.is_provisional, dcs.is_baseline, dcs.computed_at "
                        "FROM doctor_patient_assignments dpa "
                        "JOIN profiles p ON p.id = dpa.patient_id "
                        "LEFT JOIN disease_composite_scores dcs "
                        "  ON dcs.patient_id = dpa.patient_id AND dcs.disease_id = :disease_id "
                        "WHERE dpa.doctor_id = :doctor_id AND dpa.status = 'active' "
                        "ORDER BY p.id, dcs.computed_at"
                    ),
                    {"doctor_id": str(doctor_profile_id), "disease_id": disease_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def patient_scale_trajectories(self, doctor_profile_id, patient_id, disease_id: str) -> list[dict]:
        """Full history (not cycle-scoped — unlike the composite's staleness
        rule, a trajectory chart is supposed to show every prior cycle too)
        of every scale mapped to this disease, for one patient. The
        doctor_patient_assignments join is what scopes this to the calling
        doctor's own patient — a patient_id outside that set returns zero
        rows, same convention as the other endpoints in this module."""
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT sc.scale_code, sc.scale_name, "
                        "sr.direction_corrected_percentage AS score, "
                        "sr.severity_level, sr.severity_label, pai.completed_at AS date "
                        "FROM doctor_patient_assignments dpa "
                        "JOIN prs_assessment_instances pai ON pai.patient_id = dpa.patient_id "
                        "JOIN prs_scale_results sr ON sr.instance_id = pai.instance_id "
                        "JOIN prs_scales sc ON sc.scale_id = sr.scale_id "
                        "JOIN prs_disease_scale_map m ON m.scale_id = sr.scale_id AND m.disease_id = :disease_id "
                        "WHERE dpa.doctor_id = :doctor_id AND dpa.status = 'active' AND dpa.patient_id = :patient_id "
                        "AND pai.status = 'completed' AND pai.is_voided = FALSE "
                        "AND sr.direction_corrected_percentage IS NOT NULL "
                        "ORDER BY sc.scale_code, pai.completed_at"
                    ),
                    {"doctor_id": str(doctor_profile_id), "patient_id": str(patient_id), "disease_id": disease_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def doctor_protocol_scale_captures(self, doctor_profile_id, disease_id: str) -> list[dict]:
        """Raw scale-level rows from PRS captured DURING a treatment
        protocol's device sessions (core.device_session_prs_responses) —
        the weekly-cadence capture the Weekly Composite Score Trend table
        is built from (Backend Design v1 Section 5.7), as opposed to
        disease_composite_scores which mixes in main/registration
        assessments too. One device session's instance can score several
        scales at once; the service groups by (patient_id, recorded_at) and
        runs the same weighted composite formula used everywhere else.

        LEFT JOIN all the way through so a patient with zero device-session
        captures still gets one row (all-null) — same "still show up, empty"
        convention as doctor_patients_overview — while the trailing WHERE
        drops any scale that matched a different disease's map."""
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT p.id AS patient_id, p.first_name, p.last_name, "
                        "ds.recorded_at, sc.scale_code, sr.direction_corrected_percentage AS percentage "
                        "FROM doctor_patient_assignments dpa "
                        "JOIN profiles p ON p.id = dpa.patient_id "
                        "LEFT JOIN device_session_prs_responses ds ON ds.patient_id = dpa.patient_id "
                        "LEFT JOIN prs_scale_results sr ON sr.instance_id = ds.instance_id "
                        "  AND sr.direction_corrected_percentage IS NOT NULL "
                        "LEFT JOIN prs_scales sc ON sc.scale_id = sr.scale_id "
                        "LEFT JOIN prs_disease_scale_map m ON m.scale_id = sr.scale_id AND m.disease_id = :disease_id "
                        "WHERE dpa.doctor_id = :doctor_id AND dpa.status = 'active' "
                        "AND (ds.ds_prs_id IS NULL OR m.scale_id IS NOT NULL) "
                        "ORDER BY p.id, ds.recorded_at"
                    ),
                    {"doctor_id": str(doctor_profile_id), "disease_id": disease_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def doctor_protocol_scale_history(self, doctor_profile_id, disease_id: str) -> list[dict]:
        """Per-scale, per-session rows for every treatment protocol this
        doctor's patients have run device sessions under, for one disease
        (Backend Design v1 Sections 5.5/5.6). "Protocol" here means device
        modality + placement/montage — reference.neuromod_devices.modality
        joined with whichever ONE of the six per-device placement tables
        protocol_plan points at (confirmed real schema: a protocol has
        exactly one placement FK set; there is no single placements table,
        so this is the 6-way UNION-shaped COALESCE the schema's own
        comments warn cross-device reporting needs). device_session_prs_
        responses.protocol_id is a DIRECT column — no appointment hop
        needed to find which protocol a session belongs to."""
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT p.id AS patient_id, "
                        "nd.modality || ' — ' || COALESCE(t1.montage_label, t2.montage_label, t3.montage_label, "
                        "t4.montage_label, t5.montage_label, t6.montage_label) AS protocol_label, "
                        "sc.scale_code, sc.scale_name, sr.direction_corrected_percentage AS percentage, ds.recorded_at "
                        "FROM doctor_patient_assignments dpa "
                        "JOIN profiles p ON p.id = dpa.patient_id "
                        "JOIN device_session_prs_responses ds ON ds.patient_id = dpa.patient_id "
                        "JOIN protocol_plan pp ON pp.protocol_id = ds.protocol_id "
                        "JOIN neuromod_devices nd ON nd.device_id = pp.device_id "
                        "LEFT JOIN tdcs_placements t1 ON t1.tdcs_placement_id = pp.tdcs_placement_id "
                        "LEFT JOIN hd_tdcs_placements t2 ON t2.hd_tdcs_placement_id = pp.hd_tdcs_placement_id "
                        "LEFT JOIN tavns_placements t3 ON t3.tavns_placement_id = pp.tavns_placement_id "
                        "LEFT JOIN tps_placements t4 ON t4.tps_placement_id = pp.tps_placement_id "
                        "LEFT JOIN rtms_placements t5 ON t5.rtms_placement_id = pp.rtms_placement_id "
                        "LEFT JOIN other_placements t6 ON t6.other_placement_id = pp.other_placement_id "
                        "JOIN prs_scale_results sr ON sr.instance_id = ds.instance_id "
                        "  AND sr.direction_corrected_percentage IS NOT NULL "
                        "JOIN prs_scales sc ON sc.scale_id = sr.scale_id "
                        "JOIN prs_disease_scale_map m ON m.scale_id = sr.scale_id AND m.disease_id = :disease_id "
                        "WHERE dpa.doctor_id = :doctor_id AND dpa.status = 'active' "
                        "ORDER BY p.id, protocol_label, sc.scale_code, ds.recorded_at"
                    ),
                    {"doctor_id": str(doctor_profile_id), "disease_id": disease_id},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]

    async def doctor_active_patient_count(self, doctor_profile_id) -> int:
        """Distinct headcount of this doctor's active patients — independent
        of disease, unlike every other count in this module (which counts
        (patient, disease) pairs). Backs the "Patients under review" cohort
        summary card, which is a real headcount, not a pair count."""
        result = await self.session.execute(
            text("SELECT COUNT(DISTINCT patient_id) FROM doctor_patient_assignments WHERE doctor_id = :doctor_id AND status = 'active'"),
            {"doctor_id": str(doctor_profile_id)},
        )
        return result.scalar_one()

    async def doctor_diseases_overview(self, doctor_profile_id) -> list[dict]:
        """Every active disease in the reference catalog (Backend Design v1
        Section 5.1), even ones this doctor has zero patients or zero PRS
        data for — those come back as one row with composite_id/patient_id
        NULL, same "still show up, empty" convention as doctor_patients_
        overview, so the cohort landing view is the full catalog, not just
        whatever this doctor happens to have scored data for.

        The scoping subquery (disease_composite_scores pre-filtered to this
        doctor's active patients, THEN left-joined to the catalog) is what
        keeps another doctor's patients from leaking into these counts —
        joining doctor_patient_assignments after the LEFT JOIN would turn
        every unscored disease's placeholder row into N doctor-patient rows
        instead of one null row."""
        rows = (
            (
                await self.session.execute(
                    text(
                        "SELECT d.disease_id, d.disease_name, dcs.patient_id, "
                        "dcs.composite_id, dcs.calculated_value, dcs.is_baseline, dcs.is_provisional, dcs.computed_at "
                        "FROM prs_diseases d "
                        "LEFT JOIN ("
                        "  SELECT s.* FROM disease_composite_scores s "
                        "  JOIN doctor_patient_assignments dpa ON dpa.patient_id = s.patient_id "
                        "  WHERE dpa.doctor_id = :doctor_id AND dpa.status = 'active'"
                        ") dcs ON dcs.disease_id = d.disease_id "
                        "WHERE d.status = TRUE "
                        "ORDER BY d.disease_id, dcs.patient_id, dcs.computed_at"
                    ),
                    {"doctor_id": str(doctor_profile_id)},
                )
            )
            .mappings()
            .all()
        )
        return [dict(r) for r in rows]
