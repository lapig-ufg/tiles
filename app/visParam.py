VISPARAMS = {
    "tvi-green": {
        "select": (["B4", "B8A", "B11"], ["RED", "REDEDGE4", "SWIR1"]),
        "visparam": {
            "gamma": "1.1",
            "max": "4300, 5400, 2800",
            "min": "600, 700, 400",
            "bands": ["SWIR1", "REDEDGE4", "RED"],
        },
    },
    "tvi-red": {
        "select": (["B4", "B8A", "B11"], ["RED", "REDEDGE4", "SWIR1"]),
        "visparam": {
            "gamma": "1.1",
            "max": "5400, 4300, 2800",
            "min": "700, 600, 400",
            "bands": ["REDEDGE4", "SWIR1", "RED"],
        },
    },
    "tvi-rgb": {
        'select': (['B4', 'B3', 'B2']),
        "visparam": {
            'min': '200, 300, 700',
            'max': '3000, 2500, 2300',
            'bands': ['B4', 'B3', 'B2'],
            'gamma': '1.35'
        }
    },
    'landsat-tvi-true': {
        "visparam": {
            'LANDSAT/LT05/C02/T1_L2': {'bands': ['SR_B3', 'SR_B2', 'SR_B1'], 'min': [0.03, 0.03, 0.0],
                                       'max': [0.25, 0.25, 0.25], 'gamma': [1.2]},
            'LANDSAT/LE07/C02/T1_L2': {'bands': ['SR_B3', 'SR_B2', 'SR_B1'], 'min': [0.03, 0.03, 0.0],
                                       'max': [0.25, 0.25, 0.25], 'gamma': [1.2]},
            'LANDSAT/LC08/C02/T1_L2': {'bands': ['SR_B4', 'SR_B3', 'SR_B2'], 'min': [0.03, 0.03, 0.0],
                                       'max': [0.25, 0.25, 0.25], 'gamma': [1.2]},
            'LANDSAT/LC09/C02/T1_L2': {'bands': ['SR_B4', 'SR_B3', 'SR_B2'], 'min': [0.03, 0.03, 0.0],
                                       'max': [0.25, 0.25, 0.25], 'gamma': [1.2]}
        }
    },
    'landsat-tvi-agri': {
        "visparam": {
            'LANDSAT/LT05/C02/T1_L2': {'bands': ['SR_B5', 'SR_B4', 'SR_B3'], 'min': [0.05, 0.05, 0.03],
                                       'max': [0.5, 0.55, 0.3], 'gamma': [0.9]},
            'LANDSAT/LE07/C02/T1_L2': {'bands': ['SR_B5', 'SR_B4', 'SR_B3'], 'min': [0.05, 0.05, 0.03],
                                       'max': [0.5, 0.55, 0.3], 'gamma': [0.9]},
            'LANDSAT/LC08/C02/T1_L2': {'bands': ['SR_B6', 'SR_B5', 'SR_B4'], 'min': [0.05, 0.05, 0.03],
                                       'max': [0.5, 0.55, 0.3], 'gamma': [0.9]},
            'LANDSAT/LC09/C02/T1_L2': {'bands': ['SR_B6', 'SR_B5', 'SR_B4'], 'min': [0.05, 0.05, 0.03],
                                       'max': [0.5, 0.55, 0.3], 'gamma': [0.9]}
        }
    },
    'landsat-tvi-false': {
        "visparam": {
            'LANDSAT/LT05/C02/T1_L2': {'bands': ['SR_B4', 'SR_B5', 'SR_B3'], 'min': [0.05, 0.05, 0.03],
                                       'max': [0.6, 0.55, 0.3], 'gamma': [1.2]},
            'LANDSAT/LE07/C02/T1_L2': {'bands': ['SR_B4', 'SR_B5', 'SR_B3'], 'min': [0.05, 0.05, 0.03],
                                       'max': [0.6, 0.55, 0.3], 'gamma': [1.2]},
            'LANDSAT/LC08/C02/T1_L2': {'bands': ['SR_B5', 'SR_B6', 'SR_B4'], 'min': [0.05, 0.05, 0.03],
                                       'max': [0.6, 0.55, 0.3], 'gamma': [1.2]},
            'LANDSAT/LC09/C02/T1_L2': {'bands': ['SR_B5', 'SR_B6', 'SR_B4'], 'min': [0.05, 0.05, 0.03],
                                       'max': [0.6, 0.55, 0.3], 'gamma': [1.2]}
        }
    }
}


# Função para obter a coleção Landsat apropriada baseada no ano
def get_landsat_collection(year):
    """
    Retorna a coleção Landsat apropriada baseada no ano.

    Regras:
    - 1985 a 2012: Landsat 5 (LT05)
    - 2013: Landsat 7 (LE07)
    - 2014 a 2024: Landsat 8 (LC08)
    - 2025 em diante: Landsat 9 (LC09)

    Args:
        year (int): Ano para o qual se deseja obter a coleção

    Returns:
        str: Nome da coleção Landsat apropriada
    """
    if 1985 <= year <= 2012:
        return 'LANDSAT/LT05/C02/T1_L2'
    elif year == 2013:
        return 'LANDSAT/LE07/C02/T1_L2'
    elif 2014 <= year <= 2024:
        return 'LANDSAT/LC08/C02/T1_L2'
    elif year >= 2025:
        return 'LANDSAT/LC09/C02/T1_L2'
    else:
        raise ValueError(f"Ano {year} fora do intervalo suportado (1985 em diante)")


# Função para obter os parâmetros de visualização baseados no tipo e no ano
def get_landsat_vis_params(vis_type, year_or_collection):
    """
    Obtém os parâmetros de visualização baseados no tipo e no ano ou coleção.

    Args:
        vis_type (str): Tipo de visualização (ex: 'landsat-tvi-true')
        year_or_collection (int|str): Ano (int) para determinar qual satélite usar
                                      ou nome da coleção (str) diretamente

    Returns:
        dict: Parâmetros de visualização apropriados
    """
    if isinstance(year_or_collection, int):
        collection_name = get_landsat_collection(year_or_collection)
    else:
        collection_name = year_or_collection
    return VISPARAMS[vis_type]['visparam'][collection_name]


# Função para gerar lista de satélites por período de anos
def generate_landsat_list(start_year, end_year):
    """
    Gera uma lista com o satélite apropriado para cada ano no intervalo.

    Args:
        start_year (int): Ano inicial
        end_year (int): Ano final

    Returns:
        list: Lista de tuplas (ano, coleção_landsat)
    """
    landsat_list = []
    for year in range(start_year, end_year + 1):
        collection = get_landsat_collection(year)
        landsat_list.append((year, collection))
    return landsat_list

