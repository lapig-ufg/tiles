import json

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.modules.bdc.admission import admission
from app.modules.bdc.evidence import EvidenceService, create_evidence_router


@pytest.fixture
def evidence(tmp_path):
    (tmp_path / "board.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (tmp_path / "metrics.json").write_text(json.dumps({"ssim": 0.99}))
    (tmp_path / "gallery.html").write_text("<script>alert(1)</script>")
    (tmp_path / ".secret.txt").write_text("private")
    (tmp_path / "credential.env").write_text("private")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "result.csv").write_text("a,b\n1,2")
    service = EvidenceService(tmp_path)
    app = FastAPI()
    app.include_router(create_evidence_router(service), prefix="/evidence")
    return service, TestClient(app)


def test_index_filters_pagination_and_hidden_files(evidence):
    _, client = evidence
    data = client.get("/evidence?limit=2").json()
    assert data["total"] == 4 and len(data["files"]) == 2
    assert data["counts"] == {"image": 1, "data": 2, "document": 1}
    assert client.get("/evidence?kind=image").json()["files"][0]["name"] == "board.png"
    assert client.get("/evidence?q=nested").json()["total"] == 1
    assert client.get("/evidence?limit=101").status_code == 422


@pytest.mark.parametrize(
    "name",
    [
        "../outside.txt",
        "/etc/passwd",
        ".secret.txt",
        "credential.env",
        "nested/../../outside.txt",
    ],
)
def test_files_reject_traversal_and_non_evidence(evidence, name):
    _, client = evidence
    result = client.get("/evidence/file", params={"path": name})
    assert result.status_code == 404
    assert result.headers["cache-control"] == "no-store"


def test_symlinks_are_never_served_or_indexed(evidence, tmp_path):
    service, client = evidence
    (tmp_path / "linked.txt").symlink_to("/etc/passwd")
    assert client.get("/evidence/file?path=linked.txt").status_code == 404
    assert not any(f["name"] == "linked.txt" for f in service.files())


def test_html_is_attachment_and_preview_is_json_text(evidence):
    _, client = evidence
    result = client.get("/evidence/file?path=gallery.html")
    assert result.headers["content-disposition"].startswith("attachment")
    assert result.headers["content-type"] == "application/octet-stream"
    assert result.headers["x-content-type-options"] == "nosniff"
    assert "sandbox" in result.headers["content-security-policy"]
    assert (
        client.get("/evidence/preview?path=gallery.html")
        .json()["text"]
        .startswith("<script>")
    )


def test_preview_has_bounded_size_and_images_inline(evidence, tmp_path):
    _, client = evidence
    (tmp_path / "large.txt").write_text("x" * 300000)
    data = client.get("/evidence/preview?path=large.txt").json()
    assert data["truncated"] and len(data["text"]) == 262144
    result = client.get("/evidence/file?path=board.png")
    assert result.headers["content-type"] == "image/png"
    assert result.headers["content-disposition"].startswith("inline")


def test_missing_evidence_root_is_empty(tmp_path):
    service = EvidenceService(tmp_path / "absent")
    assert service.files() == [] and service.summary() == {}


def test_admission_shared_between_instances_and_released(tmp_path):
    with admission(tmp_path, 1):
        with pytest.raises(HTTPException) as exc:
            with admission(tmp_path, 1):
                pass
        assert exc.value.status_code == 503
    with admission(tmp_path, 1):
        pass


def test_comparisons_pair_records_and_distinguish_missing_data(evidence, tmp_path):
    _, client = evidence
    campaign = tmp_path / "campaign"
    campaign.mkdir()
    record = dict(
        source="landsat-2",
        region="cerrado-goias",
        year=2020,
        month=8,
        period="DRY",
        visparam="landsat-tvi-true",
        stage="scene",
        z=14,
    )
    for name, valid, board in [
        ("valid", 42, True),
        ("empty", 0, True),
        ("missing", 0, False),
    ]:
        (campaign / f"{name}.json").write_text(
            json.dumps(
                dict(
                    record,
                    metrics={
                        "valid_pixels": valid,
                        "mae": [1, 2, 3] if valid else None,
                    },
                )
            )
        )
        if board:
            (campaign / f"{name}.png").write_bytes(b"png")
    (campaign / "broken.json").write_text("{broken")
    result = client.get("/evidence/comparisons?limit=1").json()
    assert result["total"] == 3
    assert result["comparisons"][0]["id"] == "valid"
    assert result["comparisons"][0]["board"]["path"] == "campaign/valid.png"
    assert result["counts"] == {"measured": 1, "no_overlap": 1, "unavailable": 1}
    assert (
        client.get("/evidence/comparisons?status=no_overlap").json()["comparisons"][0][
            "id"
        ]
        == "empty"
    )
    assert client.get("/evidence/comparisons?source=S2_L2A-1").json()["total"] == 0
    assert (
        client.get("/evidence/comparisons?region=cerrado-goias&period=DRY").json()[
            "total"
        ]
        == 3
    )
    assert client.get("/evidence/comparisons?limit=25").status_code == 422
