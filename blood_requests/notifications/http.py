"""One tiny HTTP helper so provider backends are easy to mock in tests."""

import json
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_SECONDS = 15


class HttpResult:
    def __init__(self, status, body):
        self.status = status
        self.body = body

    def json(self):
        try:
            return json.loads(self.body or "{}")
        except ValueError:
            return {}


def post(url, *, json_body=None, form=None, headers=None, timeout=TIMEOUT_SECONDS):
    """POST and return an HttpResult. Network failures raise OSError subclasses."""
    headers = dict(headers or {})

    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers.setdefault("Content-Type", "application/json")
    else:
        data = urllib.parse.urlencode(form or {}).encode()
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")

    request = urllib.request.Request(url, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HttpResult(response.status, response.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as error:
        return HttpResult(error.code, error.read().decode("utf-8", "replace"))
