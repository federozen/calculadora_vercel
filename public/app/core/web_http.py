"""Transporte HTTP para correr la calculadora en el navegador (Vercel + stlite).

En Vercel la app corre dentro del navegador con Pyodide (WebAssembly). Desde ahí
no se puede consultar directamente LPF, ESPN, TyC, etc.: el navegador bloquea las
peticiones a otros dominios (CORS) y no deja fijar cabeceras como ``User-Agent``.

Este módulo reemplaza ``requests.get/post/request`` por una versión que envía
cada pedido a la función serverless ``/api/proxy`` del mismo deploy. El proxy lo
reenvía a la fuente real y devuelve estado, URL final, cabeceras y cuerpo. Para el
resto del código no cambia nada: recibe un ``requests.Response`` normal.

Dos protecciones para que la app nunca quede «colgada»:

* Los GET viajan como ``GET /api/proxy?url=…`` y el CDN de Vercel guarda la
  respuesta unos minutos: la segunda persona (o la segunda actualización) recibe
  las páginas al instante en lugar de volver a recorrer LPF/ESPN/TyC.
* ``time_budget(segundos)`` fija un tiempo máximo total para una actualización.
  Cuando se agota, los pedidos siguientes fallan enseguida y el código de carga
  usa las fuentes que ya respondieron o la última foto válida.

Fuera del navegador (``streamlit run`` local, Streamlit Cloud) ``install()`` no
hace nada y ``requests`` funciona como siempre.
"""
from __future__ import annotations

import base64
import contextlib
import json
import os
import re
import sys
import time
from urllib.parse import quote

IS_BROWSER = sys.platform == "emscripten"
MAX_REQUEST_SECONDS = 12.0
_INSTALLED = False
_DEADLINE: float | None = None

# Parámetros que sólo sirven para esquivar cachés intermedias. Se quitan para que
# el CDN del proxy pueda reutilizar la respuesta durante unos minutos.
_CACHE_BUSTERS = re.compile(r"([?&])(_lpf_refresh|_ts|_t|nocache|cachebust)=[^&#]*&?", re.I)


def proxy_url() -> str:
    return str(os.environ.get("LPF_PROXY_URL", "") or "/api/proxy")


def _strip_cache_busters(url: str) -> str:
    cleaned = _CACHE_BUSTERS.sub(lambda m: m.group(1), url)
    return cleaned.rstrip("?&")


@contextlib.contextmanager
def time_budget(seconds: float):
    """Limita el tiempo total de red de un bloque (anidable: gana el más corto)."""
    global _DEADLINE
    previous = _DEADLINE
    candidate = time.monotonic() + float(seconds)
    _DEADLINE = candidate if previous is None else min(previous, candidate)
    try:
        yield
    finally:
        _DEADLINE = previous


def _remaining() -> float | None:
    if _DEADLINE is None:
        return None
    return _DEADLINE - time.monotonic()


def _xhr(method: str, url: str, body: str | None, seconds: float):
    from js import XMLHttpRequest  # type: ignore[import-not-found]

    xhr = XMLHttpRequest.new()
    xhr.open(method, url, False)
    try:
        xhr.timeout = int(seconds * 1000)
    except Exception:
        pass
    if body is not None:
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(body)
    else:
        xhr.send()
    return xhr


def _send(method, url, *, params=None, data=None, json_body=None, headers=None, timeout=None, allow_redirects=True):
    import requests
    from requests.structures import CaseInsensitiveDict

    prepared = requests.Request(
        method=str(method or "GET").upper(), url=url, params=params, data=data,
        json=json_body, headers=dict(headers or {}),
    ).prepare()
    body = prepared.body
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")

    if isinstance(timeout, (tuple, list)):
        timeout = sum(float(t or 0) for t in timeout) or None
    seconds = min(float(timeout) if timeout else MAX_REQUEST_SECONDS, MAX_REQUEST_SECONDS)
    remaining = _remaining()
    if remaining is not None:
        if remaining <= 1:
            raise requests.Timeout(f"Se agotó el tiempo de actualización antes de consultar {url}")
        seconds = min(seconds, remaining)

    target = str(prepared.url)
    header_map = {k: v for k, v in dict(prepared.headers or {}).items() if k.lower() != "content-length"}
    cacheable = prepared.method == "GET" and not body
    try:
        if cacheable:
            target = _strip_cache_busters(target)
            packed = base64.urlsafe_b64encode(json.dumps(header_map, sort_keys=True).encode("utf-8")).decode("ascii")
            query = f"url={quote(target, safe='')}&h={packed}&t={int(seconds)}"
            if not allow_redirects:
                query += "&r=0"
            xhr = _xhr("GET", f"{proxy_url()}?{query}", None, seconds + 5)
        else:
            payload = json.dumps({
                "url": target, "method": prepared.method, "headers": header_map, "body": body,
                "timeout": seconds, "redirect": bool(allow_redirects),
            })
            xhr = _xhr("POST", proxy_url(), payload, seconds + 5)
    except Exception as exc:  # errores de red del navegador
        text = str(exc)
        if "timeout" in text.lower():
            raise requests.Timeout(f"Tiempo agotado consultando {url}") from exc
        raise requests.ConnectionError(f"No pude conectar con el proxy para {url}: {text}") from exc

    if int(xhr.status or 0) == 0:
        raise requests.Timeout(f"Sin respuesta del proxy para {url} (tiempo agotado o sin conexión)")
    try:
        answer = json.loads(str(xhr.responseText or ""))
    except ValueError as exc:
        raise requests.ConnectionError(
            f"El proxy respondió HTTP {xhr.status} sin JSON válido para {url}"
        ) from exc
    if answer.get("error"):
        message = str(answer.get("error"))
        if answer.get("timeout"):
            raise requests.Timeout(message)
        raise requests.ConnectionError(message)

    response = requests.Response()
    response.status_code = int(answer.get("status") or 0)
    response.reason = str(answer.get("statusText") or "")
    response.url = str(answer.get("url") or prepared.url)
    response.headers = CaseInsensitiveDict(answer.get("headers") or {})
    response._content = str(answer.get("body") or "").encode("utf-8")
    response.encoding = "utf-8"
    response.request = prepared
    return response


def install() -> bool:
    """Activa el transporte por proxy sólo cuando corre en el navegador."""
    global _INSTALLED
    if not IS_BROWSER or _INSTALLED:
        return _INSTALLED
    import requests
    import requests.api
    import requests.sessions

    def request(method, url, **kwargs):
        return _send(
            method, url,
            params=kwargs.get("params"), data=kwargs.get("data"), json_body=kwargs.get("json"),
            headers=kwargs.get("headers"), timeout=kwargs.get("timeout"),
            allow_redirects=kwargs.get("allow_redirects", True),
        )

    def get(url, params=None, **kwargs):
        return request("GET", url, params=params, **kwargs)

    def post(url, data=None, json=None, **kwargs):
        return request("POST", url, data=data, json=json, **kwargs)

    def head(url, **kwargs):
        return request("HEAD", url, **kwargs)

    def session_request(self, method, url, **kwargs):
        merged = dict(self.headers or {})
        merged.update(kwargs.pop("headers", None) or {})
        return request(method, url, headers=merged, **kwargs)

    for module in (requests, requests.api):
        module.request = request
        module.get = get
        module.post = post
        module.head = head
    requests.sessions.Session.request = session_request

    # Los reintentos esperan con time.sleep; en el navegador eso congela la
    # pantalla. Se acortan las esperas (siguen respetando el presupuesto).
    try:
        import lpf_http

        _real_sleep = time.sleep

        class _ShortSleep:
            @staticmethod
            def sleep(seconds):
                _real_sleep(min(float(seconds or 0), 0.3))

            def __getattr__(self, name):
                return getattr(time, name)

        lpf_http.time = _ShortSleep()
    except Exception:
        pass

    _INSTALLED = True
    return True
