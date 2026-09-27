# Calculadora LPF · Vercel · Por club

Versión simplificada de `calculadora_dos` para Vercel.

## Qué cambia

- No hay chat.
- No hay selector de tipo de consulta.
- Se elige **un club una sola vez**.
- La pantalla calcula automáticamente posición de zona, Tabla Anual, playoffs, copas y próxima fecha.
- Conserva el motor Python LPF del repositorio original dentro de `core.zip`.
- Muestra los bloqueos/avisos del control de calidad en lugar de ocultarlos.

## Estructura mínima para GitHub web

```text
api/
  club.py
app.js
core.zip
index.html
requirements.txt
styles.css
vercel.json
README.md
```

Sólo existe **una subcarpeta: `api`**. Todo lo demás se sube en la raíz.

## Subir a GitHub desde la web

1. Crear un repositorio vacío.
2. Subir todos los archivos de la raíz juntos.
3. Crear/entrar a `api` y subir `club.py`.
4. Conectar el repo a Vercel.
5. Framework Preset: **Other** (Vercel detectará la función Python y los archivos estáticos).
6. Deploy.

No necesita claves de IA ni secrets para iniciar.

## Datos

Esta V1 usa las mismas tablas, fixture y resultados incluidos en el repositorio recibido. El motor avanza la tabla embebida con los resultados confirmados incluidos y conserva el quality gate. La carga automática desde fuentes web del Streamlit original todavía no está conectada a esta interfaz Vercel.

## Siguiente paso sugerido

Agregar un único endpoint de actualización de datos para que la interfaz Vercel reconstruya la foto desde las fuentes públicas ya contempladas por el motor, sin volver a agregar chat ni menús de consulta.
