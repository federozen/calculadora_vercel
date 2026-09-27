from http.server import BaseHTTPRequestHandler
import json
import os
import sys
import tempfile
import zipfile
import re
import difflib
from functools import lru_cache

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CORE_ZIP = os.path.join(ROOT, "core.zip")
CORE_DIR = os.path.join(tempfile.gettempdir(), "lpf_core_v1")

RESULTS_TEXT = r'''Belgrano 2-1 Rosario Central
Sarmiento 2-3 Argentinos
Defensa y Justicia 1-1 Aldosivi
Gimnasia (Mza.) 1-0 Central Córdoba
Racing 2-1 Gimnasia
Vélez 1-0 Instituto
Huracán 1-0 Banfield
Platense 2-2 Unión
Estudiantes (Río Cuarto) 1-0 Tigre
Newell's 1-0 Talleres
River 0-1 Barracas Central
Lanús 1-0 San Lorenzo
Atlético Tucumán 0-0 Ind. Rivadavia Mza.
Estudiantes 0-2 Independiente
Deportivo Riestra 3-0 Boca
Banfield 3-2 Sarmiento
San Lorenzo 1-0 Gimnasia (Mza.)
Rosario Central 0-0 Racing
Argentinos 3-0 Estudiantes (Río Cuarto)
Barracas Central 1-0 Aldosivi
Defensa y Justicia 2-1 Deportivo Riestra
Gimnasia 1-0 River
Instituto 2-1 Platense
Independiente Rivadavia Mza. 2-1 Huracán
Talleres 1-3 Vélez
Independiente 1-0 Newell's
Central Córdoba 0-2 Atlético Tucumán
Gimnasia (Mza.) 2-0 Unión
Estudiantes (Río Cuarto) 0-0 Banfield
Belgrano 0-1 Argentinos
Estudiantes 3-0 Defensa y Justicia
Racing 1-3 Tigre
Deportivo Riestra 0-1 Barracas Central
Aldosivi 1-2 Gimnasia
Newell's 2-2 Boca
River 0-1 Rosario Central
Lanús 0-1 Instituto
Sarmiento 2-1 Independiente Rivadavia Mza.
Platense 0-4 Talleres
Vélez 1-0 Independiente
Huracán 0-0 Atlético Tucumán
Central Córdoba 1-0 San Lorenzo
Boca 1-0 Estudiantes
Tigre 0-0 Belgrano
Unión 2-1 Lanús
Rosario Central 2-1 Aldosivi
Independiente Rivadavia Mza. 2-1 Estudiantes (Río Cuarto)
Deportivo Riestra 2-0 Estudiantes
Atlético Tucumán 1-2 Sarmiento'''


def _ensure_core():
    marker = os.path.join(CORE_DIR, "lpf_services.py")
    if not os.path.exists(marker):
        os.makedirs(CORE_DIR, exist_ok=True)
        with zipfile.ZipFile(CORE_ZIP) as zf:
            zf.extractall(CORE_DIR)
    if CORE_DIR not in sys.path:
        sys.path.insert(0, CORE_DIR)


@lru_cache(maxsize=1)
def _context():
    _ensure_core()
    from lpf_data_2026 import LPF_FIXTURE, ZONA_A_LPF_2026, ZONA_B_LPF_2026, TABLA_ANUAL_LPF_2026
    from lpf_parsers import parse_tabla_anual
    from lpf_clubs import canon_club
    from lpf_loading import prepare_offline_load
    from lpf_data_quality import derive_opening_from_results
    from lpf_state import build_lpf_state
    from lpf_services import prepare_competition_snapshot, calculate

    rx = re.compile(r"^(.*?)\s+(\d+)\s*[-–:]\s*(\d+)\s+(.*)$")
    played = []
    for raw in RESULTS_TEXT.splitlines():
        m = rx.match(raw.strip())
        if m:
            played.append((canon_club(m.group(1)), canon_club(m.group(4)), int(m.group(2)), int(m.group(3))))

    zones = {
        "A": parse_tabla_anual(ZONA_A_LPF_2026)[0],
        "B": parse_tabla_anual(ZONA_B_LPF_2026)[0],
    }
    prepared = prepare_offline_load(zones, builtin_played=played)
    annual_ref = parse_tabla_anual(TABLA_ANUAL_LPF_2026)[0]
    opening, _ = derive_opening_from_results(annual_ref, LPF_FIXTURE, played[:15], opening_rounds=16)
    state, report = build_lpf_state(
        prepared["zones"],
        played=prepared["played"],
        annual_direct=annual_ref,
        builtin_opening=opening,
        fixture=LPF_FIXTURE,
        camps=("Belgrano", "", ""),
        intl=("", ""),
        n_anual=1,
        n_prom=1,
    )
    snap_payload = {
        "zones": state["zonas_lpf"],
        "played": state["jugados"],
        "annual": state["anual_directo"],
        "opening": state["apertura"],
        "previous_averages": state["promedios"],
        "fixture": list(LPF_FIXTURE),
        "qualification": {
            "champions": {"apertura": "Belgrano", "clausura": "", "copa_argentina": ""},
            "international_champions": {"libertadores": "", "sudamericana": ""},
            "copa_argentina_replacement": "",
        },
        "rules": {
            "annual_relegations": 1,
            "average_relegations": 1,
            "opening_rounds": 16,
            "playoff_cutoff": 8,
            "sudamericana_slots": 6,
        },
        "provenance": {"source": "Base incluida del repositorio"},
    }
    snapshot = prepare_competition_snapshot(snap_payload)["result"]
    return {
        "canon": canon_club,
        "calculate": calculate,
        "state": state,
        "snapshot": snapshot,
        "report": report,
        "reconcile_note": prepared.get("standings_reconcile_note") or prepared.get("duplicate_repair_note") or "",
    }


def _find_team(raw, teams, canon):
    value = str(raw or "").strip()
    if not value:
        return None, []
    canonical = canon(value)
    if canonical in teams:
        return canonical, []
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()
    by_norm = {norm(t): t for t in teams}
    q = norm(value)
    if q in by_norm:
        return by_norm[q], []
    contains = [t for t in teams if q and (q in norm(t) or norm(t) in q)]
    if len(contains) == 1:
        return contains[0], []
    suggestions = difflib.get_close_matches(q, list(by_norm), n=5, cutoff=0.45)
    return None, [by_norm[s] for s in suggestions]


def _safe_call(calc, op, payload):
    try:
        return calc(op, payload).get("result") or None, None
    except Exception as exc:
        return None, str(exc)


def _club_payload(team):
    ctx = _context()
    calc, state, snap = ctx["calculate"], ctx["state"], ctx["snapshot"]
    zone = next((label for label, base in state["zonas_lpf"].items() if team in base), None)
    common = {"snapshot": snap, "team": team}

    zone_table, zone_err = _safe_call(calc, "standings", {"snapshot": snap, "scope": "zone", "zone": zone})
    annual_table, annual_err = _safe_call(calc, "standings", {"snapshot": snap, "scope": "annual"})
    preview, preview_err = _safe_call(calc, "preview", {**common, "objective": "playoffs", "scope": "next_team_match"})
    playoff_points, pp_err = _safe_call(calc, "objective_points", {**common, "objective": "playoffs", "zone": zone})
    playoff_chances, pc_err = _safe_call(calc, "objective_chances", {**common, "objective": "playoffs", "zone": zone, "simulations": 2000})
    lib_points, lib_err = _safe_call(calc, "objective_points", {**common, "objective": "libertadores"})
    sud_points, sud_err = _safe_call(calc, "objective_points", {**common, "objective": "sudamericana"})

    def row_for(table):
        for row in ((table or {}).get("table") or []):
            if row.get("team") == team:
                return row
        return None

    quality = []
    for issue in getattr(ctx["report"], "issues", []) or []:
        if getattr(issue, "level", "") in {"blocked", "warning"}:
            quality.append({
                "level": getattr(issue, "level", ""),
                "domain": getattr(issue, "domain", ""),
                "code": getattr(issue, "code", ""),
                "message": str(getattr(issue, "message", "")),
            })

    errors = [x for x in [zone_err, annual_err, preview_err, pp_err, pc_err, lib_err, sud_err] if x]
    return {
        "team": team,
        "zone": zone,
        "zoneRow": row_for(zone_table),
        "annualRow": row_for(annual_table),
        "zoneTable": (zone_table or {}).get("table", []),
        "annualTable": (annual_table or {}).get("table", []),
        "preview": preview,
        "playoffs": {"points": playoff_points, "chances": playoff_chances},
        "libertadores": lib_points,
        "sudamericana": sud_points,
        "quality": quality,
        "reconcileNote": ctx.get("reconcile_note", ""),
        "calculationErrors": errors,
        "meta": {
            "version": "vercel-club-v1",
            "source": "motor calculadora_dos + base incluida",
            "note": "La interfaz es nueva; la matemática se ejecuta con el motor LPF del repositorio.",
        },
    }


class handler(BaseHTTPRequestHandler):
    def _send(self, status, obj):
        raw = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        try:
            from urllib.parse import urlparse, parse_qs
            ctx = _context()
            state = ctx["state"]
            teams = sorted(set(state["equipos"]))
            qs = parse_qs(urlparse(self.path).query)
            raw = (qs.get("team") or [""])[0]
            if not raw:
                self._send(200, {"teams": teams, "default": "Boca Juniors" if "Boca Juniors" in teams else teams[0]})
                return
            team, suggestions = _find_team(raw, teams, ctx["canon"])
            if not team:
                self._send(404, {"error": "No encontré ese club.", "suggestions": suggestions, "teams": teams})
                return
            self._send(200, _club_payload(team))
        except Exception as exc:
            self._send(500, {"error": str(exc)})
