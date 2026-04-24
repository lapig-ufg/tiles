import io
from functools import lru_cache
from typing import Optional

from PIL import Image, ImageDraw, ImageFont
from fastapi import HTTPException
from fastapi.responses import Response

from app.core.config import logger


_NO_STORE = "no-store, must-revalidate"

# Códigos curtos (machine-readable) para o header X-Error-Reason.
# Nunca expor mensagem bruta da exceção (pode conter host interno, stack, etc.).
REASON_RATE_LIMIT = "ee_rate_limit"
REASON_BAND_MISSING = "ee_band_missing"
REASON_UNAVAILABLE = "ee_unavailable"
REASON_TIMEOUT = "ee_timeout"
REASON_INVALID_PARAMS = "invalid_params"
REASON_INTERNAL = "internal_error"

_STATUS_TO_REASON = {
    429: REASON_RATE_LIMIT,
    502: REASON_UNAVAILABLE,
    503: REASON_UNAVAILABLE,
    504: REASON_TIMEOUT,
    400: REASON_INVALID_PARAMS,
    422: REASON_INVALID_PARAMS,
    404: REASON_INVALID_PARAMS,
}


@lru_cache(maxsize=8)
def _error_png_bytes(reason: str) -> bytes:
    """PNG placeholder curto (~300–500 B) com o código do erro.

    Corpo existe só para `curl` direto — OpenLayers não renderiza resposta
    com status != 200, então browsers enxergam tile transparente.

    Cacheado por `reason` (≤ 8 valores distintos) para evitar render Pillow
    repetido sob pico de erro. Thread-safe (lru_cache usa lock interno).
    """
    img = generate_error_image(reason)
    img.seek(0)
    return img.read()


class _TileErrorResponseCallable:
    """Callable + `.from_exception` para permitir `tile_error_response(...)` e
    `tile_error_response.from_exception(exc)`."""

    def __call__(
        self,
        *,
        status_code: int,
        reason: str,
        retry_after: Optional[int] = None,
    ) -> Response:
        headers = {
            "Cache-Control": _NO_STORE,
            "X-Error-Reason": reason,
        }
        # Retry-After é válido em 429 e 503 (RFC 7231 §7.1.3). Mandamos em 429
        # sempre (default 30s) e em 503 apenas se caller passou valor explícito
        # (ex: circuit breaker conhece o tempo restante do cooldown).
        if status_code == 429:
            headers["Retry-After"] = str(retry_after if retry_after is not None else 30)
        elif status_code == 503 and retry_after is not None:
            headers["Retry-After"] = str(retry_after)
        return Response(
            content=_error_png_bytes(reason),
            status_code=status_code,
            media_type="image/png",
            headers=headers,
        )

    def from_exception(self, exc: BaseException) -> Response:
        """Infere status e reason a partir do tipo da exceção."""
        # ee.EEException com "no band named" → 500/ee_band_missing.
        # Import local evita custo de import global e permite stubs de teste.
        try:
            import ee  # type: ignore[import-untyped]
            ee_exception_cls: type = ee.EEException
        except Exception:  # pragma: no cover
            ee_exception_cls = ()  # isinstance(_, ()) é sempre False

        if isinstance(exc, ee_exception_cls) and "no band named" in str(exc).lower():
            return self(status_code=500, reason=REASON_BAND_MISSING)

        if isinstance(exc, HTTPException):
            status = exc.status_code
            reason = _STATUS_TO_REASON.get(status, REASON_INTERNAL)
            return self(status_code=status, reason=reason)

        return self(status_code=500, reason=REASON_INTERNAL)


tile_error_response = _TileErrorResponseCallable()


class AppError(Exception):
    """Base application error"""
    def __init__(self, message: str, status_code: int = 500):
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


class TileGenerationError(AppError):
    """Error during tile generation"""
    def __init__(self, message: str, tile_info: Optional[dict] = None):
        super().__init__(message, status_code=500)
        self.tile_info = tile_info


def handle_exception(e: Exception, context: str = "") -> HTTPException:
    """Handle exceptions and log them appropriately"""
    error_id = id(e)
    
    if isinstance(e, HTTPException):
        logger.warning(f"HTTP exception in {context}: {e.detail}")
        return e
    
    elif isinstance(e, AppError):
        logger.error(f"App error in {context}: {e.message}", exc_info=True)
        return HTTPException(status_code=e.status_code, detail=e.message)
    
    else:
        logger.exception(f"Unexpected error in {context}")
        return HTTPException(
            status_code=500,
            detail=f"Internal server error (ref: {error_id})"
        )
def generate_error_image(error_message: str) -> io.BytesIO:
    # Load the provided image
    image_path = "data/template.png"
    image = Image.open(image_path)

    # Define the default font
    font = ImageFont.load_default()

    # Calculate the position for the text using textbbox
    draw = ImageDraw.Draw(image)
    text_bbox = draw.textbbox((0, 0), error_message, font=font)
    text_width, text_height = text_bbox[2] - text_bbox[0], text_bbox[3] - text_bbox[1]
    position = ((image.width - text_width) // 2, (image.height - text_height) // 2)

    # Add the text to the image
    draw.text(position, error_message, font=font, fill="black")

    # Save the modified image to a BytesIO object
    img_byte_arr = io.BytesIO()
    image.save(img_byte_arr, format='PNG')
    img_byte_arr.seek(0)
    return img_byte_arr