"""Índices espectrais declarados nos visparams (campo ``index``).

Um visparam com ``index`` descreve uma banda derivada de duas bandas de
entrada e renderizada com paleta. O cálculo acontece num único ponto, aqui,
e os construtores de camada apenas trocam a imagem e o dicionário de
visualização antes de registrar o mapa no Earth Engine.
"""
import ee

_EE_VIS_KEYS = ("bands", "min", "max", "gamma", "palette", "opacity")


def resolve_visualization(image, vis: dict) -> tuple:
    """Devolve ``(imagem, vis_ee)`` prontos para ``getMapId``.

    Sem ``index``: a imagem original e ``vis`` restrito às chaves que o
    Earth Engine conhece, sem valores nulos. Com ``index``: a banda derivada
    no lugar das bandas de entrada e ``vis_ee`` com ``bands``, ``min``,
    ``max`` e ``palette``; ``gamma`` é descartado porque o Earth Engine não o
    aceita junto com paleta.
    """
    index = vis.get("index")
    if not index:
        return image, {k: v for k, v in vis.items() if k in _EE_VIS_KEYS and v is not None}

    index_type = index.get("type")
    if index_type != "normalized_difference":
        raise ValueError(f"tipo de índice não suportado: {index_type}")

    name = index.get("name") or "NDVI"
    derived = ee.Image(image).normalizedDifference(list(index["bands"])).rename(name)
    vis_ee = {"bands": [name], "min": vis["min"], "max": vis["max"]}
    if vis.get("palette"):
        vis_ee["palette"] = list(vis["palette"])
    return derived, vis_ee
