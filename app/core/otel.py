"""
OpenTelemetry Logging - Exporta logs via OTLP para o stack OTEL (Loki/Grafana).

Usa o stdlib logging como bridge: Loguru -> stdlib -> OTLP Exporter.
Gunicorn logs vão naturalmente pelo stdlib logging.
"""

import os
import logging

from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter


_logger_provider = None


def setup_otel_logging():
    """Inicializa o OpenTelemetry Log Exporter se OTEL_EXPORTER_OTLP_ENDPOINT estiver definido."""
    global _logger_provider

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", 'http://otel:4318')
    if not endpoint:
        return None

    service_name = os.environ.get("OTEL_SERVICE_NAME", "tiles-api")
    environment = os.environ.get("TILES_ENV", "production")

    resource = Resource.create({
        "service.name": service_name,
        "service.version": "2.0",
        "deployment.environment": environment,
    })

    _logger_provider = LoggerProvider(resource=resource)

    exporter = OTLPLogExporter(
        endpoint=f"{endpoint}/v1/logs",
    )

    _logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(exporter)
    )

    # Handler que envia logs do stdlib logging para o OTLP
    otel_handler = LoggingHandler(
        level=logging.INFO,
        logger_provider=_logger_provider,
    )

    # Adiciona ao root logger - captura logs do Gunicorn, uvicorn, e app
    root_logger = logging.getLogger()
    root_logger.addHandler(otel_handler)

    # Garante que loggers do gunicorn/uvicorn propagam
    for name in ("gunicorn.error", "gunicorn.access", "uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.propagate = True

    return _logger_provider


def create_loguru_otel_sink():
    """Cria um sink do Loguru que encaminha logs para o stdlib logging -> OTLP.

    Retorna None se OTEL não estiver configurado.
    """
    if _logger_provider is None:
        return None

    stdlib_logger = logging.getLogger("tiles.app")
    stdlib_logger.setLevel(logging.DEBUG)

    level_map = {
        "TRACE": logging.DEBUG,
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "SUCCESS": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR,
        "CRITICAL": logging.CRITICAL,
    }

    def sink(message):
        record = message.record
        level = level_map.get(record["level"].name, logging.INFO)
        stdlib_logger.log(
            level,
            record["message"],
            extra={
                "loguru_module": record["module"],
                "loguru_function": record["function"],
                "loguru_line": record["line"],
            },
        )

    return sink


def shutdown_otel_logging():
    """Flush e shutdown do OTLP exporter."""
    global _logger_provider
    if _logger_provider is not None:
        _logger_provider.shutdown()
        _logger_provider = None
