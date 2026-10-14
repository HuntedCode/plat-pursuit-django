"""Image fixtures for the upload tests (test_image_utils.py, test_roadmap_image_upload.py)."""
from io import BytesIO

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

#: EXIF tag for the GPS block. A phone photo's location lives here.
GPS_IFD_TAG = 0x8825
#: EXIF Orientation tag (6 = the camera was turned 90 degrees clockwise).
ORIENTATION_TAG = 0x0112


def photo_with_gps(mode='RGB', fmt='JPEG', name='photo.jpg', size=(64, 64), orientation=None):
    """A photo carrying a GPS block, as a phone camera writes one."""
    exif = Image.Exif()
    exif[GPS_IFD_TAG] = {2: (40.0, 26.0, 46.0)}  # GPSLatitude
    if orientation is not None:
        exif[ORIENTATION_TAG] = orientation
    buf = BytesIO()
    Image.new(mode, size).save(buf, format=fmt, exif=exif)
    return SimpleUploadedFile(name, buf.getvalue())


def gps_of(stored):
    """The GPS block of a stored image, or None."""
    stored.seek(0)
    return Image.open(stored).getexif().get(GPS_IFD_TAG)
