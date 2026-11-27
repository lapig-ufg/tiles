import time

import pytest

from tests.unit.test_offline_campaign_store import store
from tests.unit.test_bdc_offline_campaign import prepared_bdc
from app.modules.bdc.tile_validity import make_diagnostics


@pytest.mark.parametrize('mutation', [None, {'item': 'wrong'}, {'profile': 'wrong'}, {'z': 1},
                                     {'selection_id': 'b' * 64}, {'renderer_revision': 'wrong'},
                                     {'raw_valid_pixels': 1}, {'content_sha256': '0' * 64}])
def test_tile_absence_requires_exact_ready_selection_and_render(store, mutation):
    job, token = prepared_bdc(store)
    item = store.next_item(job, token)
    params = item['params']
    context = dict(collection=job['config']['bdc']['collection'], item='scene',
                   profile=params['visparam'], **{k: params[k] for k in ('x', 'y', 'z')})
    proof = make_diagnostics(dict(stage='source_masks_before_visualization', read_complete=True,
                                 width=256, height=256, bands=['red', 'green', 'blue'],
                                 raw_valid_pixels=0, valid_pixels_by_band=[0, 0, 0]),
                             context, job['config']['renderer_revision'], b'png')
    proof.update(kind='bdc_tile_no_valid_source_pixels', count=0, selection_id='a' * 64,
                 year=params['year'], period=params['period'])
    store.db.campaignImageSelections.insert_one(dict(_id='a' * 64, campaignId=job['_id'],
        pointId=item['point_ref'], revision=job['config']['bdc']['selectionRevision'],
        year=params['year'], period=params['period'], state='ready',
        params={'collection': context['collection']}, profiles=[params['visparam']],
        result={'complete': True, 'best_local_id': 'scene'}))
    diagnostics = dict(validation_code='transparent_image', content_sha256=proof['content_sha256'])
    if mutation:
        proof.update(mutation)
    store.complete_item(job, token, item, dict(status='no_data', reason='bdc_tile_no_data',
        expires_at=time.time()+100, evidence=proof, diagnostics=diagnostics))
    saved = store.items.find_one({'_id': item['_id']})
    assert saved['state'] == ('retry_wait' if mutation else 'no_data')
    if not mutation:
        assert saved['reason'] == 'bdc_tile_no_data'
