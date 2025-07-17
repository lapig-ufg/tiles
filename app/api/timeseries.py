from datetime import datetime, timedelta
import numpy as np
import pandas as pd
from scipy.signal import savgol_filter
import ee
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/landsat/{lat}/{lon}")
def timeseries_landsat(
        lat: float,
        lon: float,
        data_inicio: str = Query(None, description="Start date in YYYY-MM-DD format"),
        data_fim: str = Query(None, description="End date in YYYY-MM-DD format")
):
    try:
        if not data_fim:
            data_fim = datetime.now().strftime('%Y-%m-%d')
        if not data_inicio:
            data_inicio = (datetime.now() - timedelta(days=365 * 50)).strftime('%Y-%m-%d')

        point = ee.Geometry.Point([lon, lat])

        def mask(image):
            qa = image.select('QA_PIXEL')
            # Mantem pixels onde a confianca de nuvem e sombra de nuvem é no máximo média (valores 0 e 1)
            cloud = qa.bitwiseAnd(1 << 3).eq(0)  # Bit 3: Cloud
            cloud_shadow = qa.bitwiseAnd(1 << 4).eq(0) # Bit 4: Cloud Shadow
            return image.updateMask(cloud).updateMask(cloud_shadow)

        def apply_scale(image):
            optical_bands = image.select(['RED', 'NIR']) \
                .multiply(0.0000275) \
                .add(-0.2)
            return image.addBands(optical_bands, None, True)

        def calculate_ndvi(image):
            ndvi = image.normalizedDifference(['NIR', 'RED']).rename('NDVI')
            return image.addBands(ndvi)

        l4 = ee.ImageCollection('LANDSAT/LT04/C02/T1_L2').select(['SR_B3', 'SR_B4', 'QA_PIXEL'],
                                                                 ['RED', 'NIR', 'QA_PIXEL'])
        l5 = ee.ImageCollection('LANDSAT/LT05/C02/T1_L2').select(['SR_B3', 'SR_B4', 'QA_PIXEL'],
                                                                 ['RED', 'NIR', 'QA_PIXEL'])
        l7 = ee.ImageCollection('LANDSAT/LE07/C02/T1_L2').select(['SR_B3', 'SR_B4', 'QA_PIXEL'],
                                                                 ['RED', 'NIR', 'QA_PIXEL'])
        l8 = ee.ImageCollection('LANDSAT/LC08/C02/T1_L2').select(['SR_B4', 'SR_B5', 'QA_PIXEL'],
                                                                 ['RED', 'NIR', 'QA_PIXEL'])
        l9 = ee.ImageCollection('LANDSAT/LC09/C02/T1_L2').select(['SR_B4', 'SR_B5', 'QA_PIXEL'],
                                                                 ['RED', 'NIR', 'QA_PIXEL'])

        collections = l4.merge(l5).merge(l7).merge(l8).merge(l9) \
            .filterBounds(point) \
            .filterDate(data_inicio, data_fim) \
            .map(mask) \
            .map(apply_scale) \
            .map(calculate_ndvi) \
            .sort('system:time_start')

        # Otimização: Usar getRegion para extrair todos os dados de uma vez
        ndvi_data = collections.select(['NDVI']).getRegion(point, 10).getInfo()

        if not ndvi_data or len(ndvi_data) <= 1:
            ndvi_dates, ndvi_values = [], []
        else:
            # Processar o resultado do getRegion
            header = ndvi_data[0]
            time_index = header.index('time')
            ndvi_index = header.index('NDVI')
            
            # Extrai e filtra valores nulos
            processed_data = [
                (row[time_index], row[ndvi_index]) 
                for row in ndvi_data[1:] if row[ndvi_index] is not None
            ]
            
            if not processed_data:
                 ndvi_dates, ndvi_values = [], []
            else:
                # Converte timestamp para data e agrupa pela data, tirando a média
                df = pd.DataFrame(processed_data, columns=['time', 'NDVI'])
                df['date'] = pd.to_datetime(df['time'], unit='ms').dt.strftime('%Y-%m-%d')
                
                # Agrupa por data e calcula a média do NDVI
                ndvi_df_grouped = df.groupby('date')['NDVI'].mean().reset_index()
                
                # Filtra NDVI fora do range válido
                ndvi_df_grouped = ndvi_df_grouped[
                    (ndvi_df_grouped['NDVI'] >= 0) & (ndvi_df_grouped['NDVI'] <= 1)
                ]
                
                ndvi_dates = ndvi_df_grouped['date'].tolist()
                ndvi_values = ndvi_df_grouped['NDVI'].tolist()

        def apply_savgol_filter(values, window_size=11, poly_order=2):
            if len(values) > window_size:
                return savgol_filter(values, window_length=window_size, polyorder=poly_order)
            return values

        ndvi_values_smoothed = apply_savgol_filter(np.array(ndvi_values))

        chirps = ee.ImageCollection('UCSB-CHG/CHIRPS/DAILY').filterDate(data_inicio, data_fim).filterBounds(point)

        # Otimização para precipitação também
        precip_data = chirps.getRegion(point, 500).getInfo()

        if not precip_data or len(precip_data) <= 1:
            precip_dates, precip_values = [], []
        else:
            header = precip_data[0]
            time_index = header.index('time')
            precip_index = header.index('precipitation')

            processed_precip = [
                (row[time_index], row[precip_index])
                for row in precip_data[1:] if row[precip_index] is not None
            ]
            if not processed_precip:
                precip_dates, precip_values = [], []
            else:
                precip_df = pd.DataFrame(processed_precip, columns=['time', 'precipitation'])
                precip_df['date'] = pd.to_datetime(precip_df['time'], unit='ms').dt.strftime('%Y-%m-%d')
                
                precip_df_grouped = precip_df.groupby('date')['precipitation'].sum().reset_index()

                precip_dates = precip_df_grouped['date'].tolist()
                precip_values = precip_df_grouped['precipitation'].tolist()

        plotly_data = [
            {
                'x': list(ndvi_dates),
                'y': list(ndvi_values_smoothed),
                'type': 'scatter',
                'mode': 'lines',
                'name': 'NDVI (Savgol)',
                'line': {'color': 'rgb(50, 168, 82)'}
            },
            {
                'x': list(ndvi_dates),
                'y': list(ndvi_values),
                'type': 'scatter',
                'mode': 'markers',
                'name': 'NDVI (Original)',
                'marker': {'color': 'rgba(50, 168, 82, 0.3)'}
            },
            {
                'x': list(precip_dates),
                'y': list(precip_values),
                'type': 'bar',
                'name': 'Precipitation',
                'marker': {'color': 'blue'},
                'yaxis': 'y2'
            }
        ]

        return JSONResponse(content=plotly_data)

    except ee.EEException as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching data from Earth Engine: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An unexpected error occurred: {str(e)}"
        )

@router.get("/nddi/{lat}/{lon}")
def timeseries_nddi(
    lat: float,
    lon: float,
    data_inicio: str = Query('1985-01-01', description="Start date in YYYY-MM-DD format"),
    data_fim: str = Query(None, description="End date in YYYY-MM-DD format")
):
    try:
        if not data_fim:
            data_fim = datetime.now().strftime('%Y-%m-%d')

        point = ee.Geometry.Point([lon, lat])

        mosaic = ee.ImageCollection('projects/nexgenmap/MapBiomas2/LANDSAT/BRAZIL/mosaics-2')

        def calculate_nddi(image):
            nddi = image.normalizedDifference(['ndvi_median_wet', 'ndwi_median_wet']).rename('NDDI')
            year = ee.Number(image.get('year'))
            date = ee.Date.fromYMD(year, 1, 1)
            return image.addBands(nddi).set('system:time_start', date.millis())

        nddi_collection = mosaic.map(calculate_nddi).filterDate(data_inicio, data_fim).filterBounds(point)

        # Otimização: Usar getRegion para extrair todos os dados de uma vez
        nddi_data = nddi_collection.select('NDDI').getRegion(point, 30).getInfo()

        if not nddi_data or len(nddi_data) <= 1:
            nddi_dates, nddi_values = [], []
        else:
            header = nddi_data[0]
            time_index = header.index('time')
            nddi_index = header.index('NDDI')

            processed_data = [
                (row[time_index], row[nddi_index])
                for row in nddi_data[1:] if row[nddi_index] is not None
            ]
            if not processed_data:
                nddi_dates, nddi_values = [], []
            else:
                df = pd.DataFrame(processed_data, columns=['time', 'NDDI'])
                df['date'] = pd.to_datetime(df['time'], unit='ms').dt.strftime('%Y-%m-%d')
                
                nddi_df_grouped = df.groupby('date')['NDDI'].mean().reset_index()
                
                nddi_dates = nddi_df_grouped['date'].tolist()
                nddi_values = nddi_df_grouped['NDDI'].tolist()

        plotly_data = [
            {
                'x': list(nddi_dates),
                'y': list(nddi_values),
                'type': 'scatter',
                'mode': 'markers',
                'name': 'NDDI (MapBiomas Mosaics)',
                'marker': {'color': 'rgba(50, 168, 82, 0.3)'}
            }
        ]

        return JSONResponse(content=plotly_data)

    except ee.EEException as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching data from Earth Engine: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An unexpected error occurred: {str(e)}"
        )

@router.get("/modis/{lat}/{lon}")
def timeseries_modis(
        lat: float,
        lon: float,
        data_inicio: str = Query(None, description="Start date in YYYY-MM-DD format"),
        data_fim: str = Query(None, description="End date in YYYY-MM-DD format")
):
    try:
        if not data_fim:
            data_fim = datetime.now().strftime('%Y-%m-%d')
        if not data_inicio:
            data_inicio = (datetime.now() - timedelta(days=365 * 20)).strftime('%Y-%m-%d')

        point = ee.Geometry.Point([lon, lat])

        modis = ee.ImageCollection('MODIS/061/MOD13Q1').filterBounds(point).filterDate(data_inicio, data_fim)

        def scale_ndvi(image):
            return image.select('NDVI').multiply(0.0001).copyProperties(image, ['system:time_start'])

        modis_scaled = modis.map(scale_ndvi)

        # Otimização: Usar getRegion para extrair todos os dados de uma vez
        ndvi_data = modis_scaled.getRegion(point, 250).getInfo()

        if not ndvi_data or len(ndvi_data) <= 1:
            ndvi_dates, ndvi_values = [], []
        else:
            header = ndvi_data[0]
            time_index = header.index('time')
            ndvi_index = header.index('NDVI')

            processed_data = [
                (row[time_index], row[ndvi_index])
                for row in ndvi_data[1:] if row[ndvi_index] is not None
            ]
            if not processed_data:
                ndvi_dates, ndvi_values = [], []
            else:
                df = pd.DataFrame(processed_data, columns=['time', 'NDVI'])
                df['date'] = pd.to_datetime(df['time'], unit='ms').dt.strftime('%Y-%m-%d')
                
                ndvi_df_grouped = df.groupby('date')['NDVI'].mean().reset_index()
                
                ndvi_df_grouped = ndvi_df_grouped[
                    (ndvi_df_grouped['NDVI'] >= -1) & (ndvi_df_grouped['NDVI'] <= 1)
                ]
                
                ndvi_dates = ndvi_df_grouped['date'].tolist()
                ndvi_values = ndvi_df_grouped['NDVI'].tolist()

        def apply_savgol_filter(values, window_size=13, poly_order=2):
            if len(values) > window_size:
                return savgol_filter(values, window_length=window_size, polyorder=poly_order)
            return values

        ndvi_values_smoothed = apply_savgol_filter(np.array(ndvi_values))

        chirps = ee.ImageCollection('UCSB-CHG/CHIRPS/DAILY').filterDate(data_inicio, data_fim).filterBounds(point)

        # Otimização para precipitação também
        precip_data = chirps.getRegion(point, 5000).getInfo()

        if not precip_data or len(precip_data) <= 1:
            precip_dates, precip_values = [], []
        else:
            header = precip_data[0]
            time_index = header.index('time')
            precip_index = header.index('precipitation')

            processed_precip = [
                (row[time_index], row[precip_index])
                for row in precip_data[1:] if row[precip_index] is not None
            ]
            if not processed_precip:
                precip_dates, precip_values = [], []
            else:
                precip_df = pd.DataFrame(processed_precip, columns=['time', 'precipitation'])
                precip_df['date'] = pd.to_datetime(precip_df['time'], unit='ms').dt.strftime('%Y-%m-%d')
                
                precip_df_grouped = precip_df.groupby('date')['precipitation'].sum().reset_index()

                precip_dates = precip_df_grouped['date'].tolist()
                precip_values = precip_df_grouped['precipitation'].tolist()

        plotly_data = [
            {
                'x': list(ndvi_dates),
                'y': list(ndvi_values_smoothed),
                'type': 'scatter',
                'mode': 'lines',
                'name': 'NDVI (Savgol)',
                'line': {'color': 'rgb(50, 168, 82)'}
            },
            {
                'x': list(ndvi_dates),
                'y': list(ndvi_values),
                'type': 'scatter',
                'mode': 'markers',
                'name': 'NDVI (Original)',
                'marker': {'color': 'rgba(50, 168, 82, 0.3)'}
            },
            {
                'x': list(precip_dates),
                'y': list(precip_values),
                'type': 'bar',
                'name': 'Precipitation',
                'marker': {'color': 'blue'},
                'yaxis': 'y2'
            }
        ]

        return JSONResponse(content=plotly_data)

    except ee.EEException as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching data from Earth Engine: {str(e)}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An unexpected error occurred: {str(e)}"
        )
@router.get("/sentinel2/{lat}/{lon}")
def timeseries_sentinel2(
    lat: float,
    lon: float,
    data_inicio: str = Query(None, description="Start date in YYYY-MM-DD format"),
    data_fim: str = Query(None, description="End date in YYYY-MM-DD format")
):
    try:
        if not data_fim:
            data_fim = datetime.now().strftime('%Y-%m-%d')
        if not data_inicio:
            data_inicio = (datetime.now() - timedelta(days=365*4.5)).strftime('%Y-%m-%d')

        point = ee.Geometry.Point([lon, lat])

        def maskS2clouds(image):
            qa = image.select('QA60')
            cloudBitMask = 1 << 10
            cirrusBitMask = 1 << 11
            mask = qa.bitwiseAnd(cloudBitMask).eq(0).And(qa.bitwiseAnd(cirrusBitMask).eq(0))
            return image.updateMask(mask).divide(10000).copyProperties(image, ["system:time_start"])

        def addNDVI(image):
            ndvi = image.normalizedDifference(['B8', 'B4']).rename('NDVI')
            return image.addBands(ndvi)

        s2 = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
              .filterDate(data_inicio, data_fim)
              .filterBounds(point)
              .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', 20))
              .map(maskS2clouds))

        s2_ndvi = s2.map(addNDVI)

        # Otimização: Usar getRegion para extrair todos os dados de uma vez
        ndvi_data = s2_ndvi.select('NDVI').getRegion(point, 10).getInfo()

        if not ndvi_data or len(ndvi_data) <= 1:
            ndvi_dates, ndvi_values = [], []
        else:
            header = ndvi_data[0]
            time_index = header.index('time')
            ndvi_index = header.index('NDVI')

            processed_data = [
                (row[time_index], row[ndvi_index])
                for row in ndvi_data[1:] if row[ndvi_index] is not None
            ]
            if not processed_data:
                ndvi_dates, ndvi_values = [], []
            else:
                df = pd.DataFrame(processed_data, columns=['time', 'NDVI'])
                df['date'] = pd.to_datetime(df['time'], unit='ms').dt.strftime('%Y-%m-%d')
                
                ndvi_df_grouped = df.groupby('date')['NDVI'].mean().reset_index()
                
                ndvi_dates = ndvi_df_grouped['date'].tolist()
                ndvi_values = ndvi_df_grouped['NDVI'].tolist()

        def apply_savgol_filter(values, window_size=7, poly_order=3):
            if len(values) > window_size:
                return savgol_filter(values, window_length=window_size, polyorder=poly_order).tolist()
            return values

        ndvi_values_smoothed = apply_savgol_filter(np.array(ndvi_values))

        chirps = ee.ImageCollection('UCSB-CHG/CHIRPS/DAILY').filterDate(data_inicio, data_fim).filterBounds(point)

        # Otimização para precipitação também
        precip_data = chirps.getRegion(point, 5000).getInfo()

        if not precip_data or len(precip_data) <= 1:
            precip_dates, precip_values = [], []
        else:
            header = precip_data[0]
            time_index = header.index('time')
            precip_index = header.index('precipitation')

            processed_precip = [
                (row[time_index], row[precip_index])
                for row in precip_data[1:] if row[precip_index] is not None
            ]
            if not processed_precip:
                precip_dates, precip_values = [], []
            else:
                precip_df = pd.DataFrame(processed_precip, columns=['time', 'precipitation'])
                precip_df['date'] = pd.to_datetime(precip_df['time'], unit='ms').dt.strftime('%Y-%m-%d')
                
                precip_df_grouped = precip_df.groupby('date')['precipitation'].sum().reset_index()

                precip_dates = precip_df_grouped['date'].tolist()
                precip_values = precip_df_grouped['precipitation'].tolist()

        plotly_data = [
            {
                'x': ndvi_dates,
                'y': ndvi_values_smoothed,
                'type': 'scatter',
                'mode': 'lines',
                'name': 'NDVI (Savgol)',
                'line': {'color': 'rgb(50, 168, 82)'}
            },
            {
                'x': ndvi_dates,
                'y': ndvi_values,
                'type': 'scatter',
                'mode': 'markers',
                'name': 'NDVI (Original)',
                'marker': {'color': 'rgba(50, 168, 82, 0.3)'}
            },
            {
                'x': precip_dates,
                'y': precip_values,
                'type': 'bar',
                'name': 'Precipitation',
                'marker': {'color': 'blue'},
                'yaxis': 'y2'
            }
        ]

        return JSONResponse(content=plotly_data)

    except ee.EEException as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching data from Earth Engine: {str(e)}"
        )
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An unexpected error occurred: {str(e)}"
        )
