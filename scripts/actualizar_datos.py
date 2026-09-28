"""Actualiza los datos de la calculadora con Python nativo (sin navegador).

Lo corre la tarea programada de GitHub (.github/workflows/actualizar-datos.yml).
Ejecuta la misma lógica que el botón «Actualizar a hoy» de la app —LPF oficial,
ESPN, TyC y FutbolArgentino con sus controles de consistencia— y, si todo cierra,
reescribe:

* public/app/core/data/lpf_resultados.txt   (todos los resultados confirmados)
* public/app/core/data/lpf_last_valid.json  (última foto válida de tablas)
* public/app/core/data/lpf_actualizacion.json (fecha, fuente y resumen)

Vercel vuelve a publicar solo cuando esos archivos cambian, y la app abre al día
sin tener que consultar las fuentes desde el navegador.

Uso local:  python scripts/actualizar_datos.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "public" / "app" / "app.py"


def main() -> int:
    from streamlit.testing.v1 import AppTest

    os.environ["LPF_EXPORT_DATA"] = "1"
    at = AppTest.from_file(str(APP), default_timeout=900)
    at.run()
    if at.exception:
        for exc in at.exception:
            print("ERROR en la app:", exc.message, file=sys.stderr)
        return 1
    result = at.session_state["_EXPORT_RESULT"] if "_EXPORT_RESULT" in at.session_state else None
    if not result:
        print("La app no devolvió resultado de exportación.", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result.get("ok"):
        print("No se actualizaron los archivos: " + str(result.get("error")), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
