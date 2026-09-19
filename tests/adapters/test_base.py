"""
No API key / network needed — pure dataclass and capability-error shape tests.
"""
from adapters.base import (
    DeviceInfo, JobSubmission, JobStatusInfo, JobResultData, AdapterCapabilityError,
)


def test_device_info_to_dict_omits_none_fields():
    d = DeviceInfo(name="ibm_fez", provider="ibm", num_qubits=156, operational=True)
    out = d.to_dict()
    assert out == {"name": "ibm_fez", "provider": "ibm", "num_qubits": 156, "operational": True}
    assert "technology" not in out  # IonQ-only field, left None, must not appear


def test_device_info_reconciles_ionq_shaped_fields():
    d = DeviceInfo(name="qpu.forte-1", provider="ionq", num_qubits=36, technology="trapped-ion")
    out = d.to_dict()
    assert out["technology"] == "trapped-ion"
    assert "operational" not in out  # IBM-only field, left None


def test_job_submission_to_dict_omits_none_fields():
    j = JobSubmission(job_id="abc123", provider="ionq", backend="qpu.forte-1", status="SUBMITTED", shots=1024)
    out = j.to_dict()
    assert out["job_id"] == "abc123"
    assert "self_check" not in out


def test_job_status_info_and_job_result_data_omit_none_fields():
    s = JobStatusInfo(job_id="abc", provider="ibm", status="DONE")
    assert "queue_position" not in s.to_dict()

    r = JobResultData(job_id="abc", provider="ibm", status="DONE", counts={"00": 512, "11": 512})
    assert r.to_dict()["counts"] == {"00": 512, "11": 512}
    assert "total_shots" not in r.to_dict()


def test_adapter_capability_error_names_provider_and_capability():
    err = AdapterCapabilityError("ibm", "estimate_gates")
    assert err.provider_name == "ibm"
    assert err.capability == "estimate_gates"
    assert "ibm" in str(err) and "estimate_gates" in str(err)
