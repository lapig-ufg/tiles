import os
import sys

from dynaconf import Dynaconf
from loguru import logger


def start_logger():
    type_logger = "development"
    if os.environ.get("TILES_ENV") == "production":
        type_logger = "production"
    logger.info(f"The system is operating in mode {type_logger}")


confi_format = "[ {time} | process: {process.id} | {level: <8}] {module}.{function}:{line} {message}"
rotation = "500 MB"


if os.environ.get("TILES_ENV") == "production":
    logger.remove()
    logger.add(sys.stderr, level="INFO", format=confi_format)

try:
    logger.add("/logs/tiles/tiles.log", rotation=rotation, level="INFO")
except:
    logger.add(
        "./logs/tiles/tiles.log",
        rotation=rotation,
        level="INFO",
    )
try:
    logger.add(
        "/logs/tiles/tiles_WARNING.log",
        level="WARNING",
        rotation=rotation,
    )
except:
    logger.add(
        "./logs/tiles/tiles_WARNING.log",
        level="WARNING",
        rotation=rotation,
    )

settings = Dynaconf(
    envvar_prefix=False,  # Sem prefixo - usa variáveis de ambiente diretamente
    settings_files=[
        "settings.toml",
        ".secrets.toml",
        "../settings.toml",
        "/data/settings.toml",
    ],
    environments=True,
    load_dotenv=True,
)
