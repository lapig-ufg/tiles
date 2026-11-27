import json
from types import SimpleNamespace

import numpy as np
import pytest
from rio_tiler.errors import TileOutsideBounds

from tests.test_bdc_offline_tiles import state, warm


def source_masks(state, monkeypatch, *, masked=True, ndvi=False, value=20, error=None):
    from app.modules.bdc import service as module
    bands = ['nir', 'red'] if ndvi else ['red', 'green', 'blue']
    item = {'id': 'scene', 'assets': {band: {'href': 'https://data.inpe.br/' + band + '.tif',
                                          'type': 'image/tiff', 'raster:bands': [{'nodata': 0}]} for band in bands}}
    profile = {'id': 'rgb', 'bands': bands, 'ndvi': ndvi, 'min': [-1] if ndvi else [0, 0, 0],
               'max': [1] if ndvi else [255, 255, 255], 'gamma': 1, 'scale': 1, 'offset': 0}
    calls = []
    class Bridge:
        def __init__(self, remote): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def url(self, url): return url
    class Reader:
        def __init__(self, url): self.dataset = SimpleNamespace(nodata=0)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def tile(self, *args, **kwargs):
            calls.append(1)
            if error:
                raise error
            return SimpleNamespace(array=np.ma.array(np.full((1, 256, 256), value, dtype=float),
                                                     mask=np.full((1, 256, 256), masked)))
    monkeypatch.setattr(module, 'RangeBridge', Bridge)
    monkeypatch.setattr(module, 'Reader', Reader)
    monkeypatch.setattr(state.service, 'detail', lambda *args: {'item': item, 'profiles': [profile]})
    monkeypatch.setattr(state.service, 'render_pixels', module.BdcService.render_pixels.__get__(state.service))
    return calls


def test_source_masks_prove_absence_only_for_requested_render_grid(state, monkeypatch):
    source_masks(state, monkeypatch)
    result = warm(state)
    assert result['status'] == 'no_data'
    proof = result['evidence']
    assert proof['kind'] == 'bdc_tile_no_valid_source_pixels'
    assert proof['collection'] == 'landsat-2' and proof['item'] == 'scene' and proof['profile'] == 'rgb'
    assert [proof['z'], proof['x'], proof['y']] == [6, 0, 0]
    assert proof['raw_valid_pixels'] == 0
    assert proof['valid_pixels_by_band'] == [0, 0, 0]
    assert proof['width'] == proof['height'] == 256
    assert state.storage.uploads == 0


@pytest.mark.parametrize('value,ndvi', [(-10, True), (float('nan'), False), (255, False)])
def test_invalid_visualization_without_source_absence_remains_failure(state, monkeypatch, value, ndvi):
    source_masks(state, monkeypatch, masked=False, value=value, ndvi=ndvi)
    result = warm(state)
    assert result['status'] == 'failed'
    assert result['reason'] == 'invalid_tile'
    assert 'evidence' not in result


def test_outside_required_asset_has_explicit_scoped_evidence(state, monkeypatch):
    source_masks(state, monkeypatch, error=TileOutsideBounds('outside'))
    result = warm(state)
    assert result['status'] == 'no_data'
    assert result['evidence']['kind'] == 'bdc_tile_outside_asset_bounds'
    assert result['evidence']['outside_band'] == 'red'


def test_transport_failure_never_becomes_absence(state, monkeypatch):
    source_masks(state, monkeypatch, error=IOError('read failed'))
    with pytest.raises(IOError):
        warm(state)


def test_legacy_transparent_cache_is_revalidated_once_and_sidecar_binds_png(state, monkeypatch):
    calls = source_masks(state, monkeypatch)
    state.service.render('landsat-2', 'scene', 6, 0, 0, 'rgb', 'png', _offline_lookup=False)
    for path in (state.service.root / 'tiles').glob('*.diagnostics.json'):
        path.unlink()
    before = len(calls)
    assert warm(state)['status'] == 'no_data'
    assert len(calls) == before + 3
    assert warm(state)['status'] == 'no_data'
    assert len(calls) == before + 3
    sidecar = next((state.service.root / 'tiles').glob('*.diagnostics.json'))
    proof = json.loads(sidecar.read_text())
    proof['content_sha256'] = '0' * 64
    sidecar.write_text(json.dumps(proof))
    assert warm(state)['status'] == 'no_data'
    assert len(calls) == before + 6


def test_sidecar_failure_keeps_rendered_image_without_proof(state, monkeypatch, caplog):
    from app.modules.bdc import service as module
    source_masks(state, monkeypatch)
    original = module.atomic_write
    def write(path, data):
        if str(path).endswith('.diagnostics.json'):
            raise OSError('storage unavailable')
        return original(path, data)
    monkeypatch.setattr(module, 'atomic_write', write)
    data, stats = state.service.render('landsat-2', 'scene', 6, 0, 0, 'rgb', 'png', _offline_lookup=False)
    assert data.startswith(b'\x89PNG')
    assert 'tile_diagnostics' not in stats
    assert 'diagnostic' in caplog.text
    assert warm(state)['status'] == 'failed'


@pytest.mark.parametrize('mutation', [
    {'schema_version': True}, {'item': 'other'}, {'renderer_revision': 'wrong'},
    {'content_sha256': '0' * 64}, {'valid_pixels_by_band': [0, 0]},
    {'valid_pixels_by_band': [65536, 65536, 65536], 'raw_valid_pixels': 0},
    {'read_complete': False},
])
def test_invalid_proof_is_revalidated(state, monkeypatch, mutation):
    calls = source_masks(state, monkeypatch)
    assert warm(state)['status'] == 'no_data'
    sidecar = next((state.service.root / 'tiles').glob('*.diagnostics.json'))
    proof = json.loads(sidecar.read_text())
    proof.update(mutation)
    sidecar.write_text(json.dumps(proof))
    before = len(calls)
    assert warm(state)['status'] == 'no_data'
    assert len(calls) == before + 3


def test_expired_source_proof_requires_fresh_render(state, monkeypatch):
    from datetime import datetime, timedelta, timezone
    calls = source_masks(state, monkeypatch)
    assert warm(state)['status'] == 'no_data'
    sidecar = next((state.service.root / 'tiles').glob('*.diagnostics.json'))
    proof = json.loads(sidecar.read_text())
    proof['checked_at'] = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    sidecar.write_text(json.dumps(proof))
    before = len(calls)
    assert warm(state)['status'] == 'no_data'
    assert len(calls) == before + 3


def test_failed_sidecar_refresh_preserves_old_cache_pair(state, monkeypatch):
    from app.modules.bdc import service as module
    source_masks(state, monkeypatch)
    assert warm(state)['status'] == 'no_data'
    sidecar = next((state.service.root / 'tiles').glob('*.diagnostics.json'))
    old = sidecar.read_bytes()
    source_masks(state, monkeypatch, masked=False, ndvi=True, value=-10)
    original = module.atomic_write
    def write(path, data):
        if str(path).endswith('.diagnostics.json'):
            raise OSError('storage unavailable')
        return original(path, data)
    monkeypatch.setattr(module, 'atomic_write', write)
    _, stats = state.service.render('landsat-2', 'scene', 6, 0, 0, 'rgb', 'png',
                                   _offline_lookup=False, _refresh=True)
    assert 'tile_diagnostics' not in stats
    assert sidecar.read_bytes() == old
