import os
import sys

from dynaconf import Dynaconf
from loguru import logger

from app.core.otel import setup_otel_logging, create_loguru_otel_sink


def start_logger():
    type_logger = "development"
    if os.environ.get("TILES_ENV") == "production":
        type_logger = "production"

    # Inicializa OTEL logging (exporta via OTLP se configurado)
    setup_otel_logging()

    # Adiciona sink do Loguru -> OTLP (se OTEL estiver ativo)
    otel_sink = create_loguru_otel_sink()
    if otel_sink is not None:
        logger.add(otel_sink, level="INFO")
        logger.info("OTEL logging ativado - exportando logs via OTLP")

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

# Configuração unificada do Redis
# Use REDIS_URL do ambiente ou padrão para container valkey
REDIS_URL = settings.get("REDIS_URL", "redis://valkey:6379")
