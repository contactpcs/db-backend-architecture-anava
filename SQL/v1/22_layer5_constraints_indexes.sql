-- Layer 5 — primary keys, foreign keys, indexes for the new tables + the new
-- consent_records.guardian_id FK. ON DELETE RESTRICT throughout, matching the
-- rest of the schema's integrity rule (never cascade on clinical/legal data).

-- Primary keys
ALTER TABLE compliance."erasure_requests" ADD CONSTRAINT "erasure_requests_pkey" PRIMARY KEY ("request_id");
ALTER TABLE compliance."erasure_request_items" ADD CONSTRAINT "erasure_request_items_pkey" PRIMARY KEY ("item_id");
ALTER TABLE compliance."data_portability_requests" ADD CONSTRAINT "data_portability_requests_pkey" PRIMARY KEY ("request_id");
ALTER TABLE compliance."staff_termination_authorizations" ADD CONSTRAINT "staff_termination_authorizations_pkey" PRIMARY KEY ("termination_id");
ALTER TABLE compliance."compliance_incidents" ADD CONSTRAINT "compliance_incidents_pkey" PRIMARY KEY ("incident_id");
ALTER TABLE compliance."manual_snapshots" ADD CONSTRAINT "manual_snapshots_pkey" PRIMARY KEY ("snapshot_id");

-- Foreign keys
ALTER TABLE compliance."erasure_requests" ADD CONSTRAINT "fk_erasure_requests_patient_id" FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."erasure_requests" ADD CONSTRAINT "fk_erasure_requests_requested_by" FOREIGN KEY ("requested_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."erasure_request_items" ADD CONSTRAINT "fk_erasure_request_items_request_id" FOREIGN KEY ("request_id") REFERENCES compliance."erasure_requests" ("request_id") ON DELETE RESTRICT;
ALTER TABLE compliance."data_portability_requests" ADD CONSTRAINT "fk_data_portability_requests_patient_id" FOREIGN KEY ("patient_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."data_portability_requests" ADD CONSTRAINT "fk_data_portability_requests_requested_by" FOREIGN KEY ("requested_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."staff_termination_authorizations" ADD CONSTRAINT "fk_staff_term_staff_profile_id" FOREIGN KEY ("staff_profile_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."staff_termination_authorizations" ADD CONSTRAINT "fk_staff_term_primary_authorizer" FOREIGN KEY ("primary_authorizer_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."staff_termination_authorizations" ADD CONSTRAINT "fk_staff_term_secondary_authorizer" FOREIGN KEY ("secondary_authorizer_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."compliance_incidents" ADD CONSTRAINT "fk_compliance_incidents_detected_by" FOREIGN KEY ("detected_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."manual_snapshots" ADD CONSTRAINT "fk_manual_snapshots_created_by" FOREIGN KEY ("created_by") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;
ALTER TABLE compliance."consent_records" ADD CONSTRAINT "fk_consent_records_guardian_id" FOREIGN KEY ("guardian_id") REFERENCES core."profiles" ("id") ON DELETE RESTRICT;

-- Indexes
CREATE INDEX "idx_erasure_requests_patient_id" ON compliance."erasure_requests" USING btree ("patient_id");
CREATE INDEX "idx_erasure_requests_status" ON compliance."erasure_requests" USING btree ("status");
CREATE INDEX "idx_erasure_requests_response_due" ON compliance."erasure_requests" USING btree ("response_due_at") WHERE (status <> 'completed');
CREATE INDEX "idx_erasure_request_items_request_id" ON compliance."erasure_request_items" USING btree ("request_id");
CREATE INDEX "idx_erasure_request_items_bucket" ON compliance."erasure_request_items" USING btree ("bucket");
CREATE INDEX "idx_erasure_request_items_retention_expires" ON compliance."erasure_request_items" USING btree ("retention_expires_at") WHERE (deleted_at IS NULL);
CREATE INDEX "idx_dpr_patient_id" ON compliance."data_portability_requests" USING btree ("patient_id");
CREATE INDEX "idx_dpr_status" ON compliance."data_portability_requests" USING btree ("status");
CREATE INDEX "idx_staff_term_staff_profile_id" ON compliance."staff_termination_authorizations" USING btree ("staff_profile_id");
CREATE INDEX "idx_staff_term_type" ON compliance."staff_termination_authorizations" USING btree ("termination_type");
CREATE INDEX "idx_compliance_incidents_status" ON compliance."compliance_incidents" USING btree ("status");
CREATE INDEX "idx_compliance_incidents_detected_at" ON compliance."compliance_incidents" USING btree ("detected_at");
CREATE INDEX "idx_manual_snapshots_intended_deletion" ON compliance."manual_snapshots" USING btree ("intended_deletion_at") WHERE (deleted_at IS NULL);
CREATE INDEX "idx_consent_records_guardian_id" ON compliance."consent_records" USING btree ("guardian_id") WHERE (guardian_id IS NOT NULL);
CREATE INDEX "idx_patients_closure_type" ON core."patients" USING btree ("closure_type") WHERE (closure_type IS NOT NULL);
CREATE INDEX "idx_patients_retention_cleared" ON core."patients" USING btree ("retention_basis_cleared_at") WHERE (legal_hold = false);
CREATE INDEX "idx_profiles_anonymized" ON core."profiles" USING btree ("is_anonymized") WHERE (is_anonymized = false);
