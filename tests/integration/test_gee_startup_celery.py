import subprocess
import sys
import textwrap


def test_prefork_worker_survives_startup_longer_than_default_alive_timeout():
    result = subprocess.run([sys.executable, '-c', textwrap.dedent('''
        import os
        import tempfile
        import time
        from pathlib import Path
        from unittest.mock import patch

        from celery.contrib.testing.worker import start_worker
        from app.core import gee_auth, mongodb
        from app.tasks.celery_app import celery_app

        celery_app.conf.update(
            broker_url=os.environ.get('TILES_TEST_BROKER', 'memory://'), result_backend='cache+memory://',
            imports=(), include=(), task_routes={}, task_queues=(),
            task_default_queue='startup_probe', worker_concurrency=1,
            worker_max_memory_per_child=0, worker_max_tasks_per_child=1,
            worker_prefetch_multiplier=1,
        )
        async def connect():
            pass

        def initialize(worker_id):
            time.sleep(5)

        @celery_app.task(name='startup_probe', ignore_result=True)
        def probe(marker):
            Path(marker).write_text('ready')

        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / 'result'
            with patch.object(gee_auth, 'initialize_earth_engine', initialize), \\
                 patch.object(mongodb, 'connect_to_mongo', connect):
                with start_worker(celery_app, pool='prefork', concurrency=1,
                                  use_eventloop=celery_app.conf.broker_url != 'memory://',
                                  perform_ping_check=False, shutdown_timeout=15):
                    for generation in range(2):
                        target = marker.with_name(str(generation))
                        probe.delay(str(target))
                        deadline = time.monotonic() + 14
                        while not target.exists() and time.monotonic() < deadline:
                            time.sleep(.1)
                        assert target.read_text() == 'ready'
    ''')], capture_output=True, text=True, timeout=50)
    assert result.returncode == 0, result.stderr
