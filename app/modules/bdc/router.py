import logging
import os
import threading
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .admission import admission
from .http import BdcRoute
from .selection_jobs import OriginBusy
from .service import BdcService
from .transport import ServicePaused


class WarmRequest(BaseModel):
    collection: str = Field(min_length=1, max_length=180)
    item: str = Field(min_length=1, max_length=180)
    profile: str = Field(min_length=1, max_length=100)
    format: Literal["png", "jpeg"] = "jpeg"
    quality: int = Field(default=90, ge=50, le=100)
    background: Literal["white", "black"] = "white"
    tiles: list[tuple[int, int, int]] = Field(min_length=1, max_length=9)


def create_router(service=None):
    service = service or BdcService(Path(os.environ.get("BDC_CACHE_DIR", "cache/bdc")))
    app = APIRouter(route_class=BdcRoute)
    slots = threading.BoundedSemaphore(2)

    def run(operation, lookup=None):
        acquired = False
        try:
            if lookup is not None:
                cached = lookup()
                if cached is not None:
                    return cached
            acquired = slots.acquire(blocking=False)
            if not acquired:
                raise HTTPException(
                    503,
                    'Laboratório ocupado. Aguarde as leituras em andamento.',
                    headers={'Retry-After': '2'},
                )
            with admission(
                getattr(
                    service, "root", Path(os.environ.get("BDC_CACHE_DIR", "cache/bdc"))
                )
            ):
                return operation()
        except OriginBusy as exc:
            raise HTTPException(
                503,
                "Origem BDC ocupada. Aguarde as leituras em andamento.",
                headers={"Retry-After": "2", "Cache-Control": "no-store"},
            ) from exc
        except ServicePaused as exc:
            raise HTTPException(
                503,
                "Origem BDC pausada após indisponibilidade. O cache permanece disponível.",
            ) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except HTTPException:
            raise
        except Exception as exc:
            logging.getLogger(__name__).exception("Falha na leitura BDC")
            root = getattr(service, "root", None)
            if root and (root / "http/data.inpe.br.paused").exists():
                raise HTTPException(
                    503,
                    "Origem BDC pausada. Nenhuma retentativa automática será realizada.",
                ) from exc
            raise HTTPException(
                502,
                "Não foi possível ler este recurso do BDC. Consulte os arquivos locais de diagnóstico.",
            ) from exc
        finally:
            if acquired:
                slots.release()

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "service": "bdc",
            "formats": ["png", "jpeg"],
            "max_parallel_renders": 2,
        }

    @app.get("/collections")
    def collections():
        return run(service.catalog)

    @app.get("/search")
    def search(
        collection: str,
        bbox: str,
        start: str,
        end: str,
        page: int = Query(1, ge=1, le=1000),
        limit: int = Query(12, ge=1, le=24),
        max_cloud: int = Query(100, ge=0, le=100),
    ):
        def execute():
            try:
                bounds = [float(v) for v in bbox.split(",")]
            except ValueError as exc:
                raise ValueError("Área inválida") from exc
            return service.search(
                collection, bounds, start, end, page, limit, max_cloud
            )

        return run(execute)

    @app.get("/best-images")
    def best_images(
        collection: Literal["landsat-2", "S2_L2A-1"],
        start: str,
        end: str,
        lon: float = Query(..., ge=-180, le=180),
        lat: float = Query(..., ge=-80, le=80),
        radius_m: float = Query(1000, ge=100, le=5000),
        criterion: Literal["cloud", "unusable"] = "cloud",
    ):
        return run(lambda: service.best_images(collection, lon, lat, radius_m, start, end, criterion))

    @app.get("/items/{collection}/{item}")
    def item(collection: str, item: str):
        return run(lambda: service.detail(collection, item))

    @app.get("/tiles/{collection}/{item}/{z}/{x}/{y}.{format_}")
    def tile(
        collection: str,
        item: str,
        z: int,
        x: int,
        y: int,
        format_: Literal["png", "jpeg"],
        profile: str,
        quality: int = Query(90, ge=50, le=100),
        background: Literal["white", "black"] = "white",
    ):
        data, stats = run(
            lambda: service.render(
                collection, item, z, x, y, profile, format_, quality, background
            ),
            lookup=lambda: service.read_cached_tile(
                collection, item, z, x, y, profile, format_, quality, background
            ),
        )
        return Response(
            data,
            media_type="image/" + format_,
            headers={
                "Cache-Control": "private, max-age=3600",
                "ETag": '"' + stats["key"] + '"',
                "X-Cache": stats["cache"],
                "X-Shared-Cache": stats.get("shared_cache", "NOT_APPLICABLE"),
                **({"X-Shared-Cache-Reason": stats["shared_cache_reason"]} if stats.get("shared_cache_reason") else {}),
                "X-Render-Ms": f"{stats['ms']:.2f}",
                "X-Range-Requests": str(stats["ranges"]),
                "X-Origin-Bytes": str(stats["bytes"]),
                "X-Content-Type-Options": "nosniff",
                "X-NoData-Background": background
                if format_ == "jpeg"
                else "transparent",
            },
        )

    @app.post("/prewarm")
    def prewarm(body: WarmRequest):
        def execute():
            from .catalog import tile_region

            tiles = list(dict.fromkeys(body.tiles))
            for x, y, z in tiles:
                tile_region(x, y, z)
            results = []
            for x, y, z in tiles:
                _, stats = service.render(
                    body.collection,
                    body.item,
                    z,
                    x,
                    y,
                    body.profile,
                    body.format,
                    body.quality,
                    body.background,
                )
                results.append(dict(x=x, y=y, z=z, **stats))
            return {"completed": len(results), "results": results}

        return run(execute)

    from .evidence import create_evidence_router
    from .selection_jobs import create_selection_router

    app.include_router(create_evidence_router(), prefix="/evidence")
    app.include_router(create_selection_router())
    return app


router = create_router()
