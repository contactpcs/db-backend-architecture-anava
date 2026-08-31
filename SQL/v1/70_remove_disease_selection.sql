-- 70_remove_disease_selection.sql
--
-- APPLY ORDER: after 69. Independent of 64-69.
--
-- THE PROBLEM (Mohan, 27 Aug 2026)
--
-- Registration wizard makes a patient pick a "primary disease/condition"
-- before consent — clinically odd (patients shouldn't self-diagnose before
-- ever seeing a doctor) and the user wants it removed: from the frontend
-- flow, the registration_status state machine, and the DB fields that
-- capture it.
--
-- What actually depended on it (found before removing anything, not
-- assumed):
--   1. Registration's own PRS step (assessment_stage='general_registration')
--      never actually used disease_id for scale selection —
--      PatientScaleAssignmentService.auto_assign_for_disease hardcodes
--      EQ-5D-5L regardless of disease (prs/service.py). Purely plumbing here.
--   2. ProtocolRequestService.decide() (clinical/service.py) DID genuinely
--      depend on it — a CA-submitted, doctor-approved protocol request had
--      no disease_id of its own, so it fell back to the patient's
--      registration-time primary disease selection to create the
--      main_clinical patient_scale_assignments rows. Fixed in application
--      code: the CA now supplies disease_id directly in protocol_details
--      (the same free-form JSONB that already carries main_prs_scale_ids)
--      at submission time — no schema change needed for that table, decide()
--      just reads protocol_details['disease_id'] instead of looking up
--      patient_disease_selection.
--
-- THE FIX
--
-- 1. core.prs_assessment_instances.disease_id becomes nullable — a
--    general_registration instance no longer has a disease to record (its
--    scale composition is hardcoded to EQ-5D-5L regardless, see
--    prs/service.py::_compose_scales). Every OTHER assessment_stage
--    (main_clinical, followup, ...) still always supplies a real disease_id
--    from its own source (doctor's manual assign flow, or now protocol_
--    details['disease_id']) — nothing else about that column's real usage
--    changes, this only stops it rejecting the one stage that never needed it.
--
-- 2. core.patient_disease_selection is dropped entirely — hard delete per
--    explicit decision (not soft-deprecated), including its historical rows.
--    reference.prs_diseases (the disease catalog itself) is NOT touched —
--    it's actively used by the doctor's manual "Assign Assessment" picker
--    and the treatment-protocol condition-to-scale bridge (51), entirely
--    independent of the registration wizard.

BEGIN;

ALTER TABLE core."prs_assessment_instances" ALTER COLUMN "disease_id" DROP NOT NULL;

DROP TABLE IF EXISTS core."patient_disease_selection" CASCADE;

COMMIT;
