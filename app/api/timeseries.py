"""
Endpoints de séries temporais — NDVI (Landsat, Sentinel-2, MODIS) e Precipitação.

Usa REST API v1 do GEE (ee_compute) para computação assíncrona e paralela,
substituindo .getInfo() por chamadas HTTP não-bloqueantes.

Nota: endpoints NDWI foram separados em timeseries_ndwi.py.
"""
from datetime import datetime, timedelta
from typing import Optional

import ee
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from app.services.timeseries_service import (
    apply_smoothing,
    build_plotly_response,
    build_precipitation_expr,
    parse_smoothing_method,
    process_gee_region_data,
    process_precipitation_data,
)
from app.utils.ee_compute import compute_parallel
from app.utils.smoothing.config import Satellite

router = APIRouter()

# Data de início da coleção COPERNICUS/S2_SR_HARMONIZED (primeira imagem disponível).
# Usada como default de data_inicio para que a série temporal Sentinel-2 cubra todo o histórico.
S2_SR_HARMONIZED_START = "2017-03-28"


def _handle_ee_quota_error(exc: ee.EEException) -> None:
    """Registra rotação de SA quando o erro é de quota/429."""
    error_msg = str(exc).lower()
    if "429" in error_msg or "quota" in error_msg or "too many requests" in error_msg:
        from app.core.gee_auth import get_gee_manager
        mgr = get_gee_manager()
        if mgr:
            mgr.rotate_on_429()


@router.get("/landsat/{lat}/{lon}")
async def timeseries_landsat(
        lat: float,
        lon: float,
        data_inicio: str = Query(None, description="Start date in YYYY-MM-DD format"),
        data_fim: str = Query(None, description="End date in YYYY-MM-DD format"),
        method: Optional[str] = Query(None, description="Smoothing method: raw, savgol, whittaker, spline, loess"),
):
    try:
        if not data_fim:
            data_fim = datetime.now().strftime('%Y-%m-%d')
        if not data_inicio:
            data_inicio = (datetime.now() - timedelta(days=365 * 50)).strftime('%Y-%m-%d')

        smoothing_method = parse_smoothing_method(method)
        point = ee.Geometry.Point([lon, lat])

        def mask(image):
            qa = image.select('QA_PIXEL')
            cloud = qa.bitwiseAnd(1 << 3).eq(0)
            cloud_shadow = qa.bitwiseAnd(1 << 4).eq(0)
            return image.updateMask(cloud).updateMask(cloud_shadow)

        def apply_scale(image):
            optical_bands = image.select(['GREEN', 'RED', 'NIR']) \
                .multiply(0.0000275) \
                .add(-0.2)
            return image.addBands(optical_bands, None, True)

        def calculate_ndvi(image):
            ndvi = image.normalizedDifference(['NIR', 'RED']).rename('NDVI')
            return image.addBands(ndvi)

        l4 = ee.ImageCollection('LANDSAT/LT04/C02/T1_L2') \
            .filterBounds(point).filterDate(data_inicio, data_fim) \
            .select(['SR_B2', 'SR_B3', 'SR_B4', 'QA_PIXEL'], ['GREEN', 'RED', 'NIR', 'QA_PIXEL'])
        l5 = ee.ImageCollection('LANDSAT/LT05/C02/T1_L2') \
            .filterBounds(point).filterDate(data_inicio, data_fim) \
            .select(['SR_B2', 'SR_B3', 'SR_B4', 'QA_PIXEL'], ['GREEN', 'RED', 'NIR', 'QA_PIXEL'])
        l7 = ee.ImageCollection('LANDSAT/LE07/C02/T1_L2') \
            .filterBounds(point).filterDate(data_inicio, data_fim) \
            .select(['SR_B2', 'SR_B3', 'SR_B4', 'QA_PIXEL'], ['GREEN', 'RED', 'NIR', 'QA_PIXEL'])
        l8 = ee.ImageCollection('LANDSAT/LC08/C02/T1_L2') \
            .filterBounds(point).filterDate(data_inicio, data_fim) \
            .select(['SR_B3', 'SR_B4', 'SR_B5', 'QA_PIXEL'], ['GREEN', 'RED', 'NIR', 'QA_PIXEL'])
        l9 = ee.ImageCollection('LANDSAT/LC09/C02/T1_L2') \
            .filterBounds(point).filterDate(data_inicio, data_fim) \
            .select(['SR_B3', 'SR_B4', 'SR_B5', 'QA_PIXEL'], ['GREEN', 'RED', 'NIR', 'QA_PIXEL'])

        collections = l4.merge(l5).merge(l7).merge(l8).merge(l9) \
            .map(mask).map(apply_scale).map(calculate_ndvi) \
            .sort('system:time_start')

        # Construir expressões EE (lazy — sem chamada ao servidor)
        ndvi_expr = collections.select(['NDVI']).getRegion(point, 10)
        precip_expr = build_precipitation_expr(point, data_inicio, data_fim, scale=500)

        # Computar NDVI e precipitação em paralelo via REST API
        ndvi_data, precip_data = await compute_parallel(ndvi_expr, precip_expr)

        ndvi_dates, ndvi_values = process_gee_region_data(ndvi_data, 'NDVI', valid_range=(0, 1))
        ndvi_smoothed = apply_smoothing(ndvi_dates, ndvi_values, Satellite.LANDSAT, smoothing_method)
        precip_dates, precip_values = process_precipitation_data(precip_data)

        plotly_data = build_plotly_response(
            ndvi_dates, ndvi_values, ndvi_smoothed,
            precip_dates, precip_values, smoothing_method,
        )
        return JSONResponse(content=plotly_data)

    except ee.EEException as e:
        _handle_ee_quota_error(e)
        raise HTTPException(status_code=500, detail=f"Error fetching data from Earth Engine: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")


@router.get("/modis/{lat}/{lon}")
async def timeseries_modis(
        lat: float,
        lon: float,
        data_inicio: str = Query(None, description="Start date in YYYY-MM-DD format"),
        data_fim: str = Query(None, description="End date in YYYY-MM-DD format"),
        method: Optional[str] = Query(None, description="Smoothing method: raw, savgol, whittaker, spline, loess"),
):
    try:
        if not data_fim:
            data_fim = datetime.now().strftime('%Y-%m-%d')
        if not data_inicio:
            data_inicio = (datetime.now() - timedelta(days=365 * 20)).strftime('%Y-%m-%d')

        smoothing_method = parse_smoothing_method(method)
        point = ee.Geometry.Point([lon, lat])

        modis = ee.ImageCollection('MODIS/061/MOD13Q1').filterBounds(point).filterDate(data_inicio, data_fim)

        def scale_ndvi(image):
            return image.select('NDVI').multiply(0.0001).copyProperties(image, ['system:time_start'])

        modis_scaled = modis.map(scale_ndvi)

        # Construir expressões (lazy) e computar em paralelo
        ndvi_expr = modis_scaled.getRegion(point, 250)
        precip_expr = build_precipitation_expr(point, data_inicio, data_fim, scale=5000)

        ndvi_data, precip_data = await compute_parallel(ndvi_expr, precip_expr)

        ndvi_dates, ndvi_values = process_gee_region_data(ndvi_data, 'NDVI', valid_range=(-1, 1))
        ndvi_smoothed = apply_smoothing(ndvi_dates, ndvi_values, Satellite.MODIS, smoothing_method)
        precip_dates, precip_values = process_precipitation_data(precip_data)

        plotly_data = build_plotly_response(
            ndvi_dates, ndvi_values, ndvi_smoothed,
            precip_dates, precip_values, smoothing_method,
        )
        return JSONResponse(content=plotly_data)

    except ee.EEException as e:
        _handle_ee_quota_error(e)
        raise HTTPException(status_code=500, detail=f"Error fetching data from Earth Engine: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")


@router.get("/sentinel2/{lat}/{lon}")
async def timeseries_sentinel2(
    lat: float,
    lon: float,
    data_inicio: str = Query(None, description="Start date in YYYY-MM-DD format"),
    data_fim: str = Query(None, description="End date in YYYY-MM-DD format"),
    method: Optional[str] = Query(None, description="Smoothing method: raw, savgol, whittaker, spline, loess"),
):
    try:
        if not data_fim:
            data_fim = datetime.now().strftime('%Y-%m-%d')
        if not data_inicio:
            # Cobre toda a série do S2_SR_HARMONIZED (início da coleção: 2017-03-28).
            # Antes usava janela deslizante de 4,5 anos, que truncava o início em ~2022.
            data_inicio = S2_SR_HARMONIZED_START

        smoothing_method = parse_smoothing_method(method)
        point = ee.Geometry.Point([lon, lat])

        def maskS2clouds(image):
            qa = image.select('QA60')
            cloudBitMask = 1 << 10
            cirrusBitMask = 1 << 11
            mask = qa.bitwiseAnd(cloudBitMask).eq(0).And(qa.bitwiseAnd(cirrusBitMask).eq(0))
            return image.updateMask(mask).divide(10000).copyProperties(image, ["system:time_start"])

        def addNdvi(image):
            ndvi = image.normalizedDifference(['B8', 'B4']).rename('NDVI')
            return image.addBands(ndvi)

        s2 = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
              .filterDate(data_inicio, data_fim)
              .filterBounds(point)
              .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))
              .map(maskS2clouds))

        s2_ndvi = s2.map(addNdvi)

        # Construir expressões (lazy) e computar em paralelo
        ndvi_expr = s2_ndvi.select(['NDVI']).getRegion(point, 10)
        precip_expr = build_precipitation_expr(point, data_inicio, data_fim, scale=5000)

        ndvi_data, precip_data = await compute_parallel(ndvi_expr, precip_expr)

        ndvi_dates, ndvi_values = process_gee_region_data(ndvi_data, 'NDVI')
        ndvi_smoothed = apply_smoothing(ndvi_dates, ndvi_values, Satellite.SENTINEL2, smoothing_method)
        precip_dates, precip_values = process_precipitation_data(precip_data)

        plotly_data = build_plotly_response(
            ndvi_dates, ndvi_values, ndvi_smoothed,
            precip_dates, precip_values, smoothing_method,
        )
        return JSONResponse(content=plotly_data)

    except ee.EEException as e:
        _handle_ee_quota_error(e)
        raise HTTPException(status_code=500, detail=f"Error fetching data from Earth Engine: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")
