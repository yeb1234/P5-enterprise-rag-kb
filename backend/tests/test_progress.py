"""层级进度模型：阶段权重、体积加权、单调性。"""
from app.core.constants import IngestStage
from app.services.progress import compute_batch_progress, compute_document_progress


def test_stage_progress_is_monotonic():
    values = [
        compute_document_progress(IngestStage.QUEUED.value, 0.0),
        compute_document_progress(IngestStage.PARSE.value, 0.5),
        compute_document_progress(IngestStage.PARSE.value, 1.0),
        compute_document_progress(IngestStage.CHUNK.value, 1.0),
        compute_document_progress(IngestStage.EMBED.value, 0.5),
        compute_document_progress(IngestStage.INDEX.value, 1.0),
        compute_document_progress(IngestStage.DONE.value, 1.0),
    ]
    assert values == sorted(values), values
    assert values[0] == 20.0          # 分片传完即 20%
    assert values[-1] == 100.0


def test_upload_phase_is_20_percent():
    assert compute_document_progress("queued", 0, upload_progress=0.0) == 0.0
    assert compute_document_progress("queued", 0, upload_progress=0.5) == 10.0
    assert compute_document_progress("queued", 0, upload_progress=1.0) == 20.0


def test_batch_progress_weighted_by_file_size():
    """500MB 文件不该和 1KB 文件各占一半进度。"""
    files = [
        {"size_bytes": 500 * 1024 * 1024, "progress": 0.0, "status": "running"},
        {"size_bytes": 1024, "progress": 100.0, "status": "succeeded"},
    ]
    assert compute_batch_progress(files) < 0.1

    files[0]["progress"] = 100.0
    assert compute_batch_progress(files) > 99.9


def test_cancelled_files_excluded():
    files = [
        {"size_bytes": 100, "progress": 100.0, "status": "succeeded"},
        {"size_bytes": 999999, "progress": 0.0, "status": "cancelled"},
    ]
    assert compute_batch_progress(files) == 100.0


def test_empty_batch():
    assert compute_batch_progress([]) == 0.0
