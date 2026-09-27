// Proxy HTTP para la calculadora (Vercel Serverless Function, Node 18+).
//
// La app corre en el navegador (stlite/Pyodide) y no puede consultar LPF, ESPN,
// TyC, etc. directamente: el navegador bloquea esas peticiones (CORS) y prohíbe
// cabeceras como User-Agent o Referer. Python le manda acá un JSON con el pedido
// ({url, method, headers, body, timeout, redirect}); este proxy lo ejecuta desde
// el servidor y devuelve {status, statusText, url, headers, body}.
//
// Sólo se aceptan los dominios de ALLOWED_HOSTS (más los de PROXY_EXTRA_HOSTS,
// separados por coma, si se configuran en Vercel → Settings → Environment Variables).

const ALLOWED_HOSTS = [
  "lpf.org.ar",
  "espn.com",
  "espn.com.ar",
  "espncdn.com",
  "futbolargentino.com",
  "tycsports.com",
  "copaargentina.org",
  "wikipedia.org",
  "football-data.org",
  "apify.com",
  "anthropic.com",
];

const DROP_REQUEST_HEADERS = new Set([
  "host", "connection", "content-length", "accept-encoding", "transfer-encoding", "keep-alive",
]);
const MAX_BODY_BYTES = 12 * 1024 * 1024;

function allowedHost(hostname) {
  const extra = String(process.env.PROXY_EXTRA_HOSTS || "")
    .split(",").map((h) => h.trim().toLowerCase()).filter(Boolean);
  const host = String(hostname || "").toLowerCase();
  return [...ALLOWED_HOSTS, ...extra].some((allowed) => host === allowed || host.endsWith("." + allowed));
}

function charsetOf(contentType, bytes) {
  const fromHeader = /charset=([^;]+)/i.exec(contentType || "");
  if (fromHeader) return fromHeader[1].trim().replace(/["']/g, "").toLowerCase();
  const head = new TextDecoder("latin1").decode(bytes.subarray(0, 4096));
  const fromMeta = /<meta[^>]+charset=["']?([\w-]+)/i.exec(head);
  return fromMeta ? fromMeta[1].toLowerCase() : "utf-8";
}

function decode(bytes, contentType) {
  try {
    return new TextDecoder(charsetOf(contentType, bytes)).decode(bytes);
  } catch {
    return new TextDecoder("utf-8").decode(bytes);
  }
}

function cors(res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
  res.setHeader("Cache-Control", "no-store");
}

async function readJson(req) {
  if (req.body && typeof req.body === "object") return req.body;
  if (typeof req.body === "string") return JSON.parse(req.body || "{}");
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return JSON.parse(Buffer.concat(chunks).toString("utf-8") || "{}");
}

export default async function handler(req, res) {
  cors(res);
  if (req.method === "OPTIONS") return res.status(204).end();
  if (req.method !== "POST") return res.status(405).json({ error: "Usá POST con un JSON {url, method, headers, body}." });

  let spec;
  try {
    spec = await readJson(req);
  } catch {
    return res.status(400).json({ error: "JSON inválido." });
  }

  let target;
  try {
    target = new URL(String(spec.url || ""));
  } catch {
    return res.status(400).json({ error: "URL inválida." });
  }
  if (!["http:", "https:"].includes(target.protocol)) {
    return res.status(400).json({ error: "Sólo se permiten URLs http/https." });
  }
  if (!allowedHost(target.hostname)) {
    return res.status(403).json({
      error: `El proxy no tiene habilitado ${target.hostname}. Agregalo en PROXY_EXTRA_HOSTS si lo necesitás.`,
    });
  }

  const method = String(spec.method || "GET").toUpperCase();
  const headers = {};
  for (const [key, value] of Object.entries(spec.headers || {})) {
    if (!DROP_REQUEST_HEADERS.has(key.toLowerCase()) && value != null) headers[key] = String(value);
  }
  const seconds = Math.min(Math.max(Number(spec.timeout) || 30, 1), 55);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), seconds * 1000);

  try {
    const upstream = await fetch(target, {
      method,
      headers,
      body: ["GET", "HEAD"].includes(method) || spec.body == null ? undefined : String(spec.body),
      redirect: spec.redirect === false ? "manual" : "follow",
      signal: controller.signal,
    });
    const buffer = new Uint8Array(await upstream.arrayBuffer());
    if (buffer.byteLength > MAX_BODY_BYTES) {
      return res.status(200).json({ error: `La respuesta de ${target.hostname} es demasiado grande.` });
    }
    const outHeaders = {};
    upstream.headers.forEach((value, key) => {
      if (!["set-cookie", "content-encoding", "content-length"].includes(key)) outHeaders[key] = value;
    });
    return res.status(200).json({
      status: upstream.status,
      statusText: upstream.statusText,
      url: upstream.url || target.toString(),
      headers: outHeaders,
      body: method === "HEAD" ? "" : decode(buffer, upstream.headers.get("content-type")),
    });
  } catch (err) {
    const timedOut = err && err.name === "AbortError";
    return res.status(200).json({
      error: timedOut
        ? `Tiempo agotado (${seconds}s) consultando ${target.hostname}.`
        : `Error de red consultando ${target.hostname}: ${err && err.message ? err.message : err}`,
      timeout: timedOut,
    });
  } finally {
    clearTimeout(timer);
  }
}
