# Document Consistency Report
Generated: June 2026
Documents Checked: Application Bible v3 | Clinic Lifecycle | Patient Lifecycle | Master Document v1

---

## RESULT: CONSISTENT WITH INTENTIONAL EXTENSIONS

All 4 documents are aligned on clinical flows, rules, and architecture.
Master Document is a SUPERSET — it adds tech stack, AWS infra, store, payments, PRS additions
that are not in the original 3 docs (those docs are platform-agnostic, pre-discussion).

---

## POINTS CONSISTENT ACROSS ALL 4 DOCS ✓

| Topic | Status |
|-------|--------|
| 7 roles and responsibilities | ✓ Identical |
| Clinic lifecycle: 6 stages, status values (setup/active/pending_closure/closed) | ✓ Identical |
| Patient lifecycle: 6 phases | ✓ Identical |
| Session sequence: S1→S2→[S3→S4]→Treatment | ✓ Identical |
| CA designs protocol, Doctor only authorizes | ✓ Identical |
| Doctor auto-allocation: load balanced by lowest active_patient_count | ✓ Identical |
| 8 consent types, names identical | ✓ Identical |
| Consent records NEVER deleted | ✓ Identical |
| Two-layer logging: audit_logs (DB triggers) + activity_logs (app code) | ✓ Identical |
| Both logs NEVER deleted | ✓ Identical |
| Registration status machine: 6 exact steps | ✓ Identical |
| Session 1 + Session 3: ZERO patient-doctor contact | ✓ Identical |
| Session 4 always follows Session 3, never skipped | ✓ Identical |
| Patient relocation transfer: NOT an exit, block carries over no restart | ✓ Identical |
| Clinic closure: pending_closure → patient transfer/exit → staff offboard → closed | ✓ Identical |
| Receiving clinic must be same/neighboring region | ✓ Identical |
| patient_clinic_transfers table: 4 transfer_reason values | ✓ Identical |
| treatment_plans: parent_plan_id chain, superseded plans retained | ✓ Identical |
| device purchase: only after initial block treatment sessions complete | ✓ Identical |
| patient_onboarding consent: Receptionist witnesses | ✓ Identical |
| home_treatment_visit: does NOT start new block | ✓ Identical |
| One active appointment block per patient at a time | ✓ Identical |
| Data retention: all records permanent | ✓ Identical |

---

## INTENTIONAL UPDATES IN MASTER DOC (not contradictions)

These are changes from verbal discussion with user AFTER original 3 docs were written.
Original 3 docs are now outdated on these specific points.

| Topic | Original 3 Docs | Master Doc | Source |
|-------|----------------|------------|--------|
| Initial block treatment sessions | FIXED at exactly 5 | Standard 5, Doctor can extend beyond 5 (extended billed) | User verbal update |
| Follow-up block treatment sessions | 1 to 5 MAX | 1 to N (Doctor decides, extended sessions billed) | User verbal update |
| Treatment session billing | TBD placeholder | Razorpay, standard vs extended, clinic config | User verbal update |
| Store module | NOT in original docs | Full device + accessory + inventory system | User verbal update |
| Tech stack | NOT in original docs | AWS RDS + Cognito + S3 + Razorpay | User verbal update |
| S3 folder structure | NOT in original docs | Full structure with regions/clinics/patients | User verbal update |
| PRS: patient_scale_assignments table | NOT in original docs | New table added | User verbal update |
| PRS: prs_scales.applicable_for field | NOT in original docs | New field added | User verbal update |
| Anamnesis: block_id + version fields | NOT in original docs | New fields added | User verbal update |
| Super Admin bootstrap | NOT in original docs | AWS Secrets Manager + one-shot script | User verbal update |

---

## ONE ACTUAL DISCREPANCY IN ORIGINAL DOCS (between the 3 originals)

Bible v3 Flow U Step 2 says:
  "CHECK — no active patients mid-treatment" (implies clinic CANNOT close if patients exist)

Clinic Lifecycle Doc Stage 5 says:
  Patients are transferred/exited DURING pending_closure phase (clinic CAN close WITH active patients, they just need to be handled)

→ Clinic Lifecycle doc is correct. The Bible's CHECK in Flow U is misleading.
→ Master Doc follows the Clinic Lifecycle approach: pending_closure handles patients. ✓

---

## CONCLUSION

Master Document v1 is the authoritative source going forward.
Original 3 docs are reference material only.
Any future changes go into Master Document first, then schema, then code.
