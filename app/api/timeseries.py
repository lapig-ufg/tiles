from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
import ee

router = APIRouter()
@router.get("/landsat/{lat}/{lon}")
def timeseries_landsat(lat: float, lon: float):
    try:
        # Set unified band names
        bandNames = ['BLUE', 'GREEN', 'RED', 'NIR', 'SWIR1', 'SWIR2', 'QA_PIXEL']

        # Combine all Landsat collections
        l4 = ee.ImageCollection('LANDSAT/LT04/C02/T1_L2') \
            .select(['SR_B1', 'SR_B2', 'SR_B3', 'SR_B4', 'SR_B5', 'SR_B7', 'QA_PIXEL'], bandNames)
        l5 = ee.ImageCollection('LANDSAT/LT05/C02/T1_L2') \
            .select(['SR_B1', 'SR_B2', 'SR_B3', 'SR_B4', 'SR_B5', 'SR_B7', 'QA_PIXEL'], bandNames)
        l7 = ee.ImageCollection('LANDSAT/LE07/C02/T1_L2') \
            .select(['SR_B1', 'SR_B2', 'SR_B3', 'SR_B4', 'SR_B5', 'SR_B7', 'QA_PIXEL'], bandNames)
        l8 = ee.ImageCollection('LANDSAT/LC08/C02/T1_L2') \
            .select(['SR_B2', 'SR_B3', 'SR_B4', 'SR_B5', 'SR_B6', 'SR_B7', 'QA_PIXEL'], bandNames)
        l9 = ee.ImageCollection('LANDSAT/LC09/C02/T1_L2') \
            .select(['SR_B2', 'SR_B3', 'SR_B4', 'SR_B5', 'SR_B6', 'SR_B7', 'QA_PIXEL'], bandNames)

        collections = [l4, l5, l7, l8, l9]

        collection = ee.ImageCollection([])
        for li in collections:
            collection = collection.merge(li)

        point = ee.Geometry.Point([lon, lat])
        collection = collection.filterBounds(point)

        def get_time_series(image):
            date = ee.Date(image.get('system:time_start'))
            values = image.reduceRegion(ee.Reducer.mean(), point, 30)
            return ee.Feature(None, {'date': date, 'values': values})

        time_series = collection.map(get_time_series).getInfo()['features']

        # Format the data for Plotly
        dates = [entry['properties']['date'] for entry in time_series]
        series_data = {band: [] for band in bandNames}
        for entry in time_series:
            values = entry['properties']['values']
            for band in bandNames:
                series_data[band].append(values.get(band, None))

        # Create Plotly data structure
        plotly_data = []
        for band in bandNames:
            plotly_data.append({
                'x': dates,
                'y': series_data[band],
                'type': 'scatter',
                'mode': 'lines+markers',
                'name': band
            })

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