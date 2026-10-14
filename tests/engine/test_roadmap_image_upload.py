"""What the roadmap image upload endpoint actually stores.

Roadmap writers (community contributors, trial writers included) upload images that are served
to every reader, so the stored file must carry none of the uploader's metadata and must be named
for what it really is. The pipeline-level rules live in test_image_utils.py; these tests check
them end to end through `/api/v1/roadmap/<id>/upload-image/`, including the 400s a refused
upload returns.
"""
from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

from tests.factories import ConceptFactory, ConceptTrophyGroupFactory, ProfileFactory
from tests.images import GPS_IFD_TAG, photo_with_gps
from trophies import image_utils
from trophies.image_utils import ALLOWED_FORMATS_TEXT, PROCESSING_FAILED_MESSAGE
from trophies.models import Roadmap

pytestmark = pytest.mark.django_db

STORED_DIR = 'roadmaps/images'


@pytest.fixture
def media_root(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    return tmp_path


@pytest.fixture
def writer_client(client):
    client.force_login(ProfileFactory(roadmap_role='writer').user)
    return client


@pytest.fixture
def roadmap():
    concept = ConceptFactory()
    ctg = ConceptTrophyGroupFactory(concept=concept, trophy_group_id='default')
    return Roadmap.objects.create(concept=concept, concept_trophy_group=ctg, status='draft')


def _upload(client, roadmap, image, watermark='true'):
    return client.post(
        f'/api/v1/roadmap/{roadmap.id}/upload-image/',
        {'image': image, 'watermark': watermark},
        HTTP_CF_RAY='test-ray',  # skip the Cloudflare-bypass redirect for direct-origin requests
    )


def _stored(media_root):
    folder = media_root / STORED_DIR
    return sorted(folder.glob('*')) if folder.exists() else []


def _gps_on_disk(path):
    return Image.open(path).getexif().get(GPS_IFD_TAG)


def test_a_greyscale_phone_photo_is_stored_watermarked_without_its_location(writer_client, roadmap, media_root):
    resp = _upload(writer_client, roadmap, photo_with_gps(mode='L'))

    assert resp.status_code == 201, resp.content
    [stored] = _stored(media_root)
    assert stored.suffix == '.jpg'
    assert _gps_on_disk(stored) is None


def test_a_png_named_like_a_web_page_is_stored_as_a_png(writer_client, roadmap, media_root):
    upload = photo_with_gps(fmt='PNG', name='x.html')
    resp = _upload(writer_client, roadmap, upload, watermark='false')

    assert resp.status_code == 201, resp.content
    [stored] = _stored(media_root)
    assert stored.name.endswith('_x.png')
    assert resp.json()['url'].split('?')[0].endswith('_x.png')
    assert _gps_on_disk(stored) is None


def test_an_unprocessable_image_is_refused_and_nothing_is_stored(writer_client, roadmap, media_root, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError('simulated Pillow failure')
    monkeypatch.setattr(image_utils, '_apply_watermark', broken)

    resp = _upload(writer_client, roadmap, photo_with_gps())

    assert resp.status_code == 400
    assert resp.json() == {'error': PROCESSING_FAILED_MESSAGE}
    assert _stored(media_root) == []


def test_a_rejected_format_reads_as_a_sentence_not_a_list(writer_client, roadmap, media_root):
    """`str()` of a ValidationError is `"['...']"`; the editor shows `error` to the writer verbatim."""
    buf = BytesIO()
    Image.new('RGB', (4, 4)).save(buf, format='TIFF')

    resp = _upload(writer_client, roadmap, SimpleUploadedFile('scan.tif', buf.getvalue()))

    assert resp.status_code == 400
    assert resp.json() == {'error': f'Image must be a valid {ALLOWED_FORMATS_TEXT} image.'}
    assert _stored(media_root) == []
