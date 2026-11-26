import numpy as np

GDAL_OPTIONS = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES",
    GDAL_HTTP_MULTIRANGE="SERIAL",
    GDAL_HTTP_MAX_RETRY=0,
    GDAL_HTTP_TIMEOUT=65,
    GDAL_HTTP_CONNECTTIMEOUT=10,
    GDAL_CACHEMAX=64 * 1024 * 1024,
    CPL_VSIL_CURL_CACHE_SIZE=16 * 1024 * 1024,
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
    VSI_CACHE=True,
)
PALETTE = [
    "a52a2a",
    "c4813e",
    "e6c26b",
    "fff2a8",
    "d9ef8b",
    "a6d96a",
    "66bd63",
    "1a9850",
    "006837",
]
VIS = {
    "tvi-green": (["B11", "B8A", "B04"], [600, 700, 400], [4300, 5400, 2800], 1.1),
    "tvi-red": (["B8A", "B11", "B04"], [700, 600, 400], [5400, 4300, 2800], 1.1),
    "tvi-rgb": (["B04", "B03", "B02"], [200, 300, 700], [3000, 2500, 2300], 1.35),
    "tvi-ndvi": (["B08", "B04"], [-0.2], [0.9], 1),
    "landsat-tvi-true": (
        ["red", "green", "blue"],
        [0.03, 0.03, 0],
        [0.25, 0.25, 0.25],
        1.2,
    ),
    "landsat-tvi-agri": (
        ["swir16", "nir08", "red"],
        [0.05, 0.05, 0.03],
        [0.5, 0.55, 0.3],
        0.9,
    ),
    "landsat-tvi-false": (
        ["nir08", "swir16", "red"],
        [0.05, 0.05, 0.03],
        [0.6, 0.55, 0.3],
        1.2,
    ),
    "landsat-tvi-ndvi": (["nir08", "red"], [-0.2], [0.9], 1),
    "bdc-cbers-rgb-proposta": (["rgb"], [0, 0, 0], [255, 255, 255], 1),
}


def visualize(data, valid, minimum, maximum, gamma, ndvi=False):
    data = np.asarray(data, dtype=np.float64)
    valid = np.asarray(valid, dtype=bool).copy()
    if ndvi:
        denominator = data[0] + data[1]
        valid &= (data >= 0).all(axis=0) & (denominator != 0)
        data = np.divide(
            data[0] - data[1],
            denominator,
            out=np.zeros_like(denominator),
            where=denominator != 0,
        )[None]
    valid &= np.isfinite(data).all(axis=0)
    low, high = np.asarray(minimum)[:, None, None], np.asarray(maximum)[:, None, None]
    if (
        not np.isfinite(gamma)
        or gamma <= 0
        or not np.isfinite(low).all()
        or not np.isfinite(high).all()
        or np.any(high <= low)
    ):
        raise ValueError("Intervalo ou gamma inválido")
    normalized = np.clip((np.nan_to_num(data) - low) / (high - low), 0, 1)
    if ndvi:
        palette = np.array(
            [[int(c[i : i + 2], 16) for i in (0, 2, 4)] for c in PALETTE]
        )
        positions = normalized[0] * (len(palette) - 1)
        rgb = np.stack(
            [
                np.interp(positions, np.arange(len(palette)), palette[:, c])
                for c in range(3)
            ]
        )
    else:
        rgb = normalized ** (1 / gamma) * 255
    rgba = np.concatenate(
        [np.rint(rgb).clip(0, 255).astype("uint8"), valid[None].astype("uint8") * 255]
    )
    rgba[:3, ~valid] = 0
    return np.moveaxis(rgba, 0, -1)
