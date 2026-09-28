"""Foto actual de los descensos respetando desempates obligatorios.

La diferencia de gol no decide una igualdad en una posición de descenso. Este
módulo separa equipos que bajarían sin desempate de los que deberían jugarlo y
resuelve además la regla de que el descenso por Tabla Anual excluye al equipo que
ya descendió por promedios.
"""
from __future__ import annotations

from fractions import Fraction
from itertools import combinations
from collections.abc import Mapping, Sequence

LPF_RUNTIME_API = 21


def _bottom_resolution(items, score, slots: int):
    slots = max(0, int(slots))
    groups = {}
    for item in items:
        groups.setdefault(score(item), []).append(item)
    confirmed = []
    playoff = []
    playoff_slots = 0
    remaining = slots
    for value in sorted(groups):
        if remaining <= 0:
            break
        group = groups[value]
        if len(group) <= remaining:
            confirmed.extend(group)
            remaining -= len(group)
        else:
            playoff = list(group)
            playoff_slots = remaining
            remaining = 0
    return confirmed, playoff, playoff_slots


def _avg_score(row):
    pts = int(row.get("Pts", row.get("points", row.get("pts", 0))) or 0)
    played = int(row.get("PJ", row.get("played", row.get("pj", 0))) or 0)
    return Fraction(pts, played) if played else Fraction(0, 1)


def current_relegation_picture(
    annual: Mapping[str, Mapping[str, object]],
    averages: Sequence[Mapping[str, object]] | None,
    *,
    annual_relegations: int = 1,
    average_relegations: int = 1,
) -> dict[str, object]:
    annual_items = [
        {"team": str(team), "pts": int((row or {}).get("pts", 0))}
        for team, row in (annual or {}).items()
    ]
    avg_items = [dict(row) for row in (averages or [])]
    for row in avg_items:
        row["team"] = str(row.get("Equipo", row.get("team", "")))

    avg_confirmed, avg_playoff, avg_playoff_slots = _bottom_resolution(
        avg_items, _avg_score, int(average_relegations)
    ) if avg_items and average_relegations else ([], [], 0)
    confirmed_names = [row["team"] for row in avg_confirmed]
    playoff_names = [row["team"] for row in avg_playoff]

    if avg_playoff:
        choices = [
            tuple(choice)
            for choice in combinations(playoff_names, avg_playoff_slots)
        ]
    else:
        choices = [tuple()]

    annual_scenarios = []
    for choice in choices:
        relegated_avg = set(confirmed_names) | set(choice)
        pool = [row for row in annual_items if row["team"] not in relegated_avg]
        confirmed, playoff, playoff_slots = _bottom_resolution(
            pool, lambda row: int(row["pts"]), int(annual_relegations)
        ) if annual_relegations else ([], [], 0)
        annual_scenarios.append({
            "average_relegated": sorted(relegated_avg),
            "annual_confirmed": [row["team"] for row in confirmed],
            "annual_playoff": [row["team"] for row in playoff],
            "annual_playoff_slots": playoff_slots,
        })

    annual_candidate_sets = [
        set(scenario["annual_confirmed"]) | set(scenario["annual_playoff"])
        for scenario in annual_scenarios
    ]
    annual_candidates = sorted(set().union(*annual_candidate_sets)) if annual_candidate_sets else []
    if annual_scenarios:
        common = set(annual_scenarios[0]["annual_confirmed"])
        for scenario in annual_scenarios[1:]:
            common &= set(scenario["annual_confirmed"])
        annual_confirmed_common = sorted(common)
    else:
        annual_confirmed_common = []

    return {
        "average_confirmed": confirmed_names,
        "average_playoff": playoff_names,
        "average_playoff_slots": avg_playoff_slots,
        "annual_scenarios": annual_scenarios,
        "annual_confirmed_common": annual_confirmed_common,
        "annual_candidates": annual_candidates,
        "annual_depends_on_average_playoff": len(annual_scenarios) > 1,
    }

# ---------------------------------------------------------------------------
# Tramo final: solver exacto conjunto Tabla Anual + promedios
# ---------------------------------------------------------------------------


def _reachable_additions(games_left: int) -> list[int]:
    """Totales de puntos que un club puede sumar en ``games_left`` partidos."""
    r = max(0, int(games_left))
    return sorted({3 * wins + draws for wins in range(r + 1) for draws in range(r - wins + 1)})


def _joint_solver_dependencies():
    """Carga perezosa de scipy/numpy para mantener liviano el módulo de foto actual."""
    try:
        import numpy as np
        from scipy.optimize import Bounds, LinearConstraint, milp
        from scipy.sparse import lil_matrix
        return np, Bounds, LinearConstraint, milp, lil_matrix
    except Exception:
        return None


def _normalize_pending_matches(matches, teams: set[str]):
    out = []
    seen = set()
    for raw in matches or []:
        if not isinstance(raw, (tuple, list)) or len(raw) < 2:
            continue
        home, away = str(raw[0]), str(raw[1])
        if home == away or (home, away) in seen:
            continue
        if home not in teams and away not in teams:
            continue
        seen.add((home, away))
        out.append((home, away))
    return out


def _joint_relegation_fixture_check(annual, remaining, matches, average_totals):
    teams = set(annual) & set(average_totals or {})
    if not teams or len(teams) != len(annual):
        return False, "faltan totales de promedios para uno o más equipos", []
    normalized = _normalize_pending_matches(matches, teams)
    coverage = {team: 0 for team in teams}
    for home, away in normalized:
        if home in coverage:
            coverage[home] += 1
        if away in coverage:
            coverage[away] += 1
    missing = [
        team for team in sorted(teams)
        if int(coverage.get(team, 0)) != max(0, int(remaining.get(team, 0)))
    ]
    if missing:
        sample = ", ".join(missing[:6]) + ("…" if len(missing) > 6 else "")
        return False, f"fixture pendiente incompleto para la prueba conjunta ({sample})", normalized
    return True, "", normalized


def _joint_relegation_feasible(
    annual,
    remaining,
    matches,
    average_totals,
    team: str,
    target_final_points: int,
    *,
    route: str,
):
    """Busca un cierre compatible donde ``team`` quede expuesto al descenso.

    Implementa exactamente la regla 2026 usada por la app cuando hay 1 descenso
    por promedios + 1 por Tabla Anual:

    * ``route='average'``: el equipo puede quedar último/empatado último en
      promedios. Un empate es inseguro porque obliga a desempate.
    * ``route='annual_after_average'``: algún *otro* equipo puede ocupar la plaza
      de promedios y, una vez excluido, ``team`` puede quedar último/empatado
      último en la Tabla Anual.

    La segunda ruta selecciona dentro del MILP al posible descendido por promedio;
    por eso no suma techos individuales incompatibles ni evalúa las dos tablas por
    separado.
    """
    deps = _joint_solver_dependencies()
    if deps is None:
        return {"available": False, "feasible": False, "reason": "scipy.optimize.milp no está disponible"}
    np, Bounds, LinearConstraint, milp, lil_matrix = deps

    if team not in annual or team not in (average_totals or {}):
        return {"available": False, "feasible": False, "reason": "equipo desconocido"}

    ok, reason, matches = _joint_relegation_fixture_check(annual, remaining, matches, average_totals)
    if not ok:
        return {"available": False, "feasible": False, "reason": reason}

    teams = list(annual)
    rivals = [name for name in teams if name != team]
    current_points = {
        name: int((annual[name].get("pts", 0) if isinstance(annual[name], Mapping) else annual[name]) or 0)
        for name in teams
    }
    avg_points = {name: int(average_totals[name][0]) for name in teams}
    avg_played = {name: int(average_totals[name][1]) for name in teams}
    final_games = {name: avg_played[name] + max(0, int(remaining.get(name, 0))) for name in teams}
    if any(final_games[name] <= 0 for name in teams):
        return {"available": False, "feasible": False, "reason": "denominadores de promedio inválidos"}

    need_team = int(target_final_points) - current_points[team]
    if need_team not in _reachable_additions(int(remaining.get(team, 0))):
        return {"available": True, "feasible": False, "reason": "puntaje final inalcanzable"}

    m = len(matches)
    z_candidates = rivals if route == "annual_after_average" else []
    z_start = 3 * m
    nvars = 3 * m + len(z_candidates)
    if nvars == 0:
        return {"available": True, "feasible": False, "reason": "sin variables pendientes"}

    gains = {name: np.zeros(nvars) for name in teams}
    home_pts = (3, 1, 0)
    away_pts = (0, 1, 3)
    for idx, (home, away) in enumerate(matches):
        for outcome in range(3):
            if home in gains:
                gains[home][3 * idx + outcome] += home_pts[outcome]
            if away in gains:
                gains[away][3 * idx + outcome] += away_pts[outcome]

    rows = []
    # Un resultado por partido.
    for idx in range(m):
        row = np.zeros(nvars)
        row[3 * idx:3 * idx + 3] = 1
        rows.append((row, 1.0, 1.0))

    # El equipo analizado termina exactamente con target_final_points.
    rows.append((gains[team].copy(), float(need_team), float(need_team)))

    if route == "average":
        # Para quedar último/empatado último, TODOS los rivales pueden terminar
        # con promedio >= al del equipo. Los cocientes se comparan sin float.
        for rival in rivals:
            row = final_games[team] * gains[rival] - final_games[rival] * gains[team]
            lower = final_games[rival] * avg_points[team] - final_games[team] * avg_points[rival]
            rows.append((row, float(lower), np.inf))

    elif route == "annual_after_average":
        # Elegimos exactamente un rival como posible descendido por promedio.
        if not z_candidates:
            return {"available": True, "feasible": False, "reason": "sin rival para excluir por promedio"}
        pick = np.zeros(nvars)
        pick[z_start:] = 1
        rows.append((pick, 1.0, 1.0))

        # Big-M sólo activa las restricciones del candidato elegido. Es muy
        # superior a cualquier diferencia posible de puntos*cantidad de PJ.
        big_ratio = 1_000_000.0
        big_points = 1_000.0
        for z_index, candidate in enumerate(z_candidates):
            zvar = z_start + z_index
            # candidate puede ser el peor promedio: todos los demás terminan
            # con cociente >= al suyo. El empate se considera inseguro.
            for other in teams:
                if other == candidate:
                    continue
                row = final_games[candidate] * gains[other] - final_games[other] * gains[candidate]
                lower = (
                    final_games[other] * avg_points[candidate]
                    - final_games[candidate] * avg_points[other]
                )
                row[zvar] -= big_ratio
                rows.append((row, float(lower - big_ratio), np.inf))

            # Excluido candidate por promedio, team puede quedar último o empatado
            # último de la anual: todos los restantes tienen >= target_final_points.
            for other in teams:
                if other in (team, candidate):
                    continue
                row = gains[other].copy()
                row[zvar] -= big_points
                lower = int(target_final_points) - current_points[other]
                rows.append((row, float(lower - big_points), np.inf))
    else:
        return {"available": False, "feasible": False, "reason": f"ruta desconocida: {route}"}

    # Armado vectorizado (antes fila por fila en lil_matrix: 10-30x más lento).
    A = np.vstack([row for row, _low, _high in rows]).astype(float) if rows else np.zeros((0, nvars))
    lb = np.empty(len(rows))
    ub = np.empty(len(rows))
    for idx, (row, low, high) in enumerate(rows):
        lb[idx] = low
        ub[idx] = high

    result = milp(
        c=np.zeros(nvars),
        integrality=np.ones(nvars),
        bounds=Bounds(np.zeros(nvars), np.ones(nvars)),
        constraints=LinearConstraint(A, lb, ub),
        options={"time_limit": 10.0, "mip_rel_gap": 0.0},
    )
    feasible = bool(result.success and result.x is not None)
    picked = None
    if feasible and z_candidates:
        zvals = result.x[z_start:]
        if len(zvals):
            picked = z_candidates[int(np.argmax(zvals))]
    return {
        "available": True,
        "feasible": feasible,
        "reason": str(getattr(result, "message", "") or ""),
        "average_relegated_candidate": picked,
    }


def joint_relegation_exact_ladder(
    annual: Mapping[str, Mapping[str, object]],
    remaining: Mapping[str, int],
    matches: Sequence[tuple[str, str]],
    average_totals: Mapping[str, tuple[int, int]] | None,
    team: str,
    *,
    annual_relegations: int = 1,
    average_relegations: int = 1,
    exact_window: int = 8,
) -> dict[str, object]:
    """Escalera exacta de permanencia combinando anual y promedios.

    Hoy el solver conjunto soporta la regla LPF 2026 de una plaza por cada vía.
    Devuelve el menor **total final alcanzable** que impide cualquier cierre donde
    el equipo pueda caer por alguna de las dos tablas. Un empate en una posición
    de descenso se toma como inseguro porque reglamentariamente exige desempate.
    """
    if team not in annual:
        return {"available": False, "reason": "equipo desconocido", "rows": []}
    if int(annual_relegations) != 1 or int(average_relegations) != 1:
        return {
            "available": False,
            "reason": "el solver conjunto exacto está implementado para 1 descenso anual + 1 por promedios",
            "rows": [],
        }
    games_left = max(0, int(remaining.get(team, 0)))
    if games_left > int(exact_window):
        return {
            "available": False,
            "reason": f"fuera de la ventana exacta ({games_left} partidos restantes)",
            "rows": [],
        }
    if not average_totals:
        return {"available": False, "reason": "faltan antecedentes/totales de promedios", "rows": []}

    ok, reason, normalized = _joint_relegation_fixture_check(annual, remaining, matches, average_totals)
    if not ok:
        return {"available": False, "reason": reason, "rows": []}

    current = int((annual[team].get("pts", 0) if isinstance(annual[team], Mapping) else annual[team]) or 0)
    reachable = [current + add for add in _reachable_additions(games_left)]
    rows = []
    guarantee = None
    for final_points in reachable:
        avg_risk = _joint_relegation_feasible(
            annual, remaining, normalized, average_totals, team, final_points, route="average"
        )
        if not avg_risk.get("available"):
            return {"available": False, "reason": avg_risk.get("reason", "solver no disponible"), "rows": rows}

        annual_risk = {"available": True, "feasible": False, "average_relegated_candidate": None}
        if not avg_risk.get("feasible"):
            annual_risk = _joint_relegation_feasible(
                annual, remaining, normalized, average_totals, team, final_points,
                route="annual_after_average",
            )
            if not annual_risk.get("available"):
                return {"available": False, "reason": annual_risk.get("reason", "solver no disponible"), "rows": rows}

        unsafe_route = None
        detail = ""
        if avg_risk.get("feasible"):
            unsafe_route = "promedios"
            detail = "Existe un cierre compatible en el que puede quedar último o empatado último en promedios."
        elif annual_risk.get("feasible"):
            unsafe_route = "anual"
            candidate = annual_risk.get("average_relegated_candidate")
            suffix = f" ({candidate} puede ocupar la plaza por promedios)" if candidate else ""
            detail = (
                "Existe un cierre compatible en el que puede quedar último o empatado último de la Tabla General "
                f"después de excluir al descendido por promedio{suffix}."
            )
        else:
            detail = (
                "No existe ningún cierre compatible que lo deje en zona de descenso por promedios ni por la Tabla General "
                "después de aplicar la regla de duplicación."
            )
            if guarantee is None:
                guarantee = int(final_points)

        rows.append({
            "final_points": int(final_points),
            "safe": unsafe_route is None,
            "unsafe_route": unsafe_route,
            "detail": detail,
        })

    return {
        "available": True,
        "method": "milp-joint-relegation",
        "guarantee": guarantee,
        "maximum": max(reachable) if reachable else current,
        "current": current,
        "rows": rows,
        "exact": True,
        "reason": "",
    }


# Memoización: la UI repite estos cálculos en cada clic con los mismos datos.
from lpf_memo import memoize as _memoize
_joint_relegation_feasible = _memoize(_joint_relegation_feasible)
joint_relegation_exact_ladder = _memoize(joint_relegation_exact_ladder)
