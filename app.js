const $ = (id) => document.getElementById(id);
const select = $("team");

function esc(s="") { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function pos(n){ return n ? `${n}º` : '—'; }
function pct(v){ return Number.isFinite(Number(v)) ? `${Number(v).toFixed(1).replace('.0','')}%` : '—'; }
function value(obj, keys){ for(const k of keys){ if(obj && obj[k] !== undefined && obj[k] !== null) return obj[k]; } return null; }

async function loadTeams(){
  const r = await fetch('/api/club');
  const data = await r.json();
  select.innerHTML = data.teams.map(t => `<option ${t===data.default?'selected':''}>${esc(t)}</option>`).join('');
  loadClub();
}

function objectiveHTML(obj){
  if(!obj) return '<p>No disponible con la foto actual.</p>';
  const exact = value(obj,['guaranteed_points','guarantee','exact_guarantee','garantia_exacta']);
  const ref = value(obj,['conservative_reference','reference','referencia_conservadora','floor','piso']);
  const current = value(obj,['current_points','points','puntos_actuales']);
  const max = value(obj,['maximum_points','ceiling','max_points','techo']);
  const rows = [];
  if(current!=null) rows.push(`<div><b>Hoy:</b> ${esc(current)} pts</div>`);
  if(exact!=null) rows.push(`<div><b>Garantía exacta:</b> ${esc(exact)} pts</div>`);
  else if(ref!=null) rows.push(`<div><b>Referencia conservadora:</b> ${esc(ref)} pts</div>`);
  if(max!=null) rows.push(`<div><b>Techo:</b> ${esc(max)} pts</div>`);
  if(!rows.length) rows.push(`<pre>${esc(JSON.stringify(obj,null,2))}</pre>`);
  return rows.join('');
}

function renderTable(rows, team){
  $('zoneTable').innerHTML = `<thead><tr><th>#</th><th>Equipo</th><th>PJ</th><th>PTS</th><th>DG</th></tr></thead><tbody>` +
    rows.map(r => `<tr class="${r.team===team?'active':''}"><td>${r.position}</td><td>${esc(r.team)}</td><td>${r.played}</td><td><b>${r.points}</b></td><td>${r.goal_difference>0?'+':''}${r.goal_difference}</td></tr>`).join('') + `</tbody>`;
}

async function loadClub(){
  const team = select.value;
  $('status').textContent = 'Calculando…';
  $('go').disabled = true;
  try{
    const r = await fetch(`/api/club?team=${encodeURIComponent(team)}`);
    const d = await r.json();
    if(!r.ok) throw new Error(d.error || 'No se pudo calcular');
    $('result').classList.remove('hidden');
    $('clubName').textContent = d.team;
    $('zoneLabel').textContent = `ZONA ${d.zone || '—'}`;
    $('zonePos').textContent = pos(d.zoneRow?.position);
    $('zonePts').textContent = d.zoneRow ? `${d.zoneRow.points} pts · ${d.zoneRow.played} PJ` : '—';
    $('annualPos').textContent = pos(d.annualRow?.position);
    $('annualPts').textContent = d.annualRow ? `${d.annualRow.points} pts · ${d.annualRow.played} PJ` : '—';
    const ch = d.playoffs?.chances || {};
    $('playoffChance').textContent = pct(ch.qualification_percentage);
    $('playoffCut').textContent = ch.cutoff ? `Clasifican ${ch.cutoff}` : 'Objetivo: top 8';
    $('gamesLeft').textContent = ch.games_left ?? '—';
    $('ceiling').textContent = ch.ceiling != null ? `Techo ${ch.ceiling} pts` : '—';
    const own = d.preview?.own_match;
    $('nextMatch').textContent = own ? `${own[0]} vs. ${own[1]}` : 'Sin partido identificado';
    $('previewText').innerHTML = d.preview?.markdown ? d.preview.markdown.split('\n').filter(Boolean).slice(0,8).map(x=>`<p>${esc(x.replace(/^#+\s*/,''))}</p>`).join('') : '<p>No disponible.</p>';
    $('playoffBody').innerHTML = objectiveHTML(d.playoffs?.points);
    $('libBody').innerHTML = objectiveHTML(d.libertadores);
    $('sudBody').innerHTML = objectiveHTML(d.sudamericana);
    renderTable(d.zoneTable || [], d.team);
    const issues=(d.quality||[]).filter(x=>x.level==='blocked' || x.level==='warning');
    if(d.reconcileNote) issues.unshift({level:'info',domain:'datos',message:d.reconcileNote});
    if(issues.length){
      $('qualityCard').classList.remove('hidden');
      $('quality').innerHTML=issues.map(i=>`<div class="issue"><b>${esc((i.domain||i.level).toUpperCase())}</b><span>${esc(i.message)}</span></div>`).join('');
    } else $('qualityCard').classList.add('hidden');
    $('status').textContent = d.calculationErrors?.length ? 'Cálculo listo con algunas áreas no disponibles.' : 'Cálculo listo.';
  } catch(e){ $('status').textContent = e.message; }
  finally { $('go').disabled = false; }
}

$('go').addEventListener('click', loadClub);
$('reload').addEventListener('click', loadClub);
select.addEventListener('change', loadClub);
loadTeams().catch(e => $('status').textContent = e.message);
