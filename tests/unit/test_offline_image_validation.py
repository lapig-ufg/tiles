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
