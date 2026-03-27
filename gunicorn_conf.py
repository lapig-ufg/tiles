"""
Configuração do Gunicorn com hooks para inicialização do pool de SAs do GEE.

Cada worker recebe uma service account distinta via post_fork,
garantindo distribuição de cota entre processos.
"""
import os
import socket

# Configurações movidas de CLI para config file (compatibilidade gunicorn 21+)
keepalive = 5


def post_fork(server, worker):
    """Chamado em cada worker após o fork. Inicializa GEE com SA do pool."""
    from app.core.gee_auth import initialize_earth_engine

    worker_id = f"{socket.gethostname()}-gunicorn-{worker.pid}"
    initialize_earth_engine(worker_id)


def worker_exit(server, worker):
    """Chamado quando o worker encerra. Libera a SA de volta ao pool."""
    from app.core.gee_auth import shutdown_earth_engine

    shutdown_earth_engine()
