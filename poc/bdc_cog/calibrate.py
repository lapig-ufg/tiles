import json
from pathlib import Path

import numpy as np
import rasterio
from rio_tiler.io import Reader

from .campaign import REGIONS, choose_scene, reference
from .catalog import tile_at
from .compare import board, fit_radiometry, metrics
from .render import GDAL_OPTIONS, VIS, visualize
from .transport import RangeBridge, Transport, atomic_write


def inverse_png(rgb, minimum, maximum, gamma):
    value = np.asarray(rgb, dtype=float) / 255
    low, high = np.asarray(minimum)[:, None, None], np.asarray(maximum)[:, None, None]
    estimate = low + (high - low) * value**gamma
    lower = low + (high - low) * np.clip(value - 0.5 / 255, 0, 1) ** gamma
    upper = low + (high - low) * np.clip(value + 0.5 / 255, 0, 1) ** gamma
    return estimate, upper - lower


def main():
    root = Path(__file__).parent
    t = Transport(root / "cache/http")
    samples = []
    report = []
    for region, lon, lat in REGIONS:
        for period in ["WET", "DRY", "MONTH"]:
            x, y = tile_at(lon, lat, 14)
            row = dict(
                region=region,
                lon=lon,
                lat=lat,
                source="S2_L2A-1",
                layer="s2_harmonized",
                year=2024,
                month=8,
                period=period,
                z=14,
                x=x,
                y=y,
            )
            item, image_id = choose_scene(t, row)
            if not item:
                continue
            baseline = int(item["id"].split("_")[3][1:])
            offset = -1000 if baseline >= 400 else 0
            with RangeBridge(t) as bridge, rasterio.Env(**GDAL_OPTIONS):
                with Reader(bridge.url(item["assets"]["SCL"]["href"])) as src:
                    scl = src.tile(x, y, 14, tilesize=256, nodata=0).array
                    clear = (
                        np.isin(scl.data[0], [4, 5, 6]) & ~np.ma.getmaskarray(scl)[0]
                    )
                for vis in ["tvi-green", "tvi-rgb"]:
                    bands, minimum, maximum, gamma = VIS[vis]
                    row["visparam"] = vis
                    ref, url = reference(t, row, image_id)
                    values = []
                    valid = clear & (ref[..., 3] > 0)
                    for band in bands:
                        with Reader(bridge.url(item["assets"][band]["href"])) as src:
                            tile = src.tile(x, y, 14, tilesize=256, nodata=0).array
                            values.append(tile.data[0].astype(float) + offset)
                            valid &= ~np.ma.getmaskarray(tile)[0]
                    boa = np.array(values)
                    rgb = np.moveaxis(ref[..., :3], -1, 0)
                    toa, uncertainty = inverse_png(rgb, minimum, maximum, gamma)
                    valid &= ((rgb > 2) & (rgb < 253)).all(axis=0)
                    yy, xx = np.indices(valid.shape)
                    train = ((xx // 32 + yy // 32) % 2) == 0
                    samples.append(
                        dict(
                            region=region,
                            period=period,
                            vis=vis,
                            item=item["id"],
                            offset=offset,
                            boa=boa,
                            toa=toa,
                            valid=valid,
                            train=train,
                            ref=ref,
                            uncertainty=uncertainty,
                        )
                    )
                    print(region, period, vis, "pixels", valid.sum(), flush=True)
    proposals = {}
    for vis in ["tvi-green", "tvi-rgb"]:
        group = [s for s in samples if s["vis"] == vis]
        bands, minimum, maximum, gamma = VIS[vis]
        fits = []
        for c, band in enumerate(bands):
            xs = np.concatenate([s["boa"][c][s["valid"] & s["train"]] for s in group])
            ys = np.concatenate([s["toa"][c][s["valid"] & s["train"]] for s in group])
            fit = fit_radiometry(xs, ys, np.ones(len(xs), bool), minimum[c], maximum[c])
            fit["band"] = band
            fit["quantization_interval_max"] = float(
                max(
                    s["uncertainty"][c][s["valid"]].max()
                    for s in group
                    if s["valid"].any()
                )
            )
            fits.append(fit)
        proposal = dict(
            name=vis + "-bdc-c-proposta",
            bands=bands,
            min=[f["min"] for f in fits],
            max=[f["max"] for f in fits],
            gamma=gamma,
            offset_policy="baseline >= 04.00: -1000; hipótese explícita",
            fits=fits,
            method="TOA estimada de PNG não saturado; não equivale a radiometria TOA original; SCL 4/5/6; treino em blocos alternados de 32 pixels",
        )
        proposals[proposal["name"]] = proposal
        for sample in group:
            candidate = visualize(
                sample["boa"], sample["valid"], proposal["min"], proposal["max"], gamma
            )
            reference_rgba = sample["ref"].copy()
            held = sample["valid"] & ~sample["train"]
            reference_rgba[~held, 3] = 0
            candidate[~held, 3] = 0
            result = {
                k: sample[k] for k in ["region", "period", "vis", "item", "offset"]
            }
            result["metrics"] = metrics(reference_rgba, candidate)
            result["residual_rmse_dn"] = (
                [
                    float(
                        np.sqrt(
                            np.mean(
                                (
                                    sample["toa"][c][held]
                                    - (
                                        fits[c]["a"] * sample["boa"][c][held]
                                        + fits[c]["b"]
                                    )
                                )
                                ** 2
                            )
                        )
                    )
                    for c in range(3)
                ]
                if held.any()
                else None
            )
            report.append(result)
            board(
                reference_rgba,
                candidate,
                root
                / "evidence"
                / f"calibrated-{sample['region']}-{sample['period']}-{vis}.png",
                f"Validação espacial / {sample['region']} / {sample['period']} / {vis}",
            )
    green = proposals.get("tvi-green-bdc-c-proposta")
    if green:
        proposals["tvi-red-bdc-c-proposta"] = dict(
            green,
            name="tvi-red-bdc-c-proposta",
            bands=[green["bands"][i] for i in [1, 0, 2]],
            min=[green["min"][i] for i in [1, 0, 2]],
            max=[green["max"][i] for i in [1, 0, 2]],
            fits=[green["fits"][i] for i in [1, 0, 2]],
        )
    atomic_write(
        root / "evidence/proposed-visparams.json",
        json.dumps(proposals, ensure_ascii=False, indent=2).encode(),
    )
    atomic_write(
        root / "evidence/calibration.json",
        json.dumps(report, ensure_ascii=False, indent=2).encode(),
    )


if __name__ == "__main__":
    main()
