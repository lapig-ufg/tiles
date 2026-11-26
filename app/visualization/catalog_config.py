from copy import deepcopy

import pymongo

from app.core.config import settings
from app.models.vis_params import LandsatCollectionMapping, VisParamDocument


def landsat_collection(year, mapping):
    if mapping is None:
        from app.visualization.visParam import get_landsat_collection

        return get_landsat_collection(year)
    for item in mapping.mappings:
        if item['start_year'] <= year <= item['end_year']:
            return item['collection']
    raise ValueError(f'No Landsat collection found for year {year}')


def normalize_visualization(parameters):
    vis = parameters.model_dump(exclude_none=True)
    for key in ('min', 'max', 'gamma'):
        if key in vis:
            value = vis[key]
            vis[key] = ','.join(map(str, value)) if isinstance(value, list) else str(value)
    return vis


def sentinel_visualization(document):
    if document is None or not document.vis_params:
        raise ValueError('Configuração visual Sentinel-2 indisponível')
    entry = {'visparam': normalize_visualization(document.vis_params)}
    if document.band_config:
        original = list(document.band_config.original_bands)
        mapped = document.band_config.mapped_bands
        entry['select'] = (original, list(mapped)) if mapped else original
    return entry


def landsat_visualization(document, collection):
    if document is None or not document.satellite_configs:
        raise ValueError('Configuração visual Landsat indisponível')
    for satellite in document.satellite_configs:
        if satellite.collection_id != collection:
            continue
        return normalize_visualization(satellite.vis_params)
    raise ValueError(f'No parameters found for {document.name} and collection {collection}')


def _read_catalog(visparam_name, include_landsat_mapping=False):
    with pymongo.MongoClient(
        settings.get('MONGODB_URL', 'mongodb://localhost:27017'),
        serverSelectionTimeoutMS=5000, connectTimeoutMS=5000, socketTimeoutMS=5000,
    ) as client:
        catalog = client[settings.get('MONGODB_DB', 'tvi')]['vis_params']
        mapping_document = (catalog.find_one({'_id': 'landsat_collections'})
                            if include_landsat_mapping else None)
        document = catalog.find_one({'name': visparam_name, 'active': {'$ne': False}})
    return document, mapping_document


def resolve_landsat_config_sync(visparam_name, year):
    from app.visualization import vis_params_loader

    # A flag seleciona a coleção; o caminho HTTP mantém os parâmetros visuais no MongoDB.
    document, mapping_document = _read_catalog(visparam_name, vis_params_loader.USE_MONGODB_VIS_PARAMS)
    mapping = LandsatCollectionMapping(**mapping_document) if mapping_document else None
    visual = VisParamDocument(**document) if document else None
    collection = (landsat_collection(year, mapping) if vis_params_loader.USE_MONGODB_VIS_PARAMS
                  else vis_params_loader.get_landsat_collection(year))
    return collection, landsat_visualization(visual, collection)


def resolve_s2_config_sync(visparam_name):
    from app.visualization import vis_params_loader

    if not vis_params_loader.USE_MONGODB_VIS_PARAMS:
        from app.visualization.visParam import VISPARAMS

        return deepcopy(VISPARAMS[visparam_name])
    document, _ = _read_catalog(visparam_name)
    visual = VisParamDocument(**document) if document else None
    return sentinel_visualization(visual)
