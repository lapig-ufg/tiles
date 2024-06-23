from datetime import datetime
CAPABILITIES = {
    "collections": [
        {
            "name": "s2_harmonized",
            "visparam": ["tvi-green", "tvi-red", "tvi-rgb"],
            "period": ["WET", "DRY"],
            "year": [2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024]

        },
        {
            "name": "landsat",
            "visparam": ["landsat-tvi-red", "landsat-tvi-green", "landsat-tvi-rgb"],
            "month": ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12"],
            "year": list(range(1985, datetime.now().year + 1)),
            "period": ["WET", "DRY", "MONTH"]
        }
    ]
}
