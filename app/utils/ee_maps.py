"""
Criação de mapas EE via REST com credencial por requisição — não toca o
ee.Initialize global do worker (que continua no pool de SAs para os
endpoints públicos). Espelha o padrão REST de app/utils/ee_compute.py.

A construção/serialização da imagem usa a lib `ee` já inicializada no worker
(registro de algoritmos é neutro em relação à credencial); apenas a chamada
HTTP que registra o mapa carrega o Bearer do usuário/campanha.
"""
import ee
import requests

from app.core.config import logger

_EE_BASE = "https://earthengine.googleapis.com/v1"
_VIS_KEYS = ("bands", "min", "max", "gamma", "palette", "opacity")


def create_map_with_credential(image, vis: dict, credential: dict) -> str:
    """Registra um mapa no EE com a credencial dada e devolve o template XYZ.

    Args:
        image: ee.Image já montada (sem visualização aplicada).
        vis: parâmetros de visualização (somente chaves suportadas são usadas).
        credential: ``{"access_token", "project_id", ...}`` do tvi-api.
    """
    visualized = image.visualize(**{k: v for k, v in vis.items() if k in _VIS_KEYS})
    serialized = ee.serializer.encode(visualized, for_cloud_api=True)
    project = credential["project_id"]
    resp = requests.post(
        f"{_EE_BASE}/projects/{project}/maps",
        json={"expression": serialized},
        headers={
            "Authorization": f"Bearer {credential['access_token']}",
            "X-Goog-User-Project": project,
        },
        timeout=60,
    )
    if resp.status_code >= 400:
        logger.warning(f"EE maps API {resp.status_code} (projeto {project})")
        raise RuntimeError(f"EE maps API {resp.status_code}: {resp.text[:500]}")
    name = resp.json()["name"]
    return f"{_EE_BASE}/{name}/tiles/{{z}}/{{x}}/{{y}}"
