'use strict';
const $ = id => document.getElementById(id);
const fmt = x => x === null || x === undefined ? '—' : new Intl.NumberFormat('fr-FR', {maximumFractionDigits: 1}).format(x);
const SPECIES_ORDER = ['Sanglier'];
let DATA = {rows: [], issues: [], excluded: [], report: {}};
const filters = {};

async function load() {
  // Données réelles si présentes (servies en interne), sinon démonstration fictive.
  for (const url of ['data/operations.json', 'data/demo_operations.json']) {
    try {
      const r = await fetch(url, {cache: 'no-store'});
      if (r.ok) return r.json();
    } catch (e) { /* essai suivant */ }
  }
  throw new Error('Aucune donnée disponible.');
}

function buildFilters() {
  const rows = DATA.rows;
  for (const [field, label] of [['annee', 'Année cynégétique'], ['espece', 'Espèce'], ['semestre', 'Semestre'], ['circonscription', 'Circonscription'], ['lieutenant', 'Lieutenant']]) {
    const l = document.createElement('label');
    l.textContent = label;
    const s = document.createElement('select');
    s.append(new Option('Tous', ''));
    const values = [...new Set(rows.map(r => String(r[field])))].sort((a, b) => a.localeCompare(b, 'fr', {numeric: true}));
    for (const v of values) s.append(new Option(field === 'annee' ? v.replace('_', '–') : v, v));
    if (field === 'annee' && values.length) s.value = values[values.length - 1];
    if (field === 'espece') s.value = values.find(v => SPECIES_ORDER.includes(v)) || '';
    s.onchange = render;
    l.append(s);
    $('filters').append(l);
    filters[field] = s;
  }
  const b = document.createElement('button');
  b.textContent = 'Exporter le bilan CSV';
  b.onclick = exportCsv;
  $('filters').append(b);
}

const current = () => DATA.rows.filter(r => Object.entries(filters).every(([f, s]) => !s.value || String(r[f]) === s.value));

function group(rs, dim) {
  const m = new Map();
  for (const r of rs) {
    const name = r[dim] || 'Non renseigné';
    if (!m.has(name)) m.set(name, {name, rows: [], dossiers: new Set(), people: new Set(), vus: 0, preleves: 0, balles: 0, missing: 0});
    const g = m.get(name);
    g.rows.push(r); g.dossiers.add(r.dossier_id); g.people.add(r.lieutenant);
    for (const f of ['vus', 'preleves', 'balles']) { g[f] += r[f] || 0; if (r[f] === null) g.missing++; }
  }
  for (const g of m.values()) g.ratio = g.balles && !g.missing ? 100 * g.preleves / g.balles : null;
  return [...m.values()].sort((a, b) => b.preleves - a.preleves || a.name.localeCompare(b.name, 'fr'));
}

function render() {
  const rs = current();
  const sum = f => rs.reduce((t, r) => t + (r[f] || 0), 0);
  $('cards').replaceChildren();
  for (const [v, label] of [[sum('preleves'), 'Animaux prélevés'], [sum('balles'), 'Balles déclarées'], [sum('vus'), 'Vus déclarés*'],
      [new Set(rs.map(r => r.dossier_id)).size, 'Dossiers distincts'], [new Set(rs.filter(r => r.insee_com).map(r => r.insee_com)).size, 'Communes reconnues']]) {
    const c = document.createElement('div'); c.className = 'card';
    const s = document.createElement('strong'); s.textContent = fmt(v);
    const l = document.createElement('span'); l.textContent = label;
    c.append(s, l); $('cards').append(c);
  }
  const unknown = rs.filter(r => !r.insee_com).length;
  const missing = rs.reduce((n, r) => n + ['vus', 'preleves', 'balles'].filter(f => r[f] === null).length, 0);
  $('warning').textContent = `${unknown} ligne(s) sans commune reconnue, conservée(s) dans les totaux · ${missing} compte(s) absent(s) dans la sélection · ${DATA.report.controles} contrôle(s) dans le rapport complet.`;

  const gs = group(rs, 'lieutenant'), max = Math.max(1, ...gs.map(x => x.preleves));
  $('bars').replaceChildren();
  for (const g of gs) {
    const row = document.createElement('div'); row.className = 'barrow';
    const n = document.createElement('span'); n.textContent = g.name;
    const t = document.createElement('div'); t.className = 'track';
    const b = document.createElement('div'); b.className = 'bar'; b.style.width = (100 * g.preleves / max) + '%';
    t.append(b);
    const v = document.createElement('span'); v.className = 'num'; v.textContent = fmt(g.preleves);
    row.append(n, t, v); $('bars').append(row);
  }

  $('table').replaceChildren();
  for (const g of group(rs, $('dimension').value)) {
    const tr = document.createElement('tr');
    for (const v of [g.name, g.rows.length, g.dossiers.size, g.people.size, g.vus, g.preleves, g.balles, g.ratio]) {
      const td = document.createElement('td'); td.textContent = typeof v === 'string' ? v : fmt(v); tr.append(td);
    }
    $('table').append(tr);
  }
  if (mapReady) updateMap();
}

function exportCsv() {
  const dim = $('dimension').value;
  const header = [dim, 'nb_lignes', 'nb_dossiers', 'nb_lieutenants', 'vus', 'preleves', 'balles', 'preleves_par_100_balles'];
  const esc = v => '"' + String(v ?? '').replace(/^[=+\-@]/, "'$&").replaceAll('"', '""') + '"';
  const body = group(current(), dim).map(g => [g.name, g.rows.length, g.dossiers.size, g.people.size, g.vus, g.preleves, g.balles, g.ratio]);
  const csv = '﻿' + [header, ...body].map(r => r.map(esc).join(';')).join('\r\n');
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([csv], {type: 'text/csv;charset=utf-8'}));
  a.download = 'bilan_louveterie.csv'; a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function fillControls() {
  for (const i of DATA.issues || []) {
    const tr = document.createElement('tr');
    for (const v of [i.dossier, i.type, i.detail, i.ligne_id]) { const td = document.createElement('td'); td.textContent = v ?? ''; tr.append(td); }
    $('issues').append(tr);
  }
  if (!(DATA.issues || []).length) { const tr = document.createElement('tr'), td = document.createElement('td'); td.colSpan = 4; td.textContent = 'Aucun contrôle.'; tr.append(td); $('issues').append(tr); }
  for (const e of DATA.excluded || []) {
    const tr = document.createElement('tr');
    for (const v of [e.dossier, e.etat]) { const td = document.createElement('td'); td.textContent = v; tr.append(td); }
    $('excluded').append(tr);
  }
}

/* ---------- Carte ---------- */
let map, layer, geo, mapReady = false, legend;
const CLASSES = [[0, 0, '#ffffff', '0'], [1, 5, '#c9d0ff', '1 – 5'], [6, 20, '#8a96ff', '6 – 20'], [21, 50, '#3b45d6', '21 – 50'], [51, Infinity, '#000091', '> 50']];
const NO_DECL = '#e6e6e6', INCOMPLETE = '#f2c14e';

async function initMap() {
  if (mapReady || map) return;
  if (!window.L) { $('map').textContent = 'Bibliothèque cartographique inaccessible.'; $('map').className = 'maperr'; return; }
  map = L.map('map').setView([49.07, 2.13], 9);
  L.tileLayer('https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}.png', {attribution: '© OpenStreetMap, © CARTO', maxZoom: 14}).addTo(map);
  try {
    const r = await fetch('https://geo.api.gouv.fr/communes?codeDepartement=95&format=geojson&geometry=contour&fields=code,nom');
    if (!r.ok) throw new Error(r.status);
    geo = await r.json();
  } catch (e) {
    const d = document.createElement('div'); d.className = 'maperr';
    d.textContent = 'Contours des communes indisponibles (geo.api.gouv.fr). Les bilans restent disponibles dans l’onglet Bilan.';
    $('map').before(d); return;
  }
  legend = L.control({position: 'bottomright'});
  legend.onAdd = () => { const d = L.DomUtil.create('div', 'legend'); d.id = 'legend'; return d; };
  legend.addTo(map);
  mapReady = true; updateMap();
}

function updateMap() {
  if (!geo) return;
  const metric = $('metric').value, label = $('metric').selectedOptions[0].textContent;
  const byInsee = new Map();
  for (const r of current()) if (r.insee_com) {
    const a = byInsee.get(r.insee_com) || {v: 0, miss: false};
    a.v += r[metric] || 0; if (r[metric] === null) a.miss = true;
    byInsee.set(r.insee_com, a);
  }
  const style = f => {
    const a = byInsee.get(f.properties.code);
    let fill = NO_DECL;
    if (a) fill = a.miss ? INCOMPLETE : (CLASSES.find(c => a.v >= c[0] && a.v <= c[1]) || CLASSES[4])[2];
    return {fillColor: fill, fillOpacity: .85, color: '#6b7390', weight: .6};
  };
  if (layer) layer.remove();
  layer = L.geoJSON(geo, {style, onEachFeature: (f, l) => {
    const a = byInsee.get(f.properties.code);
    l.bindTooltip(f.properties.nom + ' · ' + (a ? fmt(a.v) + (a.miss ? ' (incomplet)' : '') : 'aucune déclaration'));
  }}).addTo(map);
  const lg = $('legend');
  if (lg) {
    lg.replaceChildren();
    const t = document.createElement('b'); t.textContent = label; lg.append(t);
    for (const [fill, text] of [...CLASSES.map(c => [c[2], c[3]]), [INCOMPLETE, 'Données incomplètes'], [NO_DECL, 'Aucune déclaration']]) {
      const row = document.createElement('div'), i = document.createElement('i'); i.style.background = fill;
      row.append(i, document.createTextNode(text)); lg.append(row);
    }
  }
}

/* ---------- Onglets ---------- */
document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => {
  document.querySelectorAll('.tabs button').forEach(x => x.setAttribute('aria-selected', x === b));
  for (const id of ['bilan', 'cartes', 'controles']) $(id).hidden = id !== b.dataset.tab;
  if (b.dataset.tab === 'cartes') { initMap().then(() => map && map.invalidateSize()); }
});
$('dimension').onchange = render;
$('metric').onchange = () => updateMap();

load().then(d => {
  DATA = d;
  const real = d.report.mode === 'DONNEES DN';
  $('mode').textContent = real ? 'Données Démarche Numérique' : 'Données fictives · démonstration';
  $('mode').classList.toggle('real', real);
  $('stamp').textContent = `Démarche ${d.report.demarche} · ${d.report.recupere_le || 'Démonstration'} · ${d.rows.length} ligne(s)`;
  buildFilters(); fillControls(); render();
}).catch(e => { $('mode').textContent = 'Erreur : ' + e.message; });
