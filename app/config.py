import os
import sys

from dynaconf import Dynaconf
from loguru import logger


def start_logger():
    type_logger = 'development'
    if os.environ.get('ECO_ENV') == 'production':
        type_logger = 'production'
    logger.info(f'The system is operating in mode {type_logger}')


confi_format = '[ {time} | process: {process.id} | {level: <8}] {module}.{function}:{line} {message}'
rotation = '500 MB'


if os.environ.get('ECO_ENV') == 'production':
    logger.remove()
    logger.add(sys.stderr, level='INFO', format=confi_format)

try:
    logger.add(
        '/logs/tiles_eco/tiles_eco.log', rotation=rotation, level='INFO'
    )
except:
    logger.add(
        '../logs/tiles_eco/tiles_eco.log',
        rotation=rotation,
        level='INFO',
    )
try:
    logger.add(
        '/logs/tiles_eco/tiles_eco_WARNING.log',
        level='WARNING',
        rotation=rotation,
    )
except:
    logger.add(
        '../logs/tiles_eco/tiles_eco_WARNING.log',
        level='WARNING',
        rotation=rotation,
    )

settings = Dynaconf(
    envvar_prefix='ECOTILES',
    settings_files=[
        'settings.toml',
        '.secrets.toml',
        '../settings.toml',
        '/data/settings.toml',
    ],
    environments=True,
    load_dotenv=True,
)


LAYERS = {
    'biomes':{
        'palette':['red'],
        'opacity': 0.5,
        'layer_name': 'biomes',
        'assets':'projects/mapbiomas-territories/assets/TERRITORIESTEST/biomes'
    },
    'states':{
        'palette':['#7c7579'],
        'opacity': 0.5,
        'layer_name': 'states',
        'assets':'projects/mapbiomas-territories/assets/TERRITORIESTEST/states'
    },
    'sicar':{
        'palette':['#A0A0A0'],
        'opacity': 0.5,
        'layer_name': 'sicar',
        'assets':'projects/mapbiomas-territories/assets/RURAL-PROPERTIES/sicar'
    },
    'level_one_basin':{
        'palette':['#57F2F2'],
        'opacity': 0.5,
        'layer_name': 'level_one_basin'
    }
    
}