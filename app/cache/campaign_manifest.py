import hashlib
import json
import math
import re
from datetime import datetime

from app.cache.keys import build_tile_cache_keys


def campaign_config(campaign, width=512, height=512):
    layer = {'landsat': 'landsat', 'sentinel-2': 's2_harmonized'}.get(campaign.get('imageType'))
    if layer is None or campaign.get('useDynamicMaps') is not True or campaign.get('wmsConfig', {}).get('enabled'):
        raise ValueError('O cache offline requer mapas dinâmicos Landsat ou Sentinel sem substituição WMS')
    first, last = campaign.get('initialYear'), campaign.get('finalYear')
    zoom = campaign.get('zoomLevel', 13)
    values = (first, last, zoom, width, height)
    if any(isinstance(v, bool) or not isinstance(v, int) for v in values):
        raise ValueError('Anos, zoom e dimensões devem ser inteiros')
    minimum_year = 1970 if campaign.get('imagerySource') == 'bdc' else 1985
    if not minimum_year <= first <= last <= datetime.now().year or not 10 <= zoom <= 18:
        raise ValueError('Período ou zoom inválido')
    if not 256 <= width <= 2048 or not 256 <= height <= 2048:
        raise ValueError('Dimensões da cobertura devem estar entre 256 e 2048 pixels')
    bdc = None
    if campaign.get('imagerySource') == 'bdc':
        bdc = bdc_config(campaign)
    if bdc:
        visualizations = bdc['profiles']
    elif 'visParams' in campaign:
        visualizations = campaign['visParams']
    elif 'visParamsEnable' in campaign:
        visualizations = campaign['visParamsEnable']
    else:
        visualizations = [campaign['visParam']] if campaign.get('visParam') else None
    if not isinstance(visualizations, list) or not visualizations or len(visualizations) > 32:
        raise ValueError('Selecione os parâmetros de visualização da campanha')
    if any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', v) for v in visualizations):
        raise ValueError('Parâmetro de visualização inválido')
    default = bdc['defaultProfile'] if bdc else campaign.get('defaultVisParam')
    if default is None and not bdc:
        default = visualizations[0]
    if not isinstance(default, str) or default not in visualizations:
        raise ValueError('A composição padrão deve pertencer às visualizações da campanha')
    config = {'layer': layer, 'years': list(range(first, last + 1)),
            'visualizations': [default], 'periods': ['WET', 'DRY'],
            'zooms': [zoom],
            'width': width, 'height': height, 'buffer': 2, 'version': 2, 'policy': 'configured-default-v2'}
    if bdc:
        from app.modules.bdc.offline_tiles import renderer_revision
        config.update(source='bdc', bdc=bdc, renderer_revision=renderer_revision())
    return config


def bdc_config(campaign):
    from app.modules.bdc.profiles import profiles

    config = campaign.get('bdcConfig')
    expected = {'landsat': 'landsat-2', 'sentinel-2': 'S2_L2A-1'}.get(campaign.get('imageType'))
    if not isinstance(config, dict) or config.get('collection') != expected:
        raise ValueError('Coleção BDC incompatível com o sensor')
    revision, radius = config.get('selectionRevision'), config.get('bufferRadiusM')
    if (type(config.get('schemaVersion')) is not int or config['schemaVersion'] != 1
            or type(revision) is not int or not 1 <= revision <= 9007199254740991
            or type(radius) not in (int, float) or not math.isfinite(radius) or not 100 <= radius <= 5000
            or config.get('periodPolicy') != 'tvi-seasonal-v1'
            or config.get('selectionStrategy') not in {'local_cloud', 'local_unusable'}):
        raise ValueError('Configuração de seleção BDC inválida')
    selected = config.get('profiles')
    available = {profile['id'] for profile in profiles({'id': expected})}
    if (not isinstance(selected, list) or not selected or len(selected) > 32
            or any(not isinstance(profile, str) or profile not in available for profile in selected)
            or config.get('defaultProfile') not in selected):
        raise ValueError('Composições BDC inválidas')
    return {key: config[key] for key in ('schemaVersion', 'collection', 'selectionRevision',
            'bufferRadiusM', 'periodPolicy', 'selectionStrategy')} | {'profiles': sorted(set(selected)), 'defaultProfile': config['defaultProfile']}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def viewport_tiles(lat, lon, zooms, width, height, buffer=2):
    if (isinstance(lat, bool) or isinstance(lon, bool) or
            not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (lat, lon)) or
            not -90 <= lat <= 90 or not -180 <= lon <= 180):
        raise ValueError('Coordenadas geográficas inválidas')
    lat = max(-85.0511287798066, min(85.0511287798066, lat))
    for z in zooms:
        n = 2 ** z
        px = (lon + 180) / 360 * n * 256
        py = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n * 256
        # Leaflet arredonda a origem em pixels; a margem inclui seu keepBuffer padrão.
        left = math.floor((px - width / 2 - 1) / 256) - buffer
        right = math.floor((px + width / 2 + 1) / 256) + buffer
        top = max(0, math.floor((py - height / 2 - 1) / 256) - buffer)
        bottom = min(n - 1, math.floor((py + height / 2 + 1) / 256) + buffer)
        seen = set()
        for y in range(top, bottom + 1):
            for raw_x in range(left, right + 1):
                x = raw_x % n
                if (x, y) not in seen:
                    seen.add((x, y))
                    yield {'x': x, 'y': y, 'z': z}


def manifest_for_point(point, config, start=0):
    if config.get('source') != 'bdc':
        from app.services.tile import tile2goehashBBOX

    combinations = len(config['years']) * len(config['periods']) * len(config['visualizations'])
    point_digest = fingerprint([type(point['_id']).__name__, str(point['_id']), point['lat'], point['lon']])
    for tile_index, tile in enumerate(viewport_tiles(point['lat'], point['lon'], config['zooms'],
                                      config['width'], config['height'], config['buffer'])):
        if (tile_index + 1) * combinations <= start:
            continue
        geohash = None
        if config.get('source') != 'bdc':
            geohash, _ = tile2goehashBBOX(tile['x'], tile['y'], tile['z'])
        index = tile_index * combinations
        for year in reversed(config['years']):
            for period in config['periods']:
                for visparam in config['visualizations']:
                    index += 1
                    if index <= start:
                        continue
                    if config.get('source') == 'bdc':
                        params = dict(tile, source='bdc', year=year, period=period, visparam=visparam)
                        key = 'bdc:' + fingerprint([point_digest, config['bdc'], config['renderer_revision'], params])
                        yield {'point_id': str(point['_id']), 'point_ref': point['_id'],
                               'point_fingerprint': point_digest, 'params': params, 'key': key}
                        continue
                    params = dict(tile, layer=config['layer'], year=year, period=period,
                                  month=1, visparam=visparam, composite_mode='BEST_IMAGE')
                    key = build_tile_cache_keys(**params, geohash=geohash).png
                    yield {'point_id': str(point['_id']), 'point_ref': point['_id'],
                           'point_fingerprint': point_digest, 'params': params, 'key': key}
