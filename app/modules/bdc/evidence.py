import json
import os
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse


class EvidenceService:
    extensions = {".png", ".jpg", ".jpeg", ".json", ".csv", ".txt", ".html", ".md"}

    def __init__(self, root):
        self.root = Path(root).resolve()
        self._lock = threading.Lock()
        self._files = []
        self._expires = 0

    def path(self, name):
        relative = Path(name)
        if relative.is_absolute() or any(p.startswith(".") for p in relative.parts):
            raise HTTPException(404, "Arquivo não encontrado")
        candidate = self.root / relative
        if any(
            p.is_symlink() for p in [candidate, *candidate.parents] if p != self.root
        ):
            raise HTTPException(404, "Arquivo não encontrado")
        if (
            not candidate.is_file()
            or candidate.suffix.lower() not in self.extensions
            or not candidate.resolve().is_relative_to(self.root)
        ):
            raise HTTPException(404, "Arquivo não encontrado")
        return candidate

    def files(self):
        with self._lock:
            if time.monotonic() < self._expires:
                return self._files
            result = []
            for directory, dirs, files in os.walk(self.root, followlinks=False):
                dirs[:] = sorted(
                    d
                    for d in dirs
                    if not d.startswith(".") and not (Path(directory) / d).is_symlink()
                )
                for name in files:
                    path = Path(directory) / name
                    if (
                        name.startswith(".")
                        or path.is_symlink()
                        or path.suffix.lower() not in self.extensions
                    ):
                        continue
                    stat = path.stat()
                    relative = path.relative_to(self.root).as_posix()
                    kind = (
                        "image"
                        if path.suffix.lower() in {".png", ".jpg", ".jpeg"}
                        else "data"
                        if path.suffix.lower() in {".json", ".csv"}
                        else "document"
                    )
                    title = name
                    experiment = path.with_suffix(".json")
                    if (
                        path.parent.name == "campaign"
                        and experiment.is_file()
                        and not experiment.is_symlink()
                        and experiment.stat().st_size < 100000
                    ):
                        try:
                            record = json.loads(experiment.read_text())
                            title = (
                                " | ".join(
                                    str(record[key])
                                    for key in (
                                        "source",
                                        "region",
                                        "year",
                                        "period",
                                        "visparam",
                                        "z",
                                        "stage",
                                    )
                                    if key in record
                                )
                                or name
                            )
                        except (ValueError, OSError):
                            pass
                    result.append(
                        dict(
                            title=title,
                            path=relative,
                            name=name,
                            kind=kind,
                            size=stat.st_size,
                            modified=stat.st_mtime,
                            group=relative.split("/")[0]
                            if "/" in relative
                            else "Visão geral",
                        )
                    )
            self._files = sorted(
                result,
                key=lambda f: (
                    0
                    if f["name"] == "pilot-board.png"
                    else 1
                    if "board" in f["name"] or "calibrated" in f["name"]
                    else 2,
                    f["path"],
                ),
            )
            self._expires = time.monotonic() + 10
            return self._files

    def comparisons(self):
        files = {f["path"]: f for f in self.files()}
        results = []
        for name, file in files.items():
            if (
                not name.startswith("campaign/")
                or not name.endswith(".json")
                or file["size"] > 100000
            ):
                continue
            try:
                record = json.loads(self.path(name).read_text())
            except (ValueError, OSError, HTTPException):
                continue
            if not isinstance(record, dict) or not all(
                k in record
                for k in ("source", "region", "year", "period", "visparam", "stage")
            ):
                continue
            metrics = record.get("metrics") or {}
            if not isinstance(metrics, dict):
                metrics = {}
            valid = metrics.get("valid_pixels", 0)
            measured = (
                isinstance(valid, (int, float))
                and valid > 0
                and isinstance(metrics.get("mae"), list)
            )
            board = files.get(str(Path(name).with_suffix(".png")))
            results.append(
                dict(
                    id=Path(name).stem,
                    **{
                        k: record.get(k)
                        for k in (
                            "source",
                            "region",
                            "year",
                            "month",
                            "period",
                            "visparam",
                            "stage",
                            "z",
                        )
                    },
                    status="measured"
                    if measured
                    else "no_overlap"
                    if board and metrics.get("valid_pixels") == 0
                    else "unavailable",
                    reason=record.get("reason")
                    or record.get("error")
                    or metrics.get("status")
                    or record.get("status"),
                    metrics=metrics,
                    board=board,
                    data=file,
                    reference_format=record.get("reference_format"),
                )
            )
        return sorted(
            results,
            key=lambda r: (
                r["status"] != "measured",
                r["source"],
                r["region"],
                str(r["year"]),
                r["period"],
                str(r["z"]),
                r["id"],
            ),
        )

    def preview(self, name):
        path = self.path(name)
        if path.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            raise HTTPException(422, "Utilize a visualização de imagem")
        with path.open("rb") as stream:
            data = stream.read(262145)
        text = data[:262144].decode("utf-8", errors="replace")
        if path.suffix == ".json" and len(data) <= 262144:
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            except ValueError:
                pass
        return dict(text=text, truncated=len(data) > 262144)

    def summary(self):
        path = self.root / "summary.json"
        if not path.exists():
            return {}
        path = self.path("summary.json")
        if path.stat().st_size > 2_000_000:
            return {}
        try:
            value = json.loads(path.read_text())
            return {
                key: value.get(key, {})
                for key in ("counts", "reference_formats", "groups")
            }
        except (ValueError, OSError):
            return {}


def create_evidence_router(service=None):
    service = service or EvidenceService(
        os.environ.get("BDC_EVIDENCE_DIR", "poc/bdc_cog/evidence")
    )
    from .http import BdcRoute

    router = APIRouter(route_class=BdcRoute)

    @router.get("")
    def index(
        q: str = Query("", max_length=200),
        kind: str = Query("all", pattern="^(all|image|data|document)$"),
        page: int = Query(1, ge=1),
        limit: int = Query(24, ge=1, le=100),
    ):
        files = service.files()
        selected = [
            f
            for f in files
            if (kind == "all" or f["kind"] == kind)
            and q.casefold() in (f["path"] + " " + f["title"]).casefold()
        ]
        return dict(
            files=selected[(page - 1) * limit : page * limit],
            total=len(selected),
            page=page,
            limit=limit,
            counts={
                k: sum(f["kind"] == k for f in files)
                for k in ("image", "data", "document")
            },
            available=service.root.is_dir(),
        )

    @router.get("/comparisons")
    def comparisons(
        source: str = Query("", max_length=100),
        region: str = Query("", max_length=100),
        period: str = Query("", max_length=20),
        status: str = Query("", pattern="^(|measured|no_overlap|unavailable)$"),
        page: int = Query(1, ge=1),
        limit: int = Query(6, ge=1, le=24),
    ):
        all_results = service.comparisons()
        selected = [
            r
            for r in all_results
            if all(
                not value or r[key] == value
                for key, value in dict(
                    source=source, region=region, period=period, status=status
                ).items()
            )
        ]
        return dict(
            comparisons=selected[(page - 1) * limit : page * limit],
            total=len(selected),
            available=service.root.is_dir(),
            options={
                key: sorted({r[key] for r in all_results})
                for key in ("source", "region", "period")
            },
            counts={
                key: sum(r["status"] == key for r in all_results)
                for key in ("measured", "no_overlap", "unavailable")
            },
        )

    @router.get("/summary")
    def summary():
        return service.summary()

    @router.get("/preview")
    def preview(path: str = Query(max_length=500)):
        return service.preview(path)

    @router.get("/file")
    def file(path: str = Query(max_length=500), download: bool = False):
        file = service.path(path)
        image = file.suffix.lower() in {".png", ".jpg", ".jpeg"}
        return FileResponse(
            file,
            filename=file.name,
            media_type=("image/png" if file.suffix.lower() == ".png" else "image/jpeg")
            if image
            else "application/octet-stream",
            content_disposition_type="inline"
            if image and not download
            else "attachment",
            headers={
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
                "Cache-Control": "private, max-age=60",
            },
        )

    return router
