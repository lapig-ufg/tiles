import io

import numpy as np
from PIL import Image, ImageDraw
from scipy.ndimage import binary_erosion
from skimage.metrics import structural_similarity


def decode(data):
    with Image.open(io.BytesIO(data)) as image:
        if image.size != (256, 256):
            raise ValueError("Referência deve ter 256 × 256 pixels")
        return np.array(image.convert("RGBA"))


def metrics(reference, candidate, threshold=10):
    if reference.shape != candidate.shape or reference.shape != (256, 256, 4):
        raise ValueError("Imagens incompatíveis")
    left, right = reference[..., 3] > 0, candidate[..., 3] > 0
    valid = left & right
    result = {
        "valid_pixels": int(valid.sum()),
        "reference_pixels": int(left.sum()),
        "candidate_pixels": int(right.sum()),
        "coverage_disagreement_pct": float((left != right).mean() * 100),
        "threshold_dn": threshold,
    }
    if not valid.any():
        return dict(
            result,
            status="sem interseção válida",
            mae=None,
            rmse=None,
            ssim=None,
            histogram_tv=None,
            above_threshold_pct=None,
        )
    a, b = reference[..., :3].astype(float), candidate[..., :3].astype(float)
    delta = a - b
    result.update(
        status="medido",
        mae=np.abs(delta[valid]).mean(axis=0).tolist(),
        rmse=np.sqrt((delta[valid] ** 2).mean(axis=0)).tolist(),
        above_threshold_pct=float(
            (np.abs(delta[valid]).max(axis=1) > threshold).mean() * 100
        ),
    )
    interior = binary_erosion(valid, structure=np.ones((7, 7)))
    ssim = []
    for c in range(3):
        _, score = structural_similarity(
            a[..., c], b[..., c], data_range=255, full=True
        )
        ssim.append(float(score[interior].mean()) if interior.any() else None)
    result["ssim"] = ssim
    result["histogram_tv"] = [
        float(
            np.abs(
                np.bincount(reference[..., c][valid], minlength=256)
                - np.bincount(candidate[..., c][valid], minlength=256)
            ).sum()
            / (2 * valid.sum())
        )
        for c in range(3)
    ]
    return result


def board(reference, candidate, path, title):
    delta = np.abs(
        reference[..., :3].astype("int16") - candidate[..., :3].astype("int16")
    )
    common = (reference[..., 3] > 0) & (candidate[..., 3] > 0)
    delta = np.minimum(delta * 4, 255).astype("uint8")
    delta[~common] = [120, 0, 120]
    image = Image.new("RGB", (768, 300), "white")
    for x, pixels in [(0, reference), (256, candidate), (512, delta)]:
        tile = Image.fromarray(pixels)
        image.paste(tile, (x, 44), tile if tile.mode == "RGBA" else None)
    draw = ImageDraw.Draw(image)
    draw.text((5, 3), title, fill="black")
    for x, label in [(5, "GEE"), (261, "BDC"), (517, "Diferença ×4; roxo: sem par")]:
        draw.text((x, 25), label, fill="black")
    image.save(path)


def fit_radiometry(boa, toa, valid, minimum, maximum):
    boa, toa = np.asarray(boa, float), np.asarray(toa, float)
    mask = np.asarray(valid, bool) & np.isfinite(boa) & np.isfinite(toa)
    x, y = boa[mask], toa[mask]
    if len(x) < 30 or np.ptp(x) == 0:
        raise ValueError("Amostra radiométrica insuficiente")
    a, b = np.linalg.lstsq(np.column_stack([x, np.ones(len(x))]), y, rcond=None)[0]
    if a <= 0:
        raise ValueError("Ajuste radiométrico não monotônico")
    residual = y - (a * x + b)
    return {
        "a": float(a),
        "b": float(b),
        "n": len(x),
        "rmse": float(np.sqrt(np.mean(residual**2))),
        "min": float((minimum - b) / a),
        "max": float((maximum - b) / a),
    }
