import io

import pytest
from PIL import Image


def encoded(mode='RGB', size=(256, 256), color='green', format='PNG'):
    output = io.BytesIO()
    Image.new(mode, size, color).save(output, format=format)
    return output.getvalue()


@pytest.mark.parametrize('data', [
    encoded(size=(1, 1)), encoded(color='white'),
    encoded(mode='RGBA', color=(12, 34, 56, 0)),
    b'<html>error</html>', encoded()[:-25], b'',
], ids=['dimensions', 'white', 'transparent', 'html', 'truncated', 'empty'])
def test_offline_rejects_invalid_images(data):
    from app.cache.sync_cache import InvalidTileError, validate_offline_image
    with pytest.raises(InvalidTileError):
        validate_offline_image(data)


@pytest.mark.parametrize('format', ['PNG', 'JPEG'])
def test_offline_accepts_valid_imagery(format):
    from app.cache.sync_cache import validate_offline_image
    assert validate_offline_image(encoded(format=format)) == 'image/' + format.lower()


def test_transparent_colored_pixel_does_not_make_white_tile_valid():
    from app.cache.sync_cache import InvalidTileError, validate_offline_image
    image = Image.new('RGBA', (256, 256), 'white')
    image.putpixel((0, 0), (0, 0, 0, 0))
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    with pytest.raises(InvalidTileError):
        validate_offline_image(stream.getvalue())


@pytest.mark.parametrize(('data', 'code'), [
    (encoded(color='white'), 'white_image'),
    (encoded(color='black'), 'black_image'),
    (encoded(mode='RGBA', color=(12, 34, 56, 0)), 'transparent_image'),
    (encoded(size=(1, 1)), 'invalid_dimensions'),
    (b'<html>token=secret</html>', 'corrupt_image'),
    (encoded()[:-25], 'corrupt_image'),
    (b'', 'empty_image'),
], ids=['white', 'black', 'transparent', 'dimensions', 'html', 'truncated', 'empty'])
def test_validation_preserves_specific_safe_diagnostics(data, code):
    import hashlib
    from app.cache.sync_cache import InvalidTileError, validate_offline_image
    with pytest.raises(InvalidTileError) as error:
        validate_offline_image(data)
    details = error.value.diagnostics
    assert details['validation_code'] == code
    assert details['byte_length'] == len(data)
    assert details['content_sha256'] == hashlib.sha256(data).hexdigest()
    assert 'secret' not in str(details)
    if code in {'white_image', 'black_image', 'transparent_image'}:
        assert details['image_format'] == 'PNG'
        assert details['width'] == details['height'] == 256


@pytest.mark.parametrize(('mode', 'format'), [('RGB', 'PNG'), ('RGBA', 'PNG'), ('RGB', 'JPEG')])
def test_offline_rejects_opaque_uniform_black_imagery(mode, format):
    from app.cache.sync_cache import InvalidTileError, validate_offline_image
    with pytest.raises(InvalidTileError) as error:
        validate_offline_image(encoded(mode=mode, color='black', format=format))
    assert error.value.diagnostics['validation_code'] == 'black_image'


def test_offline_accepts_dark_imagery_with_pixel_variation():
    from app.cache.sync_cache import validate_offline_image
    image = Image.new('RGB', (256, 256), 'black')
    image.putpixel((0, 0), (1, 2, 3))
    stream = io.BytesIO()
    image.save(stream, format='PNG')
    assert validate_offline_image(stream.getvalue()) == 'image/png'
