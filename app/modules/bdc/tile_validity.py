import hashlib
import re
from datetime import datetime, timedelta, timezone

PROOF_TTL = 30 * 86400
CONTEXT_FIELDS = ('collection', 'item', 'profile', 'z', 'x', 'y')


def checked_time(document):
    try:
        value = datetime.fromisoformat(document['checked_at'])
        return value.astimezone(timezone.utc) if value.tzinfo is not None else None
    except (KeyError, TypeError, ValueError):
        return None


def valid_diagnostics(document, context, renderer, content_sha256):
    if not isinstance(document, dict) or type(document.get('schema_version')) is not int or document.get('schema_version') != 1:
        return False
    if any(document.get(key) != context.get(key) for key in CONTEXT_FIELDS):
        return False
    if (document.get('renderer_revision') != renderer or document.get('content_sha256') != content_sha256
            or not isinstance(content_sha256, str) or not re.fullmatch('[a-f0-9]{64}', content_sha256)):
        return False
    checked = checked_time(document)
    if checked is None or checked > datetime.now(timezone.utc) + timedelta(seconds=60):
        return False
    if document.get('width') != 256 or document.get('height') != 256:
        return False
    bands = document.get('bands')
    if not isinstance(bands, list) or not 1 <= len(bands) <= 3 or any(not isinstance(band, str) or not band for band in bands):
        return False
    if document.get('stage') == 'outside_asset_bounds':
        return document.get('outside_band') in bands
    counts = document.get('valid_pixels_by_band')
    raw = document.get('raw_valid_pixels')
    return (document.get('stage') == 'source_masks_before_visualization'
            and document.get('read_complete') is True
            and isinstance(counts, list) and len(counts) in (2, 3) and len(counts) == len(bands)
            and all(type(count) is int and 0 <= count <= 65536 for count in counts)
            and type(raw) is int and max(0, sum(counts) - (len(counts) - 1) * 65536) <= raw <= min(counts))


def make_diagnostics(raw, context, renderer, data):
    document = dict(raw, **context, schema_version=1, renderer_revision=renderer,
                    content_sha256=hashlib.sha256(data).hexdigest(),
                    checked_at=datetime.now(timezone.utc).isoformat())
    return document if valid_diagnostics(document, context, renderer, document['content_sha256']) else None


def absence_kind(document):
    if document.get('stage') == 'outside_asset_bounds':
        return 'bdc_tile_outside_asset_bounds'
    if document.get('stage') == 'source_masks_before_visualization' and document.get('raw_valid_pixels') == 0:
        return 'bdc_tile_no_valid_source_pixels'
    return None
