-- ============================================================
-- Anava Clinic — DB Schema
-- File 10: Store Tables
-- products, store_orders, order_items, inventory,
-- stock_transfers, device_assignments
--
-- Order matters: device_assignments references store_orders,
-- so store_orders must be created first.
-- ============================================================

-- ------------------------------------------------------------
-- products — store catalog (devices and accessories)
-- ------------------------------------------------------------
CREATE TABLE products (
    product_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        TEXT NOT NULL,
    description TEXT,
    category    TEXT NOT NULL CHECK (category IN ('device', 'accessory')),
    price       NUMERIC(10, 2) NOT NULL CHECK (price >= 0),
    sku         TEXT UNIQUE,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ------------------------------------------------------------
-- store_orders — patient purchase orders
-- Device orders require Doctor approval before dispatch.
-- Accessory orders skip to pending_dispatch directly.
-- treatment_plan_id required for device orders (validation).
-- ------------------------------------------------------------
CREATE TABLE store_orders (
    order_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id        UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    clinic_id         UUID NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    initiated_by      UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    approved_by       UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    order_type        TEXT NOT NULL CHECK (order_type IN ('device', 'accessory')),
    status            TEXT NOT NULL DEFAULT 'pending_doctor_approval'
                          CHECK (status IN (
                              'pending_doctor_approval',
                              'doctor_approved',
                              'pending_dispatch',
                              'dispatched_to_clinic',
                              'received_at_clinic',
                              'collected_by_patient',
                              'cancelled'
                          )),
    total_amount      NUMERIC(10, 2) CHECK (total_amount >= 0),
    treatment_plan_id UUID REFERENCES treatment_plans(plan_id) ON DELETE RESTRICT,
    cancelled_by      UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    cancelled_at      TIMESTAMPTZ,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ------------------------------------------------------------
-- order_items — line items within a store order
-- ------------------------------------------------------------
CREATE TABLE order_items (
    item_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id   UUID NOT NULL REFERENCES store_orders(order_id) ON DELETE RESTRICT,
    product_id UUID NOT NULL REFERENCES products(product_id) ON DELETE RESTRICT,
    quantity   INTEGER NOT NULL DEFAULT 1 CHECK (quantity >= 1),
    unit_price NUMERIC(10, 2) NOT NULL CHECK (unit_price >= 0)
);

-- ------------------------------------------------------------
-- inventory — stock levels at each clinic location
-- Main branches hold real stock; individual clinics are transient
-- UNIQUE(product_id, clinic_id) — one row per product per clinic
-- ------------------------------------------------------------
CREATE TABLE inventory (
    inventory_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id   UUID NOT NULL REFERENCES products(product_id) ON DELETE RESTRICT,
    clinic_id    UUID NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    quantity     INTEGER NOT NULL DEFAULT 0 CHECK (quantity >= 0),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (product_id, clinic_id)
);

-- ------------------------------------------------------------
-- stock_transfers — stock movement log
-- from_type: super_admin (central → main branch) or
--            main_branch (main branch → individual clinic)
-- from_clinic_id NULL when from_type='super_admin'
-- order_id set when transfer is fulfilling a patient order;
--          NULL when it is a replenishment transfer
-- ------------------------------------------------------------
CREATE TABLE stock_transfers (
    st_id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id     UUID NOT NULL REFERENCES products(product_id) ON DELETE RESTRICT,
    from_type      TEXT NOT NULL CHECK (from_type IN ('super_admin', 'main_branch')),
    from_clinic_id UUID REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    to_clinic_id   UUID NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    quantity       INTEGER NOT NULL CHECK (quantity >= 1),
    order_id       UUID REFERENCES store_orders(order_id) ON DELETE RESTRICT,
    status         TEXT NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending', 'dispatched', 'received')),
    initiated_by   UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    received_by    UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    notes          TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    dispatched_at  TIMESTAMPTZ,
    received_at    TIMESTAMPTZ,
    CONSTRAINT chk_stock_transfer_from CHECK (
        (from_type = 'super_admin' AND from_clinic_id IS NULL)
        OR (from_type = 'main_branch' AND from_clinic_id IS NOT NULL)
    )
);

-- ------------------------------------------------------------
-- device_assignments — tracks device purchase per patient
-- Created after all treatment sessions in a block complete
-- purchase_status machine: purchase_prompted → pending_payment
--   → purchased → collected
-- order_id set when Receptionist creates the store_order
-- ------------------------------------------------------------
CREATE TABLE device_assignments (
    da_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id      UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    clinic_id       UUID NOT NULL REFERENCES clinics(clinic_id) ON DELETE RESTRICT,
    plan_id         UUID NOT NULL REFERENCES treatment_plans(plan_id) ON DELETE RESTRICT,
    assigned_by     UUID NOT NULL REFERENCES profiles(id) ON DELETE RESTRICT,
    device_type     TEXT NOT NULL,
    purchase_status TEXT NOT NULL DEFAULT 'purchase_prompted'
                        CHECK (purchase_status IN (
                            'purchase_prompted',
                            'pending_payment',
                            'purchased',
                            'collected',
                            'returned'
                        )),
    order_id        UUID REFERENCES store_orders(order_id) ON DELETE RESTRICT,
    prompted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    purchased_at    TIMESTAMPTZ,
    collected_at    TIMESTAMPTZ,
    returned_at     TIMESTAMPTZ,
    returned_by     UUID REFERENCES profiles(id) ON DELETE RESTRICT,
    return_reason   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
