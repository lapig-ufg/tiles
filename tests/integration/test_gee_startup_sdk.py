import subprocess
import sys
import textwrap


def test_real_sdk_initialization_waits_for_admission_then_builds_image():
    result = subprocess.run([sys.executable, '-c', textwrap.dedent('''
        import threading
        import io
        from pathlib import Path
        from unittest.mock import patch

        import ee
        import fakeredis
        import httplib2
        from google.oauth2.credentials import Credentials
        from app.core import gee_admission as admission

        fixtures = Path(ee.__file__).parent / 'tests'
        coordinator = admission.RedisAdmission(fakeredis.FakeRedis(), admission.AdmissionLimits(
            max_concurrent=1, rate_per_second=50, interactive_reserve=0,
            interactive_rate_reserve=0, wait_seconds=0,
        ))
        occupied = coordinator.acquire()
        requests = []
        def respond(transport, uri, method, body, headers, **kwargs):
            requests.append(uri)
            assert admission.startup_remaining_seconds() is not None
            assert coordinator.redis.zcard('gee:{admission}:shared:active') == 1
            if '/algorithms' in uri:
                content = (fixtures / 'algorithms.json').read_bytes()
            elif 'discovery' in uri:
                content = (fixtures / 'cloud_api_discovery_document.json').read_bytes()
            else:
                raise AssertionError(uri)
            return httplib2.Response({'status': 200}), content

        def catalog(url, **kwargs):
            assert url == 'https://storage.googleapis.com/earthengine-stac/catalog/catalog_deprecated.json'
            return io.BytesIO(b'{"links": []}')

        timer = threading.Timer(.1, occupied.release)
        timer.start()
        try:
            with patch.object(admission, 'get_admission_controller', return_value=coordinator), \\
                 patch.object(admission.GoogleAdmissionTransport, '_request_once', respond), \\
                 patch('urllib.request.urlopen', side_effect=catalog):
                with admission.gee_startup():
                    admission.initialize_gee_transport(Credentials(
                        token='test-token', quota_project_id='test-project'))
                image = ee.Image.constant(1).rename('test')
                assert 'test' in image.serialize()
                assert any('/algorithms' in uri for uri in requests)
                assert coordinator.redis.zcard('gee:{admission}:shared:active') == 0
        finally:
            timer.join()
            occupied.release()
    ''')], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
