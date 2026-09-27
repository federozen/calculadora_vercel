"""Transporte HTTP para correr la calculadora en el navegador (Vercel + stlite).

En Vercel la app corre dentro del navegador con Pyodide (WebAssembly). Desde ahí
no se puede consultar directamente LPF, ESPN, TyC, etc.: el navegador bloquea las
peticiones a otros dominios (CORS) y no deja fijar cabeceras como ``User-Agent``.

Este módulo reemplaza ``requests.get/post/request`` por una versión que envía
cada pedido a la función serverless ``/api/proxy`` del mismo deploy. El proxy lo
reenvía a la fuente real y devuelve estado, URL final, cabeceras y cuerpo. Para el
resto del código no cambia nada: recibe un ``requests.Response`` normal.

Fuera del navegador (``streamlit run`` local, Streamlit Cloud) ``install()`` no
hace nada y ``requests`` funciona como siempre.
"""
from __future__ import annotations

import json
import os
import sys

IS_BROWSER = sys.platform == "emscripten"
_INSTALLED = False


def proxy_url() -> str:
    return str(os.environ.get("LPF_PROXY_URL", "") or "/api/proxy")


def _send(method, url, *, params=None, data=None, json_body=None, headers=None, timeout=None, allow_redirects=True):
    import requests
    from requests.structures import CaseInsensitiveDict
    from js import XMLHttpRequest  # type: ignore[import-not-found]

    prepared = requests.Request(
        method=str(method or "GET").upper(), url=url, params=params, data=data,
        json=json_body, headers=dict(headers or {}),
    ).prepare()
    body = prepared.body
    if isinstance(body, bytes):
        body = body.decode("utf-8", errors="replace")

    if isinstance(timeout, (tuple, list)):
        timeout = sum(float(t or 0) for t in timeout) or None
    seconds = float(timeout) if timeout else 30.0

    payload = json.dumps({
        "url": prepared.url,
        "method": prepared.method,
        "headers": dict(prepared.headers or {}),
        "body": body,
        "timeout": seconds,
        "redirect": bool(allow_redirects),
    })

    xhr = XMLHttpRequest.new()
    try:
        xhr.open("POST", proxy_url(), False)
        try:
            xhr.timeout = int((seconds + 5) * 1000)
        except Exception:
            pass
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(payload)
    except Exception as exc:  # errores de red del navegador
        text = str(exc)
        if "timeout" in text.lower():
            raise requests.Timeout(f"Tiempo agotado consultando {url}") from exc
        raise requests.ConnectionError(f"No pude conectar con el proxy para {url}: {text}") from exc

    if int(xhr.status or 0) == 0:
        raise requests.ConnectionError(f"Sin respuesta del proxy para {url}")
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
    _INSTALLED = True
    return True
