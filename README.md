# Calculadora LPF 2026 · versión Vercel (3.8.74)

La calculadora completa (el mismo `app.py` y el mismo motor `core/`) adaptada para
publicarse en **Vercel**, con la navegación reorganizada.

## Cómo funciona en Vercel

Vercel no puede correr un servidor de Streamlit (necesita un proceso permanente con
websockets). Por eso la app corre **dentro del navegador** con
[stlite](https://github.com/whitphx/stlite) (Streamlit + Python compilado a WebAssembly):

```text
Navegador ── index.html ─► stlite (Python + Streamlit en WebAssembly)
                               │  monta public/app/app.py y public/app/core/*
                               │
                               └─ requests.get(...) ─► /api/proxy (función serverless de Vercel)
                                                           └─► LPF · ESPN · TyC · FutbolArgentino · Wikipedia…
```

- `public/index.html` carga stlite y monta los archivos de `public/app/`.
- `api/proxy.js` reenvía las consultas a las fuentes externas (el navegador no puede
  hacerlo directamente por CORS). Sólo acepta los dominios de la lista `ALLOWED_HOSTS`.
- `public/app/core/web_http.py` redirige `requests` al proxy **sólo** cuando corre en el
  navegador. En local o en Streamlit Cloud no cambia nada.
- `scripts/build-manifest.mjs` genera `public/app/files.json` en cada deploy, así que
  agregar o borrar un `.py` no requiere tocar `index.html`.

La primera visita tarda un poco (descarga Python y pandas/numpy/scipy al navegador,
unos 20–40 s según la conexión). Después queda en caché y los cálculos corren en la
computadora del usuario, sin costo de servidor.

## Publicar en Vercel

**Desde GitHub (recomendado)**

1. Subí el contenido de esta carpeta a un repositorio nuevo.
2. En Vercel: *Add New → Project → Import* ese repositorio.
3. Dejá todo como viene: *Framework Preset* **Other**. `vercel.json` ya define el
   build (`node scripts/build-manifest.mjs`), la carpeta de salida (`public`) y la
   función `api/proxy.js`.
4. *Deploy*.

**Desde la terminal**

```bash
npm i -g vercel
vercel          # preview
vercel --prod   # producción
```

No hacen falta variables de entorno. Opcional: `PROXY_EXTRA_HOSTS` (dominios extra
separados por coma) si querés leer tablas de otros sitios desde «Traer la tabla desde
una URL».

## Datos siempre al día (tarea programada de GitHub)

El navegador es lento para recorrer decenas de páginas de LPF/ESPN/TyC. Por eso la
actualización pesada la hace GitHub, con Python normal, cada 2 horas:

1. `.github/workflows/actualizar-datos.yml` corre `scripts/actualizar_datos.py`.
2. El script ejecuta la misma lógica del botón «Actualizar a hoy» (mismas fuentes y
   mismos controles: sólo acepta resultados que reconstruyen exactamente PJ, puntos y goles).
3. Si todo cierra, guarda en `public/app/core/data/`:
   `lpf_resultados.txt`, `lpf_last_valid.json` y `lpf_actualizacion.json`, y hace commit.
4. Vercel publica solo el commit y la app abre ya al día, sin consultar nada al entrar.

Si una corrida falla (fuente caída), no toca nada y la app sigue con los datos anteriores.
Si igual abre con datos atrasados respecto del calendario, intenta actualizar desde el
navegador como antes.

**Primera vez:** en GitHub → pestaña **Actions** → *Actualizar datos LPF* → **Run workflow**.
Si Actions pide habilitarse, aceptá. Si el paso «Guardar cambios» falla por permisos:
Settings → Actions → General → *Workflow permissions* → **Read and write** → Save.

## Correrla en local como antes

```bash
pip install -r public/app/requirements.txt
streamlit run public/app/app.py
```

## Navegación nueva

Antes había un panel lateral largo (configuración + carga + LLM), seis botones de
«Accesos principales», seis atajos de Escenarios y pestañas o selectores de equipo
repetidos en cada pantalla. Ahora:

- **Equipo y objetivo se eligen una sola vez** en el panel lateral y todas las páginas
  los respetan (Panel, Puntos, Escenarios, Visualizaciones, Últimas fechas, Informe, chat).
- **Actualizar a hoy** queda siempre a mano en el panel lateral, con el estado de los datos.
- Al abrir, la app carga sola los datos que dejó la tarea programada: ya no hay que
  tocar «Cargar TODO» ni esperar a que consulte las fuentes.
- Menú superior con 4 secciones:

| Sección | Páginas | Qué incluye (todas las funciones originales) |
|---|---|---|
| **Equipo** | Panel del equipo | Resumen, situación, qué necesita, próximo partido, qué le conviene, escalera exacta, comparar, su zona, panorama |
| | Puntos por objetivo | Un equipo/todos sus objetivos · todos los equipos/un objetivo, escalera |
| | Escenarios | Gana/empata/pierde, qué pasa si…, puntos y puesto final, mejor y peor caso, distribución, clasificados y eliminados |
| **Competencia** | Previa de la fecha | Narrativa por fecha/partido, impacto en zonas, cruces de octavos |
| | Últimas fechas | Grilla G/E/P, matrices, doble entrada, árbol, partidos bisagra, reloj, ¿por qué? |
| | Visualizaciones | Equipo, zona, copas y descenso, próximo partido, la otra cancha |
| **Redacción** | Informe por equipo | Exacto + estimado, previa/post, texto listo para la nota |
| | Consultas y chat | Explorador de consultas, chat por palabras clave o con Claude |
| **Datos** | Cargar y actualizar | Carga rápida de resultados, fuentes automáticas, carga/edición manual, Copa Argentina |
| | Auditoría y reglas | Semáforo de calidad, control por equipo, respaldo JSON, reglamento 2026 |
| | Ajustes | Desempate, estructura, zonas con nombre, promedios, asistente LLM |

Las secciones con varias vistas usan botones segmentados en lugar de pestañas: sólo se
calcula la vista abierta, lo que además acelera la app en el navegador.

## Notas

- Si activás el asistente con Claude, la API key se envía a `api.anthropic.com` a través
  del proxy de tu propio deploy (no se guarda en ningún lado).
- Algunas fuentes (por ejemplo ESPN) pueden bloquear pedidos automáticos desde IPs de
  servidores. La app ya prueba varias fuentes y un respaldo; si todas fallan, avisa y
  deja cargar los resultados a mano en **Datos → Cargar y actualizar**.
- El estado vive en la pestaña del navegador: al recargar la página se vuelve a traer
  todo (y se pierden los resultados cargados a mano en esa sesión).
