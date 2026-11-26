from enum import Enum

from app.cache.keys import build_tile_cache_keys


def keys(period="WET", month=1, layer="landsat", composite_mode="BEST_IMAGE"):
    return build_tile_cache_keys(
        layer=layer, period=period, year=2020, month=month,
        visparam="landsat-tvi-false", geohash="abc", z=10, x=12, y=34,
        composite_mode=composite_mode,
    )


def test_periodo_sazonal_compartilha_png_entre_meses():
    first = keys(month=1)
    last = keys(month=12)
    assert first.png == last.png == "landsat_WET_2020_0_landsat-tvi-false_BEST_IMAGE/abc/10/12_34.png"
    assert first.path == last.path


def test_produto_mensal_preserva_meses_distintos():
    assert keys(period="MONTH", month=1).png != keys(period="MONTH", month=12).png
    assert keys(period="MONTH", month=1).path == "landsat_MONTH_2020_1_landsat-tvi-false_BEST_IMAGE/abc"


def test_periodos_e_composicoes_distintos_nao_compartilham_png():
    assert keys(period="WET").png != keys(period="DRY").png
    assert keys(composite_mode="BEST_IMAGE").png != keys(composite_mode="MOSAIC").png


def test_composicao_omitida_compartilha_o_padrao_best_image():
    assert keys(composite_mode=None).png == keys().png


def test_chaves_legadas_cobrem_composicao_padrao_omitida_e_explicita():
    result = keys(month=7, composite_mode=None)
    assert result.legacy_paths == (
        "landsat_WET_2020_7_landsat-tvi-false/abc",
        "landsat_WET_2020_7_landsat-tvi-false_BEST_IMAGE/abc",
    )
    assert result.legacy_pngs[0] == "landsat_WET_2020_7_landsat-tvi-false/abc/10/12_34.png"


def test_sentinel_nao_recebe_sufixo_de_composicao():
    assert keys(layer="s2_harmonized").path == "s2_harmonized_WET_2020_0_landsat-tvi-false/abc"


def test_enum_de_periodo_tem_chave_canonica_compativel():
    class Period(str, Enum):
        WET = "WET"
    result = keys(period=Period.WET)
    assert result.png == keys(period="WET").png
    assert "s2_harmonized" not in result.path


def test_chave_mensal_inalterada_nao_tem_fallback_redundante():
    result = keys(period="MONTH", month=6, composite_mode="MOSAIC")
    assert result.legacy_paths == ()
