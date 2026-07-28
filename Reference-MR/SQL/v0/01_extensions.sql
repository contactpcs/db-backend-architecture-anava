-- ============================================================
-- Anava Clinic — DB Schema
-- File 01: Extensions
-- Run this first on a fresh RDS PostgreSQL 14+ instance
-- ============================================================

-- pgcrypto: required for pgp_sym_encrypt (PHI column-level encryption)
-- and for sha256() used in consent_templates.content_hash.
-- gen_random_uuid() is built-in from PG13+; uuid-ossp NOT needed.
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
