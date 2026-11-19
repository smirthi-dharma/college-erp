"""Small helpers for the view tests: an in-memory picture and cleanup of files the tests upload."""
import io
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image

STRONG = 'Tr0ub4dor-and-3-horses'  # pragma: allowlist secret


def picture(name='me.png'):
    buffer = io.BytesIO()
    Image.new('RGB', (4, 4), 'red').save(buffer, 'PNG')
    return SimpleUploadedFile(name, buffer.getvalue(), content_type='image/png')


class UploadCleanupMixin:
    """Delete every file a test wrote under MEDIA_ROOT, so test runs leave no pictures behind."""

    def setUp(self):
        super().setUp()
        self._media_before = self._media_files()
        self.addCleanup(self._remove_new_media)

    @staticmethod
    def _media_files():
        root = Path(settings.MEDIA_ROOT)
        return {p for p in root.rglob('*') if p.is_file()} if root.exists() else set()

    def _remove_new_media(self):
        for path in self._media_files() - self._media_before:
            path.unlink(missing_ok=True)


def message_texts(response):
    """The flash messages shown on a rendered page (or queued for the next one after a redirect)."""
    from django.contrib.messages import get_messages
    if getattr(response, 'context', None) is not None and 'messages' in response.context:
        return [str(m) for m in response.context['messages']]
    return [str(m) for m in get_messages(response.wsgi_request)]
