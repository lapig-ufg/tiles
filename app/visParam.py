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
    "tvi-rgb":{
        'select':(['B4', 'B3', 'B2']),
        "visparam": {
            'min': '200, 300, 700',
            'max': '3000, 2500, 2300',
            'bands': ['B4', 'B3', 'B2'],
            'gamma':'1.35'
        }
    }
}
