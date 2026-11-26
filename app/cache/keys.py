from dataclasses import dataclass
from enum import Enum


@dataclass(frozen=True)
class TileCacheKeys:
    path: str
    png: str
    legacy_paths: tuple[str, ...]
    legacy_pngs: tuple[str, ...]


def build_tile_cache_keys(
    *, layer: str, period: str | Enum, year: int, month: int,
    visparam: str, geohash: str, z: int, x: int, y: int,
    composite_mode: str | None = None,
) -> TileCacheKeys:
    period_value = period.value if isinstance(period, Enum) else period
    cache_month = 0 if period_value in ("WET", "DRY") else month
    composition = (composite_mode or "BEST_IMAGE") if layer == "landsat" else None

    def path_for(period_name, month_value, mode):
        prefix = f"{layer}_{period_name}_{year}_{month_value}_{visparam}"
        if layer == "landsat" and mode:
            prefix += f"_{mode}"
        return f"{prefix}/{geohash}"

    path = path_for(period_value, cache_month, composition)
    legacy = [path_for(period, month, composite_mode)]
    if layer == "landsat" and composition == "BEST_IMAGE":
        legacy.extend((path_for(period_value, month, None),
                       path_for(period_value, month, "BEST_IMAGE")))
    legacy_paths = tuple(dict.fromkeys(candidate for candidate in legacy if candidate != path))
    suffix = f"/{z}/{x}_{y}.png"
    return TileCacheKeys(
        path=path, png=path + suffix, legacy_paths=legacy_paths,
        legacy_pngs=tuple(candidate + suffix for candidate in legacy_paths),
    )
