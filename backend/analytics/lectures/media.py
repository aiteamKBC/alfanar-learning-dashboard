"""Authenticated media delivery. Microsoft credentials never enter browser URLs."""
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from django.http import HttpResponse, StreamingHttpResponse
from rest_framework.response import Response

from ..attendance.graph_client import GraphClient, GraphError, meeting_path


def open_recording(asset, byte_range=None, client=None):
    client = client or GraphClient.from_environment()
    client.authenticate()
    if getattr(asset, "provider", "teams") == "onedrive":
        url = "https://graph.microsoft.com/v1.0/drives/" + urllib.parse.quote(asset.drive_id, safe="")
        url += "/items/" + urllib.parse.quote(asset.source_id, safe="") + "/content"
    else:
        url = "https://graph.microsoft.com/v1.0" + meeting_path(asset.organizer_id, asset.meeting_id)
        url += "/recordings/" + urllib.parse.quote(asset.source_id, safe="") + "/content"
    for _ in range(4):
        host = urllib.parse.urlsplit(url).hostname or ""
        headers = {"Accept": "video/mp4"}
        if host == "graph.microsoft.com":
            headers["Authorization"] = "Bearer " + client._token
        if byte_range:
            headers["Range"] = byte_range
        # Retry an initial read-only connection once before starting a stream.
        # A fresh signed storage redirect is obtained on the next browser retry.
        for attempt in range(2):
            try:
                upstream = client._opener.open(urllib.request.Request(url, headers=headers), timeout=30)
                break
            except urllib.error.HTTPError as exc:
                if exc.code in (500, 502, 503, 504) and attempt == 0:
                    exc.close()
                    getattr(client, "_sleep", time.sleep)(0.5)
                    continue
                upstream = exc
                break
            except (urllib.error.URLError, OSError):
                if attempt:
                    raise
                getattr(client, "_sleep", time.sleep)(0.5)
        try:
            if isinstance(upstream, urllib.error.HTTPError):
                raise upstream
            return upstream
        except urllib.error.HTTPError as exc:
            if exc.code not in (301, 302, 303, 307, 308):
                raise
            location = exc.headers.get("Location", "")
            parsed = urllib.parse.urlsplit(location)
            permitted = parsed.hostname == "graph.microsoft.com" or any(
                (parsed.hostname or "").endswith(suffix) for suffix in
                (".sharepoint.com", ".sharepoint-df.com", ".onedrive.com", ".1drv.com", ".blob.core.windows.net", ".media.azure.net", ".microsoftstream.com")
            )
            if parsed.scheme != "https" or parsed.username or parsed.password or not permitted:
                exc.close()
                raise GraphError(502, "UnsafeMediaRedirect") from None
            # No bearer token is forwarded outside graph.microsoft.com.
            url = location
            exc.close()
    raise GraphError(502, "TooManyMediaRedirects")


def resource_response(request, asset):
    if not asset.readable:
        return Response({"error": {"message": "This source file is not currently available."}}, status=404)
    if asset.kind == "transcript":
        vtt = request.query_params.get("format") == "vtt"
        content = asset.vtt_content if vtt else asset.text_content
        if not content:
            return Response({"error": {"message": "The transcript content is unavailable."}}, status=404)
        response = HttpResponse(content, content_type="text/vtt; charset=utf-8" if vtt else "text/plain; charset=utf-8")
        suffix = "vtt" if vtt else "txt"
        response["Content-Disposition"] = f'attachment; filename="lecture-{asset.lecture_id}-{asset.pk}.{suffix}"'
        response["Cache-Control"] = "private, no-store"
        return response
    byte_range = request.headers.get("Range")
    if byte_range and not re.fullmatch(r"bytes=(?:\d+-\d*|-\d+)", byte_range):
        return Response({"error": {"message": "A single valid byte range is required."}}, status=416)
    try:
        upstream = open_recording(asset, byte_range)
    except (GraphError, urllib.error.URLError, OSError, ValueError):
        return Response({"error": {"message": "The recording could not be opened. Try again, or use the recordings folder."}}, status=502)

    def chunks():
        try:
            while True:
                chunk = upstream.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            upstream.close()

    response = StreamingHttpResponse(chunks(), status=upstream.status, content_type="video/mp4")
    for name in ("Content-Length", "Content-Range", "Accept-Ranges"):
        if upstream.headers.get(name):
            response[name] = upstream.headers[name]
    response["Cache-Control"] = "private, no-store"
    return response
