"""Fuentes, normalizacion y respaldo del fixture LPF 2026.

Este modulo no depende de Streamlit. Recibe callbacks para canonizar clubes y
permite combinar LPF oficial, ESPN, FutbolArgentino.com y una ultima foto JSON sin inferir
resultados a partir de los PJ de la tabla.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
import json
import re
import unicodedata
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

ARG_TZ = ZoneInfo("America/Argentina/Buenos_Aires")

# El Clausura 2026 de Primera comenzó a fines de julio. El archivo de la LPF
# conserva también notas del Apertura 2026 y varias parejas de clubes se repiten
# entre ambos torneos. Si se aceptan esas notas viejas sólo porque la pareja existe
# en LPF_FIXTURE, un marcador del Apertura puede contaminar el Clausura.
LPF_CLAUSURA_2026_RESULTS_START = date(2026, 7, 20)
# Permalinks cortos auditados que pertenecen al Clausura. No aceptar cualquier
# ``/?p=...`` descubierto en el archivo: WordPress también conserva entradas del
# Apertura sin fecha visible en la URL y una pareja repetida podría contaminar la
# foto actual. Los nuevos permalinks cortos se incorporan de forma explícita.
LPF_CLAUSURA_2026_SHORT_POST_IDS = {"85379", "85760", "85943", "86880"}

_SPANISH_MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

_STATUS_MAP = {
    "jugado": "played",
    "finalizado": "played",
    "final": "played",
    "por jugar": "scheduled",
    "programado": "scheduled",
    "proximo": "scheduled",
    "próximo": "scheduled",
    "en juego": "live",
    "en vivo": "live",
    "aplazado": "postponed",
    "postergado": "postponed",
    "suspendido": "postponed",
    "cancelado": "cancelled",
    "anulado": "cancelled",
}

# Variantes visibles en FutbolArgentino.com. Las claves se comparan contra los
# nombres canonicos que llegan desde el fixture de la aplicacion.
_SOURCE_ALIASES = {
    "Argentinos Juniors": ["Argentinos J.", "Argentinos"],
    "Atletico Tucuman": ["A. Tucuman", "Atlético Tucumán"],
    "Barracas Central": ["Barracas"],
    "Boca Juniors": ["Boca"],
    "Central Cordoba": ["Central Córdoba SE", "Central Córdoba", "C. Córdoba"],
    "Defensa y Justicia": ["Defensa"],
    "Deportivo Riestra": ["Riestra"],
    "Estudiantes de La Plata": ["Estudiantes"],
    "Estudiantes de Rio Cuarto": ["Estudiantes Río Cuarto", "Estudiantes (RC)", "Estudiantes RC"],
    "Gimnasia La Plata": ["Gimnasia"],
    "Gimnasia de Mendoza": ["Gimnasia Mendoza", "Gimnasia (M)", "Gimnasia M"],
    "Independiente Rivadavia": ["Ind. Rivadavia", "Ind Rivadavia"],
    "Newell's Old Boys": ["Newell's", "Newells"],
    "Rosario Central": ["Rosario"],
    "Talleres": ["Talleres de Córdoba", "Talleres (Córdoba)"],
    "Union": ["Unión de Santa Fe", "Unión"],
    "Velez Sarsfield": ["Vélez"],
}


def _ascii(value: object) -> str:
    text = str(value or "").strip().lower()
    text = "".join(
        ch for ch in unicodedata.normalize("NFD", text)
        if unicodedata.category(ch) != "Mn"
    )
    text = text.replace("’", "'")
    return re.sub(r"\s+", " ", text).strip()


def _compact(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", _ascii(value))


def official_fixture_index(fixture: Sequence[Mapping[str, object]]) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for row in fixture or []:
        home = str(row.get("l") or row.get("home") or "").strip()
        away = str(row.get("v") or row.get("away") or "").strip()
        if not home or not away:
            continue
        out[(home, away)] = {
            "round": int(row.get("f") or row.get("round") or 0),
            "kind": str(row.get("tipo") or row.get("kind") or "zone"),
            "zone": row.get("zona") or row.get("zone"),
        }
    return out


def _canonical_aliases(expected_teams: Iterable[str]) -> dict[str, list[str]]:
    aliases: dict[str, list[str]] = {}
    for team in expected_teams:
        key = _ascii(team)
        candidates = [team]
        candidates.extend(_SOURCE_ALIASES.get(team, []))
        candidates.extend(_SOURCE_ALIASES.get(key.title(), []))
        # Coincidencias por forma sin acentos para nombres canonicos del proyecto.
        for source_key, values in _SOURCE_ALIASES.items():
            if _ascii(source_key) == key:
                candidates.extend(values)
        aliases[team] = list(dict.fromkeys(str(item) for item in candidates if item))
    return aliases


def resolve_team_token(
    token: object,
    *,
    canon_club: Callable[[str], str],
    expected_teams: Iterable[str],
) -> str | None:
    """Resuelve una celda visible, incluso si trae nombre largo + abreviatura."""
    raw = re.sub(r"^\s*\d+[.)-]?\s*", "", str(token or ""))
    raw = re.sub(r"\s+", " ", raw).strip()
    if not raw:
        return None

    expected = set(expected_teams)
    direct = canon_club(raw)
    if direct in expected:
        return direct

    compact = _compact(raw)
    if not compact:
        return None
    scores: list[tuple[int, str]] = []
    for team, variants in _canonical_aliases(expected).items():
        best = 0
        for variant in variants:
            needle = _compact(variant)
            if len(needle) >= 4 and needle in compact:
                best = max(best, len(needle))
        if best:
            scores.append((best, team))
    scores.sort(reverse=True)
    if not scores:
        return None
    if len(scores) == 1 or scores[0][0] > scores[1][0]:
        return scores[0][1]
    return None


def parse_spanish_date(value: object, default_year: int = 2026) -> date | None:
    text = _ascii(value)
    text = re.sub(r"^[a-z]+,\s*", "", text)
    match = re.search(r"\b(\d{1,2})\s+([a-z]+)(?:\s+(\d{4}))?\b", text)
    if not match:
        return None
    month = _SPANISH_MONTHS.get(match.group(2))
    if not month:
        return None
    try:
        return date(int(match.group(3) or default_year), month, int(match.group(1)))
    except ValueError:
        return None


def parse_clock(value: object) -> time | None:
    text = str(value or "").strip().upper().replace(".", "")
    match = re.fullmatch(r"(\d{1,2}):(\d{2})\s*(AM|PM)?", text)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2))
    ampm = match.group(3)
    if ampm:
        if hour == 12:
            hour = 0
        if ampm == "PM":
            hour += 12
    try:
        return time(hour, minute)
    except ValueError:
        return None


def _status(value: object) -> str | None:
    text = _ascii(value)
    for label, status in _STATUS_MAP.items():
        if text == _ascii(label) or text.startswith(_ascii(label) + " "):
            return status
    # FutbolArgentino.com muestra el minuto (por ejemplo, 87' o 45+2')
    # en el lugar del estado mientras el partido está en juego. Conservar el
    # registro como live evita descartarlo, pero nunca lo convierte en resultado final.
    if re.fullmatch(r"\d{1,3}(?:\+\d{1,2})?['’]?", str(value or "").strip()):
        return "live"
    if text in {"entretiempo", "descanso", "medio tiempo"}:
        return "live"
    return None


def _score(value: object) -> tuple[int, int] | None:
    match = re.fullmatch(r"\s*(\d+)\s*[-–—]\s*(\d+)\s*", str(value or ""))
    return (int(match.group(1)), int(match.group(2))) if match else None


def _iso_datetime(day: date | None, clock: time | None) -> str:
    if not day:
        return ""
    dt = datetime.combine(day, clock or time(17, 0), tzinfo=ARG_TZ)
    return dt.isoformat()


def _record_priority(record: Mapping[str, object]) -> tuple[int, int, str]:
    status = str(record.get("status") or "")
    base = {
        "played": 100,
        "live": 90,
        "scheduled": 70,
        "postponed": 40,
        "cancelled": 10,
    }.get(status, 0)
    scheduled = str(record.get("scheduled_at") or "")
    return base, 1 if scheduled else 0, scheduled


def merge_match_records(*collections: Iterable[Mapping[str, object]]) -> list[dict]:
    """Une fuentes por pareja. Un resultado siempre prevalece sobre programación."""
    best: dict[tuple[str, str], dict] = {}
    for collection in collections:
        for raw in collection or []:
            home = str(raw.get("home") or "").strip()
            away = str(raw.get("away") or "").strip()
            if not home or not away or home == away:
                continue
            row = {
                "match_id": str(raw.get("match_id") or raw.get("event_id") or f"{home}|{away}"),
                "round": int(raw.get("round") or raw.get("round_number") or 0),
                "home": home,
                "away": away,
                "scheduled_at": str(raw.get("scheduled_at") or ""),
                "status": str(raw.get("status") or "scheduled"),
                "home_score": raw.get("home_score"),
                "away_score": raw.get("away_score"),
                "source": str(raw.get("source") or ""),
            }
            key = (home, away)
            if key not in best or _record_priority(row) > _record_priority(best[key]):
                best[key] = row
    return sorted(best.values(), key=lambda row: (int(row.get("round") or 999), str(row.get("scheduled_at") or ""), row["home"], row["away"]))


def _jsonld_records(
    html: str,
    *,
    canon_club: Callable[[str], str],
    fixture_index: Mapping[tuple[str, str], Mapping[str, object]],
) -> list[dict]:
    try:
        from bs4 import BeautifulSoup
    except Exception:
        return []
    soup = BeautifulSoup(html, "lxml")
    expected = {team for pair in fixture_index for team in pair}
    records: list[dict] = []

    def walk(node):
        if isinstance(node, list):
            for item in node:
                yield from walk(item)
        elif isinstance(node, dict):
            node_type = node.get("@type")
            types = node_type if isinstance(node_type, list) else [node_type]
            if any(str(item).lower() in {"sportsevent", "event"} for item in types if item):
                yield node
            for value in node.values():
                if isinstance(value, (dict, list)):
                    yield from walk(value)

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            payload = json.loads(script.string or script.get_text() or "")
        except Exception:
            continue
        for event in walk(payload):
            home_raw = event.get("homeTeam") or event.get("competitor") or {}
            away_raw = event.get("awayTeam") or {}
            if isinstance(home_raw, list) and len(home_raw) >= 2 and not away_raw:
                home_raw, away_raw = home_raw[0], home_raw[1]
            home_name = home_raw.get("name") if isinstance(home_raw, dict) else str(home_raw or "")
            away_name = away_raw.get("name") if isinstance(away_raw, dict) else str(away_raw or "")
            home = resolve_team_token(home_name, canon_club=canon_club, expected_teams=expected)
            away = resolve_team_token(away_name, canon_club=canon_club, expected_teams=expected)
            if not home or not away or (home, away) not in fixture_index:
                continue
            event_status = _ascii(event.get("eventStatus") or event.get("status") or "")
            status = "played" if "complete" in event_status or "finished" in event_status else "scheduled"
            score = event.get("score") or {}
            home_score = away_score = None
            if isinstance(score, dict):
                try:
                    home_score = int(score.get("homeScore"))
                    away_score = int(score.get("awayScore"))
                    status = "played"
                except (TypeError, ValueError):
                    pass
            meta = fixture_index[(home, away)]
            records.append({
                "match_id": str(event.get("@id") or event.get("url") or f"FA|{home}|{away}"),
                "round": int(meta.get("round") or 0),
                "home": home,
                "away": away,
                "scheduled_at": str(event.get("startDate") or ""),
                "status": status,
                "home_score": home_score,
                "away_score": away_score,
                "source": "FutbolArgentino.com",
            })
    return records


def parse_futbolargentino_results_html(
    html: str,
    *,
    canon_club: Callable[[str], str],
    official_fixture: Sequence[Mapping[str, object]],
) -> list[dict]:
    """Lee resultados y programación visible de FutbolArgentino.com.

    Usa JSON-LD cuando está disponible y luego una estrategia por texto visible.
    La salida se filtra estrictamente contra el fixture oficial de la aplicación,
    lo que descarta partidos, noticias y duplicados ajenos al Clausura.
    """
    try:
        from bs4 import BeautifulSoup
    except Exception as exc:
        raise RuntimeError(f"BeautifulSoup no está disponible: {exc}") from exc

    fixture_index = official_fixture_index(official_fixture)
    expected = {team for pair in fixture_index for team in pair}
    jsonld = _jsonld_records(html, canon_club=canon_club, fixture_index=fixture_index)

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()
    tokens = [re.sub(r"\s+", " ", text).strip() for text in soup.stripped_strings]
    tokens = [token for token in tokens if token]

    round_number = 0
    current_day: date | None = None
    text_records: list[dict] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        round_match = re.search(r"\bFecha\s+(\d+)\b", token, flags=re.I)
        if round_match:
            round_number = int(round_match.group(1))
            i += 1
            continue
        parsed_day = parse_spanish_date(token)
        if parsed_day:
            current_day = parsed_day
            i += 1
            continue

        home = resolve_team_token(token, canon_club=canon_club, expected_teams=expected)
        if not home:
            i += 1
            continue

        # Estructura visible habitual: local, hora, marcador, visitante, estado.
        # La hora es opcional: el proveedor puede omitirla o moverla en el DOM
        # cuando actualiza un partido en vivo/final. Para reconocer un resultado
        # alcanzan marcador + rival + estado, siempre filtrados contra el fixture.
        time_idx = score_idx = away_idx = status_idx = None
        clock = None
        result = None
        away = None
        status = None
        upper = min(len(tokens), i + 14)
        for j in range(i + 1, upper):
            if re.search(r"\bFecha\s+\d+\b", tokens[j], flags=re.I) or parse_spanish_date(tokens[j]):
                break
            if time_idx is None:
                parsed_clock = parse_clock(tokens[j])
                if parsed_clock:
                    time_idx, clock = j, parsed_clock
                    continue
            if score_idx is None:
                parsed_score = _score(tokens[j])
                if parsed_score:
                    score_idx, result = j, parsed_score
                    continue
            if score_idx is not None and away_idx is None:
                candidate = resolve_team_token(tokens[j], canon_club=canon_club, expected_teams=expected)
                if candidate and candidate != home:
                    away_idx, away = j, candidate
                    continue
            if away_idx is not None:
                parsed_status = _status(tokens[j])
                if parsed_status:
                    status_idx, status = j, parsed_status
                    break

        if not all(value is not None for value in (score_idx, away_idx, status_idx)):
            i += 1
            continue
        key = (home, away)
        if key not in fixture_index:
            i += 1
            continue
        meta = fixture_index[key]
        effective_round = int(meta.get("round") or round_number or 0)
        home_score = away_score = None
        if status in {"played", "live"} and result is not None:
            home_score, away_score = result
        text_records.append({
            "match_id": f"FA-F{effective_round:02d}-{_compact(home)}-{_compact(away)}",
            "round": effective_round,
            "home": home,
            "away": away,
            "scheduled_at": _iso_datetime(current_day, clock),
            "status": status,
            "home_score": home_score,
            "away_score": away_score,
            "source": "FutbolArgentino.com",
        })
        i = status_idx + 1

    merged = merge_match_records(jsonld, text_records)
    if not merged:
        raise RuntimeError("no pude identificar partidos del Clausura en el HTML")
    return merged



_LPF_RESULT_TITLE_RE = re.compile(
    r"\b(?:venci[oó]|gan[oó]|gole[oó]|derrot[oó]|super[oó]|empataron|igualaron|"
    r"cerr[oó]|complet[oó]|triunf[oó]|victoria|cay[oó])\b",
    flags=re.I,
)

# Desde agosto/septiembre de 2026 la LPF empezó a reutilizar artículos de agenda
# como notas vivas de resultados y a titular cierres con formas como
# ``Todo sobre la sexta``, ``Se fue la séptima``, ``Adiós a la fecha 8`` y
# ``Culminó la novena``. Filtrar sólo por verbos de resultado dejó afuera fechas
# completas aun cuando el artículo oficial sí contenía todos los marcadores.
#
# La seguridad no depende de este filtro: el parser del artículo sólo acepta dos
# clubes + marcador explícito + una pareja que exista en LPF_FIXTURE. Por eso acá
# conviene ser inclusivos con notas que tengan semántica de fecha/jornada.
_LPF_ROUND_ARTICLE_TITLE_RE = re.compile(
    r"\b(?:fecha|jornada|agenda|programacion|programación|"
    r"primera|segunda|tercera|cuarta|quinta|sexta|septima|séptima|octava|novena|"
    r"decima|décima|undecima|undécima|duodecima|duodécima|"
    r"adios|adiós|culmino|culminó|termino|terminó|finalizo|finalizó)\b",
    flags=re.I,
)


def _lpf_official_listing_candidate(title: str) -> bool:
    normalized = _ascii(title)
    return bool(
        _LPF_RESULT_TITLE_RE.search(normalized)
        or _LPF_ROUND_ARTICLE_TITLE_RE.search(normalized)
    )


def _lpf_official_url_is_current_clausura(url: str, *, allow_short_post: bool = False) -> bool:
    """Filtra permalinks de Primera al período del Clausura 2026.

    La categoría ``/notas/primera/`` también contiene el Apertura 2026. Además,
    WordPress publicó algunos cierres del Clausura como ``/?p=NNNNN``. Los links
    fechados se aceptan sólo desde el inicio del Clausura; los permalinks cortos se
    admiten únicamente cuando provienen del propio archivo de Primera y luego pasan
    por la guarda de competencia del artículo.
    """
    parsed = urlparse(str(url or ""))
    match = re.search(r"/notas/primera/(\d{4})/(\d{2})/(\d{2})/", parsed.path)
    if match:
        try:
            published = date(*(int(part) for part in match.groups()))
        except ValueError:
            return False
        return published >= LPF_CLAUSURA_2026_RESULTS_START
    if allow_short_post and parsed.path in {"", "/"}:
        match = re.search(r"(?:^|&)p=(\d+)(?:&|$)", parsed.query)
        return bool(match and match.group(1) in LPF_CLAUSURA_2026_SHORT_POST_IDS)
    return False


_LPF_ORDINAL_ROUNDS = {
    "cuarta": 4,
    "quinta": 5,
    "sexta": 6,
    "septima": 7,
    "octava": 8,
    "novena": 9,
    "decima": 10,
    "undecima": 11,
    "duodecima": 12,
}


def _lpf_round_from_text(value: object) -> int | None:
    """Devuelve una fecha oficial cuando el texto la identifica sin ambigüedad."""
    text = _ascii(value)
    match = re.search(r"\bfecha\s+(?:n[°ºo]?\s*)?(\d{1,2})\b", text)
    if match:
        return int(match.group(1))
    matches = {
        number
        for word, number in _LPF_ORDINAL_ROUNDS.items()
        if re.search(rf"\b(?:la|fecha)\s+{word}\b", text)
    }
    return next(iter(matches)) if len(matches) == 1 else None


def _lpf_round_from_url(value: object) -> int | None:
    parsed = urlparse(str(value or ""))
    match = re.search(r"(?:^|[-_/])fecha-(\d{1,2})(?:[-_/]|$)", parsed.path.lower())
    return int(match.group(1)) if match else None


def parse_lpf_official_listing_html(html: str, *, base_url: str) -> list[dict]:
    """Extrae notas actuales de Primera que pueden contener resultados.

    La portada oficial mezcla programación, conferencias y otras noticias. La LPF
    también actualiza notas de agenda y usa títulos editoriales sin verbos clásicos
    de resultado. Por eso se conservan títulos con semántica de fecha/jornada además
    de cierres explícitos.

    Dos guardas evitan mezclar torneos: los permalinks fechados deben pertenecer a
    la ventana del Clausura 2026 y los permalinks cortos ``/?p=...`` sólo se aceptan
    cuando fueron descubiertos desde el archivo de Primera. El artículo vuelve a
    validarse por competencia y contra el fixture oficial antes de aportar un marcador.
    """
    try:
        from bs4 import BeautifulSoup
    except Exception as exc:
        raise RuntimeError(f"BeautifulSoup no está disponible: {exc}") from exc

    soup = BeautifulSoup(html or "", "lxml")
    out: list[dict] = []
    seen: set[str] = set()
    base_host = urlparse(base_url).netloc.lower()
    for anchor in soup.find_all("a", href=True):
        title = re.sub(r"\s+", " ", anchor.get_text(" ", strip=True)).strip()
        href = urljoin(base_url, str(anchor.get("href") or "").strip())
        parsed = urlparse(href)
        if not href or href in seen:
            continue
        if parsed.netloc.lower() != base_host:
            continue
        base_path = urlparse(base_url).path or ""
        allow_short = "/notas/primera" in base_path or "/categoria/primera" in base_path
        if not _lpf_official_url_is_current_clausura(href, allow_short_post=allow_short):
            continue
        if title.lower() in {"leer más", "leer mas"}:
            # El mismo href suele aparecer una segunda vez con este texto; conservar
            # la primera ancla con el título real.
            continue
        if not _lpf_official_listing_candidate(title):
            continue
        seen.add(href)
        out.append({
            "title": title,
            "url": href,
            "round": _lpf_round_from_text(title) or _lpf_round_from_url(href),
        })
    return out


def _official_score_line(
    text: str,
    *,
    canon_club: Callable[[str], str],
    expected: set[str],
    fixture_index: Mapping[tuple[str, str], Mapping[str, object]],
) -> tuple[str, str, int, int] | None:
    """Reconoce las dos formas de marcador que usa la web oficial.

    En sus notas aparecen tanto ``River 2 – Vélez 2`` como
    ``Racing 0 – Banfield 1``. También puede haber zona, estadio o TV después del
    marcador. La pareja resuelta siempre debe existir en el fixture de la app.
    """
    line = re.sub(r"\s+", " ", str(text or "")).strip()
    if not line or not any(sep in line for sep in ("–", "—", "-")):
        return None

    patterns = (
        # local 1 – 3 visitante
        re.compile(r"^(?P<home>.+?)\s+(?P<hg>\d+)\s*[–—-]\s*(?P<ag>\d+)\s+(?P<away>.+)$"),
        # local 1 – visitante 3
        re.compile(r"^(?P<home>.+?)\s+(?P<hg>\d+)\s*[–—-]\s*(?P<away>.+?)\s+(?P<ag>\d+)(?:\s*[,;(].*)?$"),
    )
    for pattern in patterns:
        match = pattern.match(line)
        if not match:
            continue
        home = resolve_team_token(match.group("home"), canon_club=canon_club, expected_teams=expected)
        away = resolve_team_token(match.group("away"), canon_club=canon_club, expected_teams=expected)
        if not home or not away or home == away:
            continue
        key = (home, away)
        if key not in fixture_index:
            continue
        return home, away, int(match.group("hg")), int(match.group("ag"))
    return None


def parse_lpf_official_results_article_html(
    html: str,
    *,
    canon_club: Callable[[str], str],
    official_fixture: Sequence[Mapping[str, object]],
    source_url: str = "",
    expected_round: int | None = None,
) -> list[dict]:
    """Extrae marcadores explícitos de notas de Primera de la web oficial LPF.

    No interpreta titulares ni prosa: sólo líneas que contienen dos clubes, un
    marcador explícito y una pareja existente en el fixture oficial. Esto permite
    usar la web de la propia LPF como fuente de resultados sin convertir una noticia
    en una inferencia.
    """
    try:
        from bs4 import BeautifulSoup
    except Exception as exc:
        raise RuntimeError(f"BeautifulSoup no está disponible: {exc}") from exc

    fixture_index = official_fixture_index(official_fixture)
    expected = {team for pair in fixture_index for team in pair}
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    # Primera conserva en el mismo archivo notas del Apertura y del Clausura 2026.
    # Una pareja válida del fixture no alcanza para distinguir torneos porque muchos
    # clubes se enfrentan en ambos. Rechazar explícitamente una nota identificada como
    # Apertura evita sumar un partido viejo o incluso adelantar un PJ futuro.
    content_root = soup.find("article") or soup.find("main") or soup
    article_text = _ascii(content_root.get_text(" ", strip=True))
    if "torneo apertura" in article_text and "torneo clausura" not in article_text:
        return []
    if source_url:
        parsed_source = urlparse(str(source_url))
        has_dated_primera = bool(re.search(
            r"/notas/primera/\d{4}/\d{2}/\d{2}/", parsed_source.path
        ))
        has_short_post = parsed_source.path in {"", "/"} and bool(
            re.search(r"(?:^|&)p=\d+(?:&|$)", parsed_source.query)
        )
        if (has_dated_primera or has_short_post) and not _lpf_official_url_is_current_clausura(
            source_url, allow_short_post=True
        ):
            return []

    # 3.8.68: nunca recorrer el HTML completo. WordPress inserta módulos de
    # relacionados/sidebar con marcadores de otras notas y torneos; si se parsean
    # junto con el cuerpo principal pueden sumar partidos futuros o históricos.
    #
    # A la vez, las notas reales de Primera suelen publicar una fecha entera dentro
    # de un único <p> separado por <br>. ``tag.get_text(" ")`` convierte entonces
    # quince resultados en una sola cadena imposible de reconocer. Recorremos los
    # ``stripped_strings`` *sólo dentro de article/main* para recuperar esos fragmentos
    # atómicos, manteniendo el round activo. Como respaldo para markup con <strong> o
    # <span> que parta una línea, aceptamos también bloques chicos que contengan como
    # máximo un separador de marcador.
    #
    # Una nota de Fecha N sólo puede aportar partidos cuyo round del fixture sea N.
    # La pareja por sí sola no alcanza porque un mismo cruce puede aparecer en otra
    # instancia/temporada.
    candidates: list[tuple[str, int | None]] = []
    current_round = int(expected_round) if expected_round is not None else None
    seen_candidates: set[tuple[str, int | None]] = set()

    def _append_candidate(value: object, scoped_round: int | None) -> None:
        clean = re.sub(r"\s+", " ", str(value or "")).strip()
        if not clean:
            return
        key = (clean, scoped_round)
        if key in seen_candidates:
            return
        seen_candidates.add(key)
        candidates.append(key)

    # Camino principal: respeta los <br> de WordPress porque cada tramo visible se
    # conserva como string independiente.
    for raw in content_root.stripped_strings:
        value = re.sub(r"\s+", " ", str(raw or "")).strip()
        if not value:
            continue
        marker = _lpf_round_from_text(value)
        if marker is not None and expected_round is None:
            current_round = marker
        _append_candidate(value, current_round)

    # Respaldo para una línea cuyo texto esté dividido por tags inline. Evitar
    # contenedores gigantes con varios partidos: ésos ya quedaron atomizados arriba.
    for tag in content_root.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "th"]):
        value = re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()
        if not value or len(value) > 320:
            continue
        separators = value.count("–") + value.count("—") + value.count(" - ")
        if separators > 1:
            continue
        marker = _lpf_round_from_text(value)
        scoped_round = (
            int(expected_round) if expected_round is not None
            else marker if marker is not None
            else current_round
        )
        _append_candidate(value, scoped_round)

    records: list[dict] = []
    seen_lines: set[tuple[str, int | None]] = set()
    for text, scoped_round in candidates:
        key_line = (text, scoped_round)
        if key_line in seen_lines:
            continue
        seen_lines.add(key_line)
        parsed = _official_score_line(
            text, canon_club=canon_club, expected=expected, fixture_index=fixture_index
        )
        if not parsed:
            continue
        home, away, home_score, away_score = parsed
        meta = fixture_index[(home, away)]
        match_round = int(meta.get("round") or 0)
        required_round = int(expected_round) if expected_round is not None else scoped_round
        # Sin contexto de fecha no publicamos un marcador automático de una nota
        # genérica: es preferible perder una fila a contaminar el torneo.
        if required_round is None or match_round != int(required_round):
            continue
        records.append({
            "match_id": f"LPF-F{match_round:02d}-{_compact(home)}-{_compact(away)}",
            "round": match_round,
            "home": home,
            "away": away,
            "scheduled_at": "",
            "status": "played",
            "home_score": home_score,
            "away_score": away_score,
            "source": "Liga Profesional de Fútbol",
            "source_url": str(source_url or ""),
        })
    return merge_match_records(records)


def parse_tyc_clausura_results_html(
    html: str,
    *,
    canon_club: Callable[[str], str],
    official_fixture: Sequence[Mapping[str, object]],
    source_url: str = "",
) -> list[dict]:
    """Extrae resultados del fixture vivo de TyC Sports para el Clausura 2026.

    La página mantiene en un único documento el fixture futuro y, bajo el encabezado
    ``Resultados del Torneo Clausura 2026``, los marcadores fecha por fecha. El parser
    ignora todo lo anterior al encabezado de resultados y exige simultáneamente:

    * una ``Fecha N`` activa;
    * dos clubes resolubles contra los nombres canónicos;
    * marcador explícito;
    * que la pareja pertenezca a ``LPF_FIXTURE`` y al mismo round ``N``.

    De este modo TyC funciona como respaldo de marcadores, no como fuente de reglas,
    posiciones ni clasificación.
    """
    try:
        from bs4 import BeautifulSoup
    except Exception as exc:
        raise RuntimeError(f"BeautifulSoup no está disponible: {exc}") from exc

    fixture_index = official_fixture_index(official_fixture)
    expected = {team for pair in fixture_index for team in pair}
    soup = BeautifulSoup(html or "", "lxml")
    for tag in soup(["script", "style", "noscript", "svg"]):
        tag.decompose()

    content_root = soup.find("article") or soup.find("main") or soup
    in_results = False
    current_round: int | None = None
    records: list[dict] = []

    for tag in content_root.find_all(["h1", "h2", "h3", "h4", "h5", "p", "li", "td"]):
        text = re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()
        if not text:
            continue
        folded = _ascii(text)
        if "resultados del torneo clausura 2026" in folded:
            in_results = True
            current_round = None
            continue
        if not in_results:
            continue
        if folded in {"te puede interesar", "tambien te puede interesar"}:
            break

        marker = _lpf_round_from_text(text)
        if marker is not None:
            current_round = int(marker)
            continue
        if current_round is None or not (1 <= current_round <= 16):
            continue

        parsed = _official_score_line(
            text,
            canon_club=canon_club,
            expected=expected,
            fixture_index=fixture_index,
        )
        if not parsed:
            continue
        home, away, home_score, away_score = parsed
        meta = fixture_index[(home, away)]
        if int(meta.get("round") or 0) != current_round:
            continue
        records.append({
            "match_id": f"tyc|{current_round}|{home}|{away}",
            "round": current_round,
            "home": home,
            "away": away,
            "scheduled_at": "",
            "status": "played",
            "home_score": home_score,
            "away_score": away_score,
            "source": "TyC Sports",
            "source_url": str(source_url or ""),
        })

    return merge_match_records(records)


def records_from_legacy(
    played: Iterable[tuple[str, str, int, int]],
    pending: Iterable[tuple[str, str]],
    *,
    schedule: Mapping[str, str] | None,
    event_meta: Mapping[str, Mapping[str, object]] | None,
    official_fixture: Sequence[Mapping[str, object]],
    source: str,
) -> list[dict]:
    fixture_index = official_fixture_index(official_fixture)
    schedule = schedule or {}
    event_meta = event_meta or {}
    records: list[dict] = []
    played_pairs = set()
    for home, away, gh, ga in played or []:
        key = (home, away)
        if key not in fixture_index:
            continue
        played_pairs.add(key)
        map_key = f"{home}|||{away}"
        meta = event_meta.get(map_key) or {}
        records.append({
            "match_id": str(meta.get("event_id") or f"{source}|{home}|{away}"),
            "round": int(meta.get("round") or fixture_index[key].get("round") or 0),
            "home": home,
            "away": away,
            "scheduled_at": str(meta.get("scheduled_at") or schedule.get(map_key) or ""),
            "status": "played",
            "home_score": int(gh),
            "away_score": int(ga),
            "source": source,
        })
    for home, away in pending or []:
        key = (home, away)
        if key not in fixture_index or key in played_pairs:
            continue
        map_key = f"{home}|||{away}"
        meta = event_meta.get(map_key) or {}
        records.append({
            "match_id": str(meta.get("event_id") or f"{source}|{home}|{away}"),
            "round": int(meta.get("round") or fixture_index[key].get("round") or 0),
            "home": home,
            "away": away,
            "scheduled_at": str(meta.get("scheduled_at") or schedule.get(map_key) or ""),
            "status": "scheduled",
            "home_score": None,
            "away_score": None,
            "source": source,
        })
    return merge_match_records(records)


def expected_played_count(zones: Mapping[str, Mapping[str, Mapping[str, object]]] | None) -> int | None:
    if not zones:
        return None
    total = 0
    for base in zones.values():
        for row in base.values():
            try:
                total += int((row or {}).get("pj", 0))
            except (TypeError, ValueError):
                return None
    return total // 2 if total % 2 == 0 else None


def validate_fixture_records(
    records: Sequence[Mapping[str, object]],
    *,
    official_fixture: Sequence[Mapping[str, object]],
    expected_played: int | None = None,
) -> list[dict]:
    fixture_index = official_fixture_index(official_fixture)
    clean = merge_match_records(records)
    errors: list[str] = []
    for row in clean:
        key = (str(row.get("home") or ""), str(row.get("away") or ""))
        if key not in fixture_index:
            errors.append(f"{key[0]}–{key[1]} no pertenece al fixture")
        if row.get("status") == "played":
            if row.get("home_score") is None or row.get("away_score") is None:
                errors.append(f"falta el marcador de {key[0]}–{key[1]}")
    played = [row for row in clean if row.get("status") == "played"]
    if expected_played is not None and len(played) < expected_played:
        errors.append(f"faltan {expected_played - len(played)} resultado(s) para explicar los PJ de las zonas")
    if errors:
        raise RuntimeError("; ".join(errors[:8]))
    return clean


def played_pending_from_records(records: Sequence[Mapping[str, object]]) -> tuple[list[tuple[str, str, int, int]], list[tuple[str, str]]]:
    played: list[tuple[str, str, int, int]] = []
    pending: list[tuple[str, str]] = []
    for row in records or []:
        home, away = str(row.get("home") or ""), str(row.get("away") or "")
        if row.get("status") == "played":
            played.append((home, away, int(row.get("home_score") or 0), int(row.get("away_score") or 0)))
        elif row.get("status") in {"scheduled", "live", "postponed"}:
            pending.append((home, away))
    return played, pending


def snapshot_payload(records: Sequence[Mapping[str, object]], *, source: str, updated_at: str | None = None) -> dict:
    return {
        "schema": 1,
        "competition": "LPF Clausura 2026",
        "source": str(source or "fuente automática"),
        "updated_at": updated_at or datetime.now(timezone.utc).isoformat(),
        "matches": merge_match_records(records),
    }


def write_snapshot(path: str | Path, payload: Mapping[str, object]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)


def read_snapshot(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def snapshot_age_hours(payload: Mapping[str, object]) -> float:
    raw = str(payload.get("updated_at") or "").replace("Z", "+00:00")
    updated = datetime.fromisoformat(raw)
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - updated).total_seconds() / 3600.0)
