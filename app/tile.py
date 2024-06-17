import math
import geopandas as gpd
from shapely.geometry import Point
def latlon_to_tile(lat, lon, zoom):
    """Converts latitude and longitude to tile coordinates."""
    lat_rad = math.radians(lat)
    n = 2 ** zoom
    x_tile = int((lon + 180.0) / 360.0 * n)
    y_tile = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n)
    return x_tile, y_tile

def get_brazil_tile_bounds(zoom):
    """Returns the tile boundaries for Brazil at a specific zoom level."""
    # Aproximated boundaries for Brazil
    lat_min, lon_min = -33.7, -73.9  # Southernmost point
    lat_max, lon_max = 5.3, -34.8    # Northernmost point
    
    
    
    x_min, y_min = latlon_to_tile(lat_min, lon_min, zoom)
    x_max, y_max = latlon_to_tile(lat_max, lon_max, zoom)
    
    return x_min, y_min, x_max, y_max

def is_within_brazil(x, y, z):
    """Check if tile x, y at zoom level z is within the bounds of Brazil."""
    x_min, y_min, x_max, y_max = get_brazil_tile_bounds(z)
    return x in range(x_min, x_max + 1) and y in range( y_max, y_min + 1)



def get_tile_bounds(lat, lon,zoom):
    """Returns the tile boundaries for Brazil at a specific zoom level."""
    # Aproximated boundaries for Brazil
    gdf=gpd.GeoDataFrame([{
        'geometry': Point(lon,lat),
        
    }],crs='EPSG:4326', geometry='geometry')
    lon_min, lat_min, lon_max, lat_max  = gdf.to_crs(3857).geometry.buffer(3000).to_crs(4326).total_bounds
 
    x_min, y_min = latlon_to_tile(lat_min, lon_min, zoom)
    x_max, y_max = latlon_to_tile(lat_max, lon_max, zoom)
    
    return x_min, y_min, x_max, y_max


def is_within_boundsbox(lat,lon ,x, y, z):
    """Check if tile x, y at zoom level z is within the bounds of Brazil."""
    x_min, y_min, x_max, y_max = get_tile_bounds(lat,lon, z)
    return x in range(x_min, x_max + 1) and y in range( y_max, y_min + 1)