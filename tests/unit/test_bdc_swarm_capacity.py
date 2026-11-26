from pathlib import Path

import yaml


def test_bdc_workers_isolate_queues_with_bounded_prefetch_and_shared_coordination():
    config = yaml.safe_load((Path(__file__).resolve().parents[2] / 'config/swarm/prod.compose.yml').read_text())
    services = config['services']
    for name, queue, replicas, concurrency in (
        ('celery_worker_bdc', 'bdc', 2, 2),
        ('celery_worker_bdc_offline', 'bdc_offline', 1, 1),
    ):
        service = services[name]
        assert service['stop_grace_period'] == '600s'
        assert service['deploy']['replicas'] == replicas
        assert '--concurrency=' + str(concurrency) in service['command']
        assert '--queues=' + queue in service['command']
        assert '--prefetch-multiplier=1' in service['command']
        assert service['environment']['SKIP_GEE_INIT'] == 'true'
        assert service['environment']['MONGODB_URL'] == services['tile']['environment']['MONGODB_URL']
        assert service['environment']['MONGODB_DB'] == services['tile']['environment']['MONGODB_DB']
        assert service['environment']['CELERY_BROKER_URL'] == services['tile']['environment']['CELERY_BROKER_URL']
        assert service['environment']['BDC_INPE_TOKEN_FILE'] == '/run/secrets/bdc_inpe_token'
        assert service['secrets'] == [{'source': 'bdc_inpe_token', 'target': 'bdc_inpe_token'}]
        assert service['deploy']['update_config']['failure_action'] == 'rollback'
        assert service['environment']['S3_ENDPOINT'] == 'http://s3-tiles:8333'
        assert 'BDC_TOKEN' not in service['environment']
    assert config['secrets']['bdc_inpe_token']['external'] is True


def test_bdc_origin_budget_is_consistent_and_limited_to_bdc_services():
    config = yaml.safe_load((Path(__file__).resolve().parents[2] / 'config/swarm/prod.compose.yml').read_text())
    expected = {'tile', 'celery_worker_bdc', 'celery_worker_bdc_offline'}
    configured = {name for name, service in config['services'].items()
                  if 'BDC_ORIGIN_SLOTS' in service.get('environment', {})}
    assert configured == expected
    assert {config['services'][name]['environment']['BDC_ORIGIN_SLOTS'] for name in expected} == {'4'}


def test_offline_parallelism_is_isolated_and_has_bounded_worker_memory():
    config = yaml.safe_load((Path(__file__).resolve().parents[2] / 'config/swarm/prod.compose.yml').read_text())
    services = config['services']
    configured = {name for name, service in services.items()
                  if 'BDC_OFFLINE_PARALLELISM' in service.get('environment', {})}
    assert configured == {'celery_worker_bdc_offline'}
    worker = services['celery_worker_bdc_offline']
    assert worker['environment']['BDC_OFFLINE_PARALLELISM'] == '4'
    assert worker['deploy']['resources']['limits']['memory'] == '2g'
    assert '--concurrency=1' in worker['command']
