import math
import time
from datetime import date
from urllib.parse import urlencode

import numpy as np
import rasterio
from pyproj import CRS, Transformer
from rasterio.features import geometry_mask
from rasterio.windows import Window, from_bounds
from shapely.geometry import Point, mapping
from shapely.ops import transform

from .catalog import STAC
from .transport import RangeBridge
from .visualization import GDAL_OPTIONS

COLLECTIONS = {'landsat-2': 'qa_pixel', 'S2_L2A-1': 'SCL'}


def point_buffer(lon, lat, radius):
    if not all(math.isfinite(v) for v in (lon, lat, radius)) or not (-180 <= lon <= 180 and -80 <= lat <= 80 and 100 <= radius <= 5000):
        raise ValueError('Ponto inválido ou raio fora do intervalo de 100 a 5.000 metros')
    local = CRS.from_proj4(f'+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m')
    inverse = Transformer.from_crs(local, 4326, always_xy=True)
    return transform(inverse.transform, Point(0, 0).buffer(radius, resolution=64))


def quality_metrics(data, inside, collection, valid_mask=None):
    if collection not in COLLECTIONS:
        raise ValueError('Seleção local disponível para Landsat e Sentinel-2 L2A')
    values = np.asarray(data)
    inside = np.asarray(inside, dtype=bool)
    valid = inside.copy() if valid_mask is None else inside & valid_mask
    if collection == 'landsat-2':
        valid &= (values & 1) == 0
        cloud = (values & ((1 << 1) | (1 << 2) | (1 << 3))) != 0
        shadow = (values & (1 << 4)) != 0
        snow = (values & (1 << 5)) != 0
        legacy = (values & ((1 << 3) | (1 << 5))) != 0
    else:
        valid &= np.isin(values, np.arange(2, 12))
        cloud = np.isin(values, [8, 9, 10])
        shadow = values == 3
        snow = values == 11
        legacy = np.zeros_like(valid)
    total, count = int(inside.sum()), int(valid.sum())
    fraction = lambda mask: float(100 * (mask & valid).sum() / count) if count else None
    return dict(buffer_pixels=total, valid_pixels=count,
                valid_pct=100 * count / total if total else 0,
                eligible=bool(total and count == total),
                cloud_pct=fraction(cloud), shadow_pct=fraction(shadow),
                snow_pct=fraction(snow), unusable_pct=fraction(cloud | shadow | snow),
                legacy_cloud_snow_count=int((legacy & valid).sum()) if count else None)


def rank_rows(rows, criterion):
    if criterion not in {'cloud', 'unusable'}:
        raise ValueError('Critério inválido')
    metric = criterion + '_pct'
    return sorted((r for r in rows if r['eligible']), key=lambda r: (
        r[metric], r['unusable_pct'], r['id']))


def read_quality(remote, item, geometry, collection):
    asset = item.get('assets', {}).get(COLLECTIONS[collection])
    if not asset:
        return dict(eligible=False, reason='Máscara de qualidade indisponível')
    with RangeBridge(remote) as bridge, rasterio.Env(**GDAL_OPTIONS):
        with rasterio.open(bridge.url(asset['href'])) as src:
            project = Transformer.from_crs(4326, src.crs, always_xy=True)
            projected = transform(project.transform, geometry)
            raw = from_bounds(*projected.bounds, transform=src.transform)
            left, top = math.floor(raw.col_off), math.floor(raw.row_off)
            window = Window(left, top, math.ceil(raw.col_off + raw.width) - left,
                            math.ceil(raw.row_off + raw.height) - top)
            if window.width * window.height > 2_000_000:
                raise ValueError('Janela excede o limite de leitura do experimento')
            data = src.read(1, window=window, boundless=True, masked=True)
            inside = geometry_mask([mapping(projected)], out_shape=data.shape,
                                   transform=src.window_transform(window), invert=True)
            result = quality_metrics(data.filled(0), inside, collection, ~np.ma.getmaskarray(data))
            result.update(resolution=list(src.res), crs=str(src.crs))
            if not result['eligible']:
                result['reason'] = 'Buffer sem cobertura integral de pixels válidos'
            return result


def search_candidates(remote, collection, geometry, start, end, max_candidates=60):
    start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    if start_date > end_date or (end_date - start_date).days > 366:
        raise ValueError('Escolha um intervalo de até 366 dias')
    found = {}
    for page in range(1, 102):
        query = urlencode(dict(collections=collection, bbox=','.join(map(str, geometry.bounds)),
                               datetime=f'{start}T00:00:00Z/{end}T23:59:59.999999Z', limit=100, page=page))
        response = remote.json(STAC + '/search?' + query)
        items = response.get('features', [])
        fresh = [item for item in items if item['id'] not in found]
        if items and not fresh:
            raise ValueError('Paginação sem avanço; seleção incompleta recusada')
        found.update((item['id'], item) for item in fresh)
        if len(found) > max_candidates:
            raise ValueError(f'Mais de {max_candidates} cenas no período. Reduza o intervalo para comparar todas as aquisições.')
        if not any(link.get('rel') == 'next' for link in response.get('links', [])) and len(items) < 100:
            return list(found.values())
    raise ValueError('Catálogo incompleto; seleção recusada')


def compare(remote, collection, lon, lat, radius, start, end, criterion='cloud', max_seconds=180):
    if collection not in COLLECTIONS:
        raise ValueError('Seleção local disponível para Landsat e Sentinel-2 L2A')
    if criterion not in {'cloud', 'unusable'}:
        raise ValueError('Critério inválido')
    started = time.monotonic()
    geometry = point_buffer(lon, lat, radius)
    candidates = search_candidates(remote, collection, geometry, start, end)
    rows = []
    for item in sorted(candidates, key=lambda item: item['id']):
        if time.monotonic() - started > max_seconds:
            raise ValueError('Tempo de análise excedido. Reduza o período; nenhuma seleção parcial foi aceita.')
        props = item.get('properties', {})
        rows.append(dict(id=item['id'], item=item, scene_cloud_pct=props.get('eo:cloud_cover'),
                         **read_quality(remote, item, geometry, collection)))
    ranked = rank_rows(rows, criterion)
    global_rows = sorted((r for r in rows if r.get('eligible') and isinstance(r['scene_cloud_pct'], (int, float)) and math.isfinite(r['scene_cloud_pct']) and 0 <= r['scene_cloud_pct'] <= 100),
                         key=lambda r: (r['scene_cloud_pct'], r['id']))
    return dict(collection=collection, lon=lon, lat=lat, radius_m=radius, start=start, end=end,
                criterion=criterion, geometry=mapping(geometry), complete=True,
                evaluated=len(rows), eligible=len(ranked),
                best_local_id=ranked[0]['id'] if ranked else None,
                best_global_id=global_rows[0]['id'] if global_rows else None,
                rows=ranked + [r for r in rows if not r['eligible']],
                seconds=round(time.monotonic() - started, 3),
                policy='Pixels nativos; buffer integral válido; desempate por área inadequada e identificador; sem filtro global de nuvens.')
