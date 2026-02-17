"""
Pipeline GEE para o dataset GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL.
64 bandas (A00-A63), 10m resolucao, anual 2017-2024.

Funcoes puras: recebem params e retornam objetos ee.* (sem .getInfo()).
"""
from __future__ import annotations

from typing import Dict, List, Optional

import ee

from app.core.config import logger


COLLECTION_ID = "GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL"
BAND_NAMES = [f"A{i:02d}" for i in range(64)]


# --------------------------------------------------------------------------- #
# Image base                                                                   #
# --------------------------------------------------------------------------- #

def build_embedding_image(year: int, roi: ee.Geometry) -> ee.Image:
    """Carrega e mosaica embeddings para ano/ROI."""
    return (
        ee.ImageCollection(COLLECTION_ID)
        .filter(ee.Filter.date(f"{year}-01-01", f"{year + 1}-01-01"))
        .filter(ee.Filter.bounds(roi))
        .mosaic()
        .clip(roi)
    )


# --------------------------------------------------------------------------- #
# Produtos derivados                                                           #
# --------------------------------------------------------------------------- #

def derive_rgb(
    image: ee.Image,
    bands: List[int],
    roi: ee.Geometry,
    scale: int,
) -> ee.Image:
    """Mapeia 3 dimensoes do embedding em R/G/B com normalizacao p2/p98."""
    band_names = [f"A{b:02d}" for b in bands]
    selected = image.select(band_names)

    stats = selected.reduceRegion(
        reducer=ee.Reducer.percentile([2, 98]),
        geometry=roi,
        scale=scale,
        bestEffort=True,
        tileScale=4,
    )

    normalized_bands = []
    for name in band_names:
        p2 = ee.Number(stats.get(f"{name}_p2"))
        p98 = ee.Number(stats.get(f"{name}_p98"))
        band = selected.select(name)
        diff = p98.subtract(p2)
        # Evita divisao por zero: ee.Algorithms.If retorna ComputedObject, cast para ee.Number
        safe_diff = ee.Number(ee.Algorithms.If(diff.gt(0), diff, ee.Number(1)))
        norm = band.subtract(p2).divide(safe_diff).clamp(0, 1).multiply(255)
        normalized_bands.append(norm)

    return ee.Image.cat(normalized_bands).toUint8().rename(["R", "G", "B"])


def derive_pca(
    image: ee.Image,
    roi: ee.Geometry,
    n_components: int,
    scale: int,
    sample_size: int,
) -> ee.Image:
    """PCA via centeredCovariance + eigen decomposition."""
    arrays = image.toArray()

    covar = arrays.reduceRegion(
        reducer=ee.Reducer.centeredCovariance(),
        geometry=roi,
        scale=scale,
        maxPixels=1_000_000_000,
        tileScale=4,
        bestEffort=True,
    )

    covar_array = ee.Array(covar.get("array"))
    eigens = covar_array.eigen()
    eigen_vectors = eigens.slice(1, 1)

    # Projetar nos n_components primeiros eigenvectors
    eigen_vectors_top = eigen_vectors.slice(0, 0, n_components)
    projected = arrays.arrayRepeat(1, 1).arrayTranspose().matrixMultiply(
        eigen_vectors_top.transpose()
    )

    pc_image = (
        projected.arrayProject([0]).arrayFlatten(
            [["PC" + str(i) for i in range(n_components)]]
        )
    )

    # Normalizar cada PC para visualizacao (p2/p98 -> 0-255)
    stats = pc_image.reduceRegion(
        reducer=ee.Reducer.percentile([2, 98]),
        geometry=roi,
        scale=scale,
        bestEffort=True,
        tileScale=4,
    )

    normalized_pcs = []
    for i in range(min(n_components, 3)):
        name = f"PC{i}"
        p2 = ee.Number(stats.get(f"{name}_p2"))
        p98 = ee.Number(stats.get(f"{name}_p98"))
        band = pc_image.select(name)
        diff = p98.subtract(p2)
        safe_diff = ee.Number(ee.Algorithms.If(diff.gt(0), diff, ee.Number(1)))
        norm = band.subtract(p2).divide(safe_diff).clamp(0, 1).multiply(255)
        normalized_pcs.append(norm)

    return ee.Image.cat(normalized_pcs).toUint8().rename(["R", "G", "B"][:len(normalized_pcs)])


def derive_clusters(
    image: ee.Image,
    roi: ee.Geometry,
    k: int,
    scale: int,
    sample_size: int,
) -> ee.Image:
    """KMeans clustering via ee.Clusterer.wekaKMeans."""
    training = image.sample(
        region=roi, scale=scale, numPixels=sample_size, seed=42
    )
    clusterer = ee.Clusterer.wekaKMeans(nClusters=k).train(training)
    return image.cluster(clusterer).rename("cluster")


def derive_magnitude(image: ee.Image) -> ee.Image:
    """Magnitude do vetor embedding (sqrt da soma dos quadrados)."""
    return image.pow(2).reduce(ee.Reducer.sum()).sqrt().rename("magnitude")


def derive_change_detection(
    year_a: int, year_b: int, roi: ee.Geometry
) -> ee.Image:
    """Cosine similarity entre dois anos (dot product para vetores unitarios)."""
    img_a = build_embedding_image(year_a, roi)
    img_b = build_embedding_image(year_b, roi)
    return img_a.multiply(img_b).reduce(ee.Reducer.sum()).rename("similarity")


# --------------------------------------------------------------------------- #
# Stats                                                                        #
# --------------------------------------------------------------------------- #

def compute_stats(image: ee.Image, roi: ee.Geometry, scale: int) -> ee.Dictionary:
    """Estatisticas via reduceRegion (sem .getInfo())."""
    return image.reduceRegion(
        reducer=(
            ee.Reducer.mean()
            .combine(ee.Reducer.stdDev(), sharedInputs=True)
            .combine(ee.Reducer.minMax(), sharedInputs=True)
            .combine(ee.Reducer.count(), sharedInputs=True)
        ),
        geometry=roi,
        scale=scale,
        bestEffort=True,
        tileScale=4,
    )


# --------------------------------------------------------------------------- #
# Map ID (sync - rodar no ThreadPoolExecutor)                                  #
# --------------------------------------------------------------------------- #

def get_map_id(image: ee.Image, vis_params: dict) -> dict:
    """Gera mapId + tileUrl. Chamada sincrona, rodar via run_in_executor."""
    map_info = ee.data.getMapId({"image": image.serialize(), **vis_params})
    return {
        "url": map_info["tile_fetcher"].url_format,
        "mapid": map_info.get("mapid", ""),
    }


def get_map_id_from_image(image: ee.Image, vis_params: dict) -> str:
    """Retorna apenas a URL template para tiles. Sync."""
    map_info = ee.data.getMapId({"image": image, **vis_params})
    return map_info["tile_fetcher"].url_format


# --------------------------------------------------------------------------- #
# Build product image                                                          #
# --------------------------------------------------------------------------- #

def build_product_image(
    year: int,
    roi: ee.Geometry,
    product: str,
    *,
    rgb_bands: Optional[List[int]] = None,
    pca_components: int = 3,
    kmeans_k: int = 8,
    scale: int = 10,
    sample_size: int = 5000,
    year_b: Optional[int] = None,
) -> ee.Image:
    """Constroi a imagem derivada com base no tipo de produto."""
    base = build_embedding_image(year, roi)

    if product == "rgb_embedding":
        bands = rgb_bands or [0, 16, 9]
        return derive_rgb(base, bands, roi, scale)
    elif product == "pca":
        return derive_pca(base, roi, pca_components, scale, sample_size)
    elif product == "clusters":
        return derive_clusters(base, roi, kmeans_k, scale, sample_size)
    elif product == "magnitude":
        return derive_magnitude(base)
    elif product == "change_detection":
        if year_b is None:
            raise ValueError("year_b obrigatorio para change_detection")
        return derive_change_detection(year, year_b, roi)
    else:
        raise ValueError(f"Produto desconhecido: {product}")


def build_vis_params(
    product: str,
    *,
    palette: Optional[List[str]] = None,
    vis_min: float = -0.3,
    vis_max: float = 0.3,
    kmeans_k: int = 8,
) -> dict:
    """Monta vis_params para o getMapId baseado no tipo de produto."""
    if product in ("rgb_embedding", "pca"):
        return {"min": 0, "max": 255}
    elif product == "clusters":
        pal = palette or [
            "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728",
            "#9467bd", "#8c564b", "#e377c2", "#7f7f7f",
            "#bcbd22", "#17becf", "#aec7e8", "#ffbb78",
        ]
        return {"min": 0, "max": kmeans_k - 1, "palette": pal[:kmeans_k]}
    elif product == "magnitude":
        return {"min": 0.8, "max": 1.2, "palette": ["blue", "green", "yellow", "red"]}
    elif product == "change_detection":
        return {"min": 0.5, "max": 1.0, "palette": ["red", "yellow", "green"]}
    else:
        return {"min": vis_min, "max": vis_max}


# --------------------------------------------------------------------------- #
# Quantizacao para export                                                      #
# --------------------------------------------------------------------------- #

def quantize_for_export(image: ee.Image) -> ee.Image:
    """Quantiza embeddings float para int8 para export eficiente."""
    sat = image.abs().pow(0.5).multiply(image.signum())
    return sat.multiply(127.5).clamp(-127, 127).int8()
