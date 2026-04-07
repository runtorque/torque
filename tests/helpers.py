import json
import sys
import types


class FakeJSONResponse:
    def __init__(self, payload=None, status=200, text=""):
        self.payload = payload
        self.status = status
        self.text = text


class FakeRequest:
    def __init__(self, body, headers=None):
        self._body = body
        self.headers = headers or {}

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        if isinstance(self._body, str):
            return json.loads(self._body)
        return self._body


def install_aiohttp_stub(include_json_helpers=False):
    aiohttp = types.ModuleType("aiohttp")
    web = types.ModuleType("aiohttp.web")

    class WebSocketResponse:
        pass

    web.WebSocketResponse = WebSocketResponse

    if include_json_helpers:
        def json_response(payload, status=200):
            return FakeJSONResponse(payload=payload, status=status)

        class Response(FakeJSONResponse):
            def __init__(self, *, status=200, text=""):
                super().__init__(payload=None, status=status, text=text)

        web.json_response = json_response
        web.Response = Response

    aiohttp.web = web
    sys.modules["aiohttp"] = aiohttp
    sys.modules["aiohttp.web"] = web
    return aiohttp, web
