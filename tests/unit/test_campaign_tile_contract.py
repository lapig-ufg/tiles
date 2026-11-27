import json
from pathlib import Path
import pytest
from app.cache.campaign_manifest import viewport_tiles

CONTRACT = json.loads((Path(__file__).parents[1] / 'fixtures/leaflet_tile_contract.json').read_text())


@pytest.mark.parametrize('case', CONTRACT['cases'])
def test_manifest_contains_leaflet_requested_tiles(case):
    requested = {(t['x'], t['y'], t['z']) for t in case['tiles']}
    prepared = {(t['x'], t['y'], t['z']) for t in viewport_tiles(
        case['lat'], case['lon'], [case['z']], case['width'], case['height'])}
    assert requested <= prepared
