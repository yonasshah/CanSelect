import os
import re
import mimetypes
from django.http import StreamingHttpResponse, Http404


class RangeFileWrapper:
    def __init__(self, filelike, blksize=8192, offset=0, length=None):
        self.filelike = filelike
        self.filelike.seek(offset, os.SEEK_SET)
        self.remaining = length
        self.blksize = blksize

    def close(self):
        if hasattr(self.filelike, 'close'):
            self.filelike.close()

    def __iter__(self):
        return self

    def __next__(self):
        if self.remaining is None:
            data = self.filelike.read(self.blksize)
            if data:
                return data
            raise StopIteration()
        if self.remaining <= 0:
            raise StopIteration()
        data = self.filelike.read(min(self.remaining, self.blksize))
        if not data:
            raise StopIteration()
        self.remaining -= len(data)
        return data


def serve_media_with_range(request, path, document_root=None):
    """Serve media files with HTTP Range support so video/audio can seek."""
    full_path = os.path.normpath(os.path.join(document_root, path))
    root = os.path.normpath(document_root)
    # Prevent directory traversal
    if not full_path.startswith(root) or not os.path.isfile(full_path):
        raise Http404("File not found")

    size = os.path.getsize(full_path)
    content_type, _ = mimetypes.guess_type(full_path)
    content_type = content_type or 'application/octet-stream'

    range_header = request.headers.get('Range', '').strip()
    range_match = re.match(r'bytes=(\d+)-(\d*)', range_header)

    if range_match:
        start = int(range_match.group(1))
        end = range_match.group(2)
        end = int(end) if end else size - 1
        end = min(end, size - 1)
        length = end - start + 1
        resp = StreamingHttpResponse(
            RangeFileWrapper(open(full_path, 'rb'), offset=start, length=length),
            status=206,
            content_type=content_type,
        )
        resp['Content-Length'] = str(length)
        resp['Content-Range'] = f'bytes {start}-{end}/{size}'
    else:
        resp = StreamingHttpResponse(
            RangeFileWrapper(open(full_path, 'rb')),
            content_type=content_type,
        )
        resp['Content-Length'] = str(size)

    resp['Accept-Ranges'] = 'bytes'
    return resp