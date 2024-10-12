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
            'LANDSAT/LE07/C02/T1_L2': {'bands': ['SR_B5', 'SR_B4', 'SR_B3'], 'min': [0.05, 0.05, 0.03],
                                       'max': [0.6, 0.55, 0.3], 'gamma': [1.2]},
            'LANDSAT/LC08/C02/T1_L2': {'bands': ['SR_B5', 'SR_B6', 'SR_B4'], 'min': [0.05, 0.05, 0.03],
                                       'max': [0.6, 0.55, 0.3], 'gamma': [1.2]},
            'LANDSAT/LC09/C02/T1_L2': {'bands': ['SR_B5', 'SR_B6', 'SR_B4'], 'min': [0.05, 0.05, 0.03],
                                       'max': [0.6, 0.55, 0.3], 'gamma': [1.2]}
        }
    }
}

# Função para obter os parâmetros de visualização com base no tipo e na coleção Landsat
def get_landsat_vis_params(vis_type, collection_name):
    return VISPARAMS[vis_type]['visparam'][collection_name]
