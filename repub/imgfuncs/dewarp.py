import cv2
import numpy as np
import tempfile
import os
import logging

from page_dewarp.options import Config
from page_dewarp.image import WarpedImage

def dewarp(
    input_image,
    no_binary: bool = True,
    shrink: int = 1,
    x_margin: int = 0,
    y_margin: int = 0,
    logger = None,
    debug_level: int = 0
) -> np.ndarray:
    """
    Dewarp a document image using the page_dewarp library.

    Args:
        input_image: Path to input image file or numpy array
        no_binary: If True, disable binary thresholding (equivalent to -nb 1)
        shrink: Downscaling factor for remapping (equivalent to -s)
        x_margin: Reduced pixels to ignore near L/R edge (equivalent to -x)
        y_margin: Reduced pixels to ignore near T/B edge (equivalent to -y)
        debug_level: Debug level (0-3)

    Returns:
        np.ndarray: The dewarped image as a numpy array

    Example:
        # From file path
        dewarped = dewarp("input.jpg")

        # From numpy array
        img = cv2.imread("input.jpg")
        dewarped = dewarp(img)

        # With custom parameters matching CLI: -nb 1 -s 1 -x 0 -y 0
        dewarped = dewarp("input.jpg", no_binary=True, shrink=1, x_margin=0, y_margin=0)
    """
    if logger == None:
        logger = logging.getLogger('repub.dewarp')

    # Create config with specified parameters
    config = Config(
        NO_BINARY=1 if no_binary else 0,
        REMAP_DECIMATE=shrink,
        PAGE_MARGIN_X=x_margin,
        PAGE_MARGIN_Y=y_margin,
        DEBUG_LEVEL=debug_level,
        DEBUG_OUTPUT="file"  # Avoid screen output for programmatic use
    )

    # Handle input - create temporary file if input is numpy array
    temp_file = None
    input_path = None

    try:
        # Create temporary file for numpy array input
        temp_file = tempfile.NamedTemporaryFile(suffix='.png', delete=False)
        cv2.imwrite(temp_file.name, input_image)
        input_path = temp_file.name
        temp_file.close()

        # Process the image
        warped_img = WarpedImage(input_path, config=config)

        if not warped_img.written:
            return None

        # Read the output image
        output_img = cv2.imread(warped_img.outfile)
        os.remove(warped_img.outfile)

        return output_img

    except Exception as e:
        logger.exception('Unable to dewarp the image. Error %s', e)
        return None

    finally:
        # Clean up temporary file if created
        if temp_file and os.path.exists(temp_file.name):
            os.unlink(temp_file.name)

if __name__ == "__main__":
    # Example usage
    import sys

    if len(sys.argv) < 2:
        print("Usage: python dewarp_function.py <input_image>")
        sys.exit(1)

    input_file = sys.argv[1]
    image = cv2.imread(input_file)
    filename = os.path.basename(input_file)
    output_file = 'warped_%s' % filename

    try:
        result = dewarp(image)
        print(f"Successfully processed image. Output shape: {result.shape}")
        cv2.imwrite(output_file, result)

    except Exception as e:
        print(f"Error processing image: {e}")
        sys.exit(1)
