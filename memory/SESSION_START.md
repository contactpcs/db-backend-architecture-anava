# SESSION START — READ THIS FIRST
Every new session: read PROJECT_MEMORY.md for full context.
This file is the 30-second orientation.

---

## Project
Anava Clinic — modifying NeuroWellness FastAPI backend.
Existing code: D:\PCS\Backend_v1\neurowellness\
New code: D:\PCS\backend-v2\backend\

## Current Status
- Master Document: DONE ✅ → D:\PCS\backend-v2\Documents\Anava_Master_Document_v1.docx
- DB Schema SQL: DONE ✅ → D:\PCS\backend-v2\SQL\ (17 files, 37 tables)
- Backend Code: NOT STARTED ⏳ → D:\PCS\backend-v2\backend\

## Stack
FastAPI | AWS RDS PostgreSQL | AWS Cognito | AWS S3 | Alembic | Razorpay | boto3

## SQL Files (run in order)
00_run_all.sql → master runner
01 extensions → 02 core tables → 03 staff roles → 04 patients →
05 requests → 06 clinical → 07 prs → 08 anamnesis → 09 consent →
10 store → 11 payments → 12 logging → 13 indexes → 14 triggers →
15 rls policies → 16 seed data

## Next Task
Start backend code in D:\PCS\backend-v2\backend\
Begin Phase 1: Foundation
  - FastAPI project structure
  - Alembic migration setup (run SQL schema through Alembic)
  - Cognito JWT middleware
  - RDS connection (async SQLAlchemy)
  - S3 client setup
  - App context middleware (SET LOCAL app.current_user_id for RLS)

## Rules
- Doc → Schema → Code (strict order)
- No schema changes without updating Master Doc first
- User: limited backend experience, needs plain explanations when asked
