"""Parsing and auth tests. They load api.py directly, so Home Assistant isn't needed."""

import asyncio
import hashlib
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from aiohttp import web

ROOT = Path(__file__).parent.parent
spec = importlib.util.spec_from_file_location(
    "ns_api", ROOT / "custom_components" / "nightscout_extended" / "api.py"
)
api = importlib.util.module_from_spec(spec)
sys.modules["ns_api"] = api  # dataclasses need the module registered
spec.loader.exec_module(api)

PROPS = json.loads((Path(__file__).parent / "fixtures" / "properties.json").read_text())


def test_parse_full_response():
    d = api.parse_properties(PROPS)
    assert d.glucose == 155
    assert d.glucose_time == datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc)
    assert d.delta == 6
    assert d.direction == "FortyFiveUp"
    assert d.iob == 1.35 and d.iob_source == "Care Portal"
    assert round(d.cob, 1) == 28.9
    assert d.basal == 0.29
    assert len(d.forecast) == 6
    assert d.forecast_at(15).mgdl == 169
    assert d.forecast_at(30).mgdl == 175


def test_forecast_too_far_returns_none():
    d = api.parse_properties(PROPS)
    assert d.forecast_at(60) is None


def test_missing_plugins_are_none():
    d = api.parse_properties({"bgnow": PROPS["bgnow"]})
    assert d.glucose == 155
    assert d.iob is None and d.cob is None and d.basal is None
    assert d.forecast == [] and d.forecast_at(15) is None


def test_garbage_values_dont_crash():
    d = api.parse_properties({"iob": {"iob": "n/a"}, "cob": {"cob": True}, "ar2": {"forecast": {"predicted": [{"mills": None}]}}})
    assert d.iob is None and d.cob is None and d.forecast == []


SECRET = "averylongapisecret"


async def _serve(handler_check):
    app = web.Application()

    async def guarded(request):
        if not handler_check(request):
            return web.json_response({"status": 401}, status=401)
        if request.path == "/api/v1/status.json":
            return web.json_response({"version": "15.0.9", "settings": {"units": "mg/dl"}})
        return web.json_response(PROPS)

    app.router.add_get("/api/v1/status.json", guarded)
    app.router.add_get("/api/v2/properties/{names}", guarded)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}"


def _run(check, credential):
    async def go():
        runner, url = await _serve(check)
        try:
            async with aiohttp.ClientSession() as s:
                c = api.NightscoutClient(s, url, credential)
                status = await c.async_get_status()
                data = await c.async_get_data()
                return c, status, data
        finally:
            await runner.cleanup()
    return asyncio.run(go())


def test_token_auth():
    c, status, data = _run(lambda r: r.query.get("token") == "readable-abc123", "readable-abc123")
    assert not c.uses_api_secret and status["version"] == "15.0.9" and data.glucose == 155


def test_api_secret_fallback():
    digest = hashlib.sha1(SECRET.encode()).hexdigest()
    c, _, data = _run(lambda r: r.headers.get("api-secret") == digest, SECRET)
    assert c.uses_api_secret and data.iob == 1.35


def test_wrong_credential_raises_auth_error():
    try:
        _run(lambda r: False, "wrong")
    except api.NightscoutAuthError:
        return
    raise AssertionError("expected NightscoutAuthError")


def test_redirect_to_other_host_is_refused_and_not_followed():
    seen = []

    async def go():
        evil = web.Application()

        async def capture(request):
            seen.append((request.query.get("token"), request.headers.get("api-secret")))
            return web.json_response({})

        evil.router.add_get("/{p:.*}", capture)
        evil_runner = web.AppRunner(evil); await evil_runner.setup()
        evil_site = web.TCPSite(evil_runner, "127.0.0.1", 0); await evil_site.start()
        evil_port = evil_site._server.sockets[0].getsockname()[1]

        app = web.Application()

        async def redirect(request):
            raise web.HTTPFound(f"http://127.0.0.1:{evil_port}{request.path_qs}")

        app.router.add_get("/{p:.*}", redirect)
        runner = web.AppRunner(app); await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0); await site.start()
        port = site._server.sockets[0].getsockname()[1]
        try:
            async with aiohttp.ClientSession() as s:
                c = api.NightscoutClient(s, f"http://127.0.0.1:{port}", "readable-abc123")
                try:
                    await c.async_get_status()
                except api.NightscoutConnectionError as err:
                    return str(err)
        finally:
            await runner.cleanup(); await evil_runner.cleanup()

    msg = asyncio.run(go())
    assert msg and "redirected" in msg
    assert "readable-abc123" not in msg  # token in the Location URL is redacted
    assert seen == []  # the other host never received anything


def test_errors_never_contain_the_credential():
    async def go():
        async with aiohttp.ClientSession() as s:
            c = api.NightscoutClient(s, "http://127.0.0.1:1", "readable-abc123")
            try:
                await c.async_get_status()
            except api.NightscoutConnectionError as err:
                return str(err)

    msg = asyncio.run(go())
    assert msg and "readable-abc123" not in msg
