import io
from typing import Optional

from PIL import Image, ImageDraw, ImageFont
from fastapi import HTTPException

from app.core.config import logger


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