"""Keep account requests and redirects on the Class Charts HTTPS origin."""
from contextlib import asynccontextmanager
from urllib.parse import urljoin, urlsplit

_HOST = "www.classcharts.com"
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_MAX_REDIRECTS = 5


class ClassChartsRequestError(Exception):
    """A request policy error whose message contains no account information."""


def validate_url(url):
    """Reject other hosts, HTTP, userinfo and ambiguous URL spellings."""
    try:
        if not isinstance(url, str) or "\\" in url or any(
            ord(char) <= 32 or ord(char) == 127 for char in url
        ):
            raise ValueError
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != _HOST
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError
    except (ValueError, TypeError):
        raise ClassChartsRequestError("Blocked an unsafe Class Charts request.") from None
    return url


def _redirect(response_url, location, status, method, data, headers):
    """Validate the next destination before forwarding any credentials."""
    try:
        target = validate_url(urljoin(str(response_url), location))
    except (ValueError, TypeError):
        raise ClassChartsRequestError("Blocked an invalid Class Charts redirect.") from None
    # Match the usual browser POST-to-GET behaviour; 307/308 retain the body.
    if (status == 303 and method != "HEAD") or (
        status in (301, 302) and method == "POST"
    ):
        method, data = "GET", None
        headers = {
            key: value for key, value in headers.items()
            if key.lower() not in {"content-type", "content-length", "transfer-encoding"}
        }
    return target, method, data, headers


def classcharts_request(session, method, url, *, data=None, headers=None,
                       params=None, timeout=10):
    """Make a synchronous request with bounded, same-origin redirects."""
    method = method.upper()
    headers = dict(headers or {})
    for attempt in range(_MAX_REDIRECTS + 1):
        validate_url(url)
        response = session.request(
            method, url, data=data, headers=headers, params=params,
            timeout=timeout, allow_redirects=False, verify=True,
        )
        location = response.headers.get("Location") or response.headers.get("URI")
        if response.status_code not in _REDIRECT_CODES or not location:
            return response
        try:
            if attempt == _MAX_REDIRECTS:
                raise ClassChartsRequestError("Too many Class Charts redirects.")
            url, method, data, headers = _redirect(
                response.url, location, response.status_code, method, data, headers
            )
            params = None
        finally:
            response.close()


@asynccontextmanager
async def async_classcharts_request(session, method, url, *, data=None,
                                   headers=None, params=None):
    """Make an async request without forwarding secrets to another origin."""
    method = method.upper()
    headers = dict(headers or {})
    for attempt in range(_MAX_REDIRECTS + 1):
        validate_url(url)
        response = await session.request(
            method, url, data=data, headers=headers, params=params,
            allow_redirects=False, ssl=True,
        )
        try:
            location = response.headers.get("Location") or response.headers.get("URI")
            if response.status not in _REDIRECT_CODES or not location:
                yield response
                return
            if attempt == _MAX_REDIRECTS:
                raise ClassChartsRequestError("Too many Class Charts redirects.")
            url, method, data, headers = _redirect(
                response.url, location, response.status, method, data, headers
            )
            params = None
        finally:
            response.release()
