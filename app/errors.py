import io
from PIL import Image, ImageDraw, ImageFont
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