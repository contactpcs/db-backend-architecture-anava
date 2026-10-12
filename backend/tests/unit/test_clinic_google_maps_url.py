import pytest
from pydantic import ValidationError

from app.modules.admin.schemas import ClinicRead, ClinicUpdate
from app.modules.patients.schemas import PatientClinicRead


def test_https_link_accepted():
    url = "https://maps.app.goo.gl/AbCdEf123"
    assert ClinicUpdate(google_maps_url=url).google_maps_url == url


def test_blank_link_accepted_so_the_form_can_clear_it():
    assert ClinicUpdate(google_maps_url="").google_maps_url == ""


@pytest.mark.parametrize("url", ["javascript:alert(1)", "http://maps.google.com/x", "maps.google.com/x", "https://", "https://a b"])
def test_non_https_link_rejected(url):
    with pytest.raises(ValidationError):
        ClinicUpdate(google_maps_url=url)


@pytest.mark.parametrize("column", ["google_maps_url", "full_address", "pincode"])
def test_read_tolerates_row_without_the_column(column):
    """A clinics row read before alembic 0061 is applied has no such key."""
    assert not ClinicRead.model_fields[column].is_required()
    assert not PatientClinicRead.model_fields[column].is_required()
    assert column in ClinicUpdate.model_fields


def test_patient_clinic_read_exposes_contact_details_only():
    row = {
        "clinic_id": "6932a589-00ff-4aa7-9505-3fa19d40f010",
        "clinic_name": "Anava Indiranagar",
        "address": "12 MG Road",
        "city": "Bengaluru",
        "state": "Karnataka",
        "phone": "080-1234",
        "email": "blr@example.com",
        # internal columns of the clinics row that must not reach a patient
        "clinic_admin_id": "6932a589-00ff-4aa7-9505-3fa19d40f011",
        "region_id": "6932a589-00ff-4aa7-9505-3fa19d40f012",
        "status": "active",
    }
    out = PatientClinicRead.model_validate(row).model_dump()
    assert set(out) == {
        "clinic_id", "clinic_name", "full_address", "address", "city", "state", "pincode", "phone", "email", "google_maps_url",
    }  # fmt: skip
    assert out["full_address"] is None and out["pincode"] is None
