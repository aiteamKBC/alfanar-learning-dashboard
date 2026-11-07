"""Read-only Microsoft Graph v1.0 client. Credentials/tokens never enter diagnostics."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from email.utils import parsedate_to_datetime

BASE = "https://graph.microsoft.com/v1.0"
SCOPE = "https://graph.microsoft.com/.default"


class GraphError(Exception):
    def __init__(self, status, code, *, operation="graph_read"):
        self.status = status
        self.code = re.sub(r"[^A-Za-z0-9_.-]", "", str(code))[:100] or "GraphRequestFailed"
        self.operation = operation
        super().__init__(f"Microsoft Graph {operation}: HTTP {status}, {self.code}")

    def diagnostic(self):
        return {"operation": self.operation, "status": self.status, "code": self.code}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        # Never forward a token or client secret to a redirect destination.
        return None


class GraphClient:
    def __init__(self, tenant, client_id, client_secret, *, scope=SCOPE, base_url=BASE,
                 opener=None, sleep=time.sleep, clock=time.time):
        try:
            self.tenant, self.client_id = str(uuid.UUID(tenant)), str(uuid.UUID(client_id))
        except (ValueError, TypeError, AttributeError):
            raise GraphError(0, "InvalidTenantOrClientID", operation="configuration") from None
        if not client_secret or scope != SCOPE or base_url.rstrip("/") not in (BASE, "https://graph.microsoft.com"):
            raise GraphError(0, "InvalidGraphConfiguration", operation="configuration")
        self._secret = client_secret
        self._token, self._expires = None, 0
        self._opener = opener or urllib.request.build_opener(NoRedirect())
        self._sleep, self._clock = sleep, clock
        self.requests = 0

    @classmethod
    def from_environment(cls):
        # Django already loads project .env values without exposing owner credentials.
        return cls(os.environ.get("MICROSOFT_GRAPH_TENANT_ID"), os.environ.get("MICROSOFT_GRAPH_CLIENT_ID"),
                   os.environ.get("MICROSOFT_GRAPH_CLIENT_SECRET"),
                   scope=os.environ.get("MICROSOFT_GRAPH_SCOPE", SCOPE),
                   base_url=os.environ.get("MICROSOFT_GRAPH_BASE_URL", BASE))

    def _request(self, request, operation):
        for attempt in range(4):
            self.requests += 1
            try:
                with self._opener.open(request, timeout=30) as response:
                    value = json.load(response)
                if not isinstance(value, dict):
                    raise GraphError(200, "InvalidResponse", operation=operation)
                return value
            except urllib.error.HTTPError as exc:
                try:
                    body = json.loads(exc.read())
                except (ValueError, OSError):
                    body = {}
                error = body.get("error", {}) if isinstance(body, dict) else {}
                code = error.get("code", "GraphRequestFailed") if isinstance(error, dict) else error
                # Whitelist the useful policy error, never retain arbitrary server messages.
                if isinstance(error, dict) and "No application access policy found" in error.get("message", ""):
                    code = "ApplicationAccessPolicyMissing"
                if exc.code not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise GraphError(exc.code, code, operation=operation) from None
                retry = exc.headers.get("Retry-After", "")
                try:
                    delay = float(retry) if retry else 2 ** attempt
                except ValueError:
                    try:
                        delay = parsedate_to_datetime(retry).timestamp() - self._clock()
                    except (ValueError, TypeError, OverflowError):
                        delay = 2 ** attempt
                # Long throttles fail safely; the next run keeps all previous evidence.
                if delay > 30:
                    raise GraphError(exc.code, "RetryLater", operation=operation) from None
                self._sleep(max(0, delay))
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == 3:
                    raise GraphError(0, "NetworkUnavailable", operation=operation) from None
                self._sleep(2 ** attempt)
            except (ValueError, UnicodeError):
                raise GraphError(200, "InvalidJSON", operation=operation) from None

    def authenticate(self):
        if self._token and self._clock() < self._expires:
            return
        form = urllib.parse.urlencode({"client_id": self.client_id, "client_secret": self._secret,
                                      "grant_type": "client_credentials", "scope": SCOPE}).encode()
        request = urllib.request.Request(f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/token", data=form,
                                         headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
        body = self._request(request, "authentication")
        token, expires = body.get("access_token"), body.get("expires_in")
        if not isinstance(token, str) or not token or not isinstance(expires, (int, float)) or expires <= 0:
            raise GraphError(200, "InvalidTokenResponse", operation="authentication")
        self._token, self._expires = token, self._clock() + max(1, expires - 60)

    @staticmethod
    def validate_url(url):
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "https" or parsed.netloc != "graph.microsoft.com" or
                not parsed.path.startswith("/v1.0/") or parsed.fragment):
            raise GraphError(0, "UnsafeGraphURL")

    def get(self, path):
        url = path if path.startswith("https://") else BASE + "/" + path.lstrip("/")
        self.validate_url(url)
        for attempt in range(2):
            self.authenticate()
            request = urllib.request.Request(url, headers={"Authorization": "Bearer " + self._token, "Accept": "application/json"})
            try:
                return self._request(request, "graph_read")
            except GraphError as exc:
                if exc.status != 401 or attempt:
                    raise
                self._token, self._expires = None, 0

    def collection(self, path):
        """Only return a complete, validated collection; partial pages are never accepted."""
        url = path if path.startswith("https://") else BASE + "/" + path.lstrip("/")
        original_path = urllib.parse.unquote(urllib.parse.urlsplit(url).path)
        visited, records = set(), {}
        while url:
            self.validate_url(url)
            if urllib.parse.unquote(urllib.parse.urlsplit(url).path) != original_path:
                raise GraphError(0, "PaginationResourceChanged")
            if url in visited or len(visited) >= 1000:
                raise GraphError(0, "PaginationLoop")
            visited.add(url)
            body = self.get(url)
            if not isinstance(body.get("value"), list):
                raise GraphError(200, "InvalidCollection")
            for item in body["value"]:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
                    raise GraphError(200, "MissingRecordID")
                if item["id"] in records and records[item["id"]] != item:
                    raise GraphError(200, "ConflictingDuplicateID")
                records[item["id"]] = item
            url = body.get("@odata.nextLink")
            if url is not None and (not isinstance(url, str) or not url.startswith("https://")):
                raise GraphError(200, "InvalidNextLink")
        return list(records.values()), len(visited)


def meeting_path(organizer, meeting):
    return "/users/" + urllib.parse.quote(organizer, safe="") + "/onlineMeetings/" + urllib.parse.quote(meeting, safe="")
