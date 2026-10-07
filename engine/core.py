"""Louveterie 95: client de lecture DN et transformation indépendante de QGIS."""
import argparse
import csv
import datetime as dt
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import unicodedata
import urllib.error
import urllib.request
import uuid

ENDPOINT = 'https://demarche.numerique.gouv.fr/api/v2/graphql'
QUERY = '''query Louveterie($number: Int!, $after: String, $archived: Boolean!) {
 demarche(number: $number) {
  number title
  dossiers(first: 50, after: $after, archived: $archived) {
   pageInfo { endCursor hasNextPage }
   nodes { id number state archived dateDerniereModification
    champs { ...Fields
     ... on RepetitionChamp { rows { id champs { ...Fields } } }
    }
   }
  }
 }
}
fragment Fields on Champ { id champDescriptorId __typename label stringValue }
'''

class ImportError95(Exception):
    pass

def key(s):
    s = unicodedata.normalize('NFKD', str(s or ''))
    return re.sub(r'[^a-z0-9]', '', ''.join(c for c in s.lower() if not unicodedata.combining(c)))

def json_write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

def request_api(token, variables, cancel=lambda: False):
    if not token:
        raise ImportError95('Clé absente : renseigner DN_API_TOKEN ou le champ masqué du plugin.')
    payload = json.dumps({'query': QUERY, 'variables': variables}).encode()
    for attempt in range(4):
        if cancel():
            raise ImportError95('Import annulé. Aucun bilan remplacé.')
        req = urllib.request.Request(ENDPOINT, data=payload, headers={
            'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json',
            'User-Agent': 'Louveterie95/0.1'})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                value = json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                # Attente bornée; pas de secrets dans les messages.
                try:
                    delay = min(30, max(1, int(e.headers.get('Retry-After', 2 ** attempt))))
                except ValueError:
                    delay = 2 ** attempt
                for _ in range(delay * 10):
                    if cancel():
                        raise ImportError95('Import annulé.')
                    time.sleep(.1)
                continue
            raise ImportError95('API HTTP %s : vérifier la clé, ses droits et la démarche.' % e.code) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            raise ImportError95('Connexion impossible : réseau, proxy ou délai API. Aucun bilan remplacé.') from None
        except (ValueError, UnicodeError):
            raise ImportError95('Réponse API non JSON. Aucun bilan remplacé.') from None
        if value.get('errors'):
            # Ne pas réémettre des valeurs saisies ou du contenu serveur arbitraire.
            raise ImportError95('Erreur GraphQL : droits ou schéma de requête à vérifier. Aucun bilan remplacé.')
        d = (value.get('data') or {}).get('demarche')
        if not d or not isinstance(d.get('dossiers'), dict):
            raise ImportError95('Démarche inaccessible ou réponse incomplète.')
        return d
    raise ImportError95('API indisponible.')

def fetch_all(token, number=101278, progress=lambda msg: None, cancel=lambda: False, transport=None):
    """Lecture complète en deux passages explicites: actifs puis archivés."""
    call = transport or (lambda v: request_api(token, v, cancel))
    dossiers, title = {}, ''
    for archived in (False, True):
        cursor, visited = None, set()
        while True:
            if cancel():
                raise ImportError95('Import annulé.')
            d = call({'number': int(number), 'after': cursor, 'archived': archived})
            title = d.get('title', '')
            conn = d['dossiers']
            nodes = conn.get('nodes')
            info = conn.get('pageInfo')
            if not isinstance(nodes, list) or not isinstance(info, dict):
                raise ImportError95('Pagination API incomplète.')
            for item in nodes:
                if not item.get('id') or 'champs' not in item:
                    raise ImportError95('Dossier incomplet.')
                old = dossiers.get(item['id'])
                if old and old != item:
                    raise ImportError95('Un dossier a changé pendant la lecture. Relancer la synchronisation.')
                dossiers[item['id']] = item
            progress('%s : %s dossiers uniques' % ('Archivés' if archived else 'Actifs', len(dossiers)))
            if info.get('hasNextPage') is False:
                break
            cursor = info.get('endCursor')
            if info.get('hasNextPage') is not True or not cursor or cursor in visited:
                raise ImportError95('Curseur de pagination invalide. Import interrompu.')
            visited.add(cursor)
    return {'number': int(number), 'title': title, 'fetched_at': dt.datetime.now(dt.timezone.utc).isoformat(),
            'dossiers': list(dossiers.values())}

def inventory(snapshot):
    result = {}
    def walk(champs, parent='racine'):
        for c in champs:
            ident = c.get('champDescriptorId', '')
            k = (parent, ident, c.get('label', ''))
            result[k] = {'parent': parent, 'id': ident, 'label': c.get('label', ''), 'type': c.get('__typename', '')}
            for row in c.get('rows', []):
                walk(row.get('champs', []), ident or c.get('label', ''))
    for d in snapshot['dossiers']:
        walk(d['champs'])
    return list(result.values())

def select(champs, selector, required=False):
    if selector is None:
        if required:
            raise ImportError95('Correspondance de champ obligatoire absente.')
        return None
    ids, labels = selector.get('ids', []), selector.get('labels', [])
    # Un ID configuré prime sur les libellés, pour résister aux renommages.
    found = [c for c in champs if c.get('champDescriptorId') in ids] if ids else [
        c for c in champs if key(c.get('label')) in {key(v) for v in labels}]
    if len(found) > 1:
        raise ImportError95('Champ ambigu : configurer ses identifiants dans mapping.json.')
    if not found and required:
        raise ImportError95('Champ introuvable : comparer mapping.json avec inventaire_champs.json.')
    return found[0] if found else None

def text_value(champs, selector, required=False):
    c = select(champs, selector, required)
    return str(c.get('stringValue') or '').strip() if c else ''

def species(s, cfg):
    return cfg.get('species_aliases', {}).get(key(s), str(s).strip() or 'Non renseignée')

def parse_date(s):
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y'):
        try:
            d = dt.datetime.strptime(s, fmt).date()
            if not 2000 <= d.year <= dt.date.today().year + 1:
                raise ValueError()
            return d
        except ValueError:
            pass
    raise ValueError('date invalide')

def integer(s):
    if not s.strip():
        return None
    if not re.fullmatch(r'\d+', s.strip()):
        raise ValueError('entier positif attendu')
    return int(s)

def references(path):
    with open(path, encoding='utf-8-sig', newline='') as f:
        rows = list(csv.DictReader(f, delimiter=';'))
    by_name, by_code = {}, {}
    for r in rows:
        code, name = r.get('insee_com', ''), r.get('commune', '')
        if not re.fullmatch(r'95\d{3}', code) or not name:
            raise ImportError95('Référentiel : champs insee_com (95xxx) et commune requis, séparateur ;.')
        if code in by_code or key(name) in by_name:
            raise ImportError95('Référentiel communal ambigu ou doublonné.')
        by_code[code] = r
        by_name[key(name)] = r
    if not rows:
        raise ImportError95('Référentiel communal vide.')
    return by_name, by_code

def normalize(snapshot, cfg, ref_path, allow_unconfirmed=False):
    if not cfg.get('confirmed') and not allow_unconfirmed:
        raise ImportError95('Mapping non validé. Vérifier les champs puis mettre confirmed à true.')
    by_name, by_code = references(ref_path)
    records, issues, excluded = [], [], []
    seen = set()
    root = cfg['root']
    for d in snapshot['dossiers']:
        if d['state'] not in cfg['states']:
            excluded.append({'dossier': d['number'], 'etat': d['state']})
            continue
        champs = d['champs']
        date_s = text_value(champs, root['date'], True)
        try:
            date = parse_date(date_s)
        except ValueError:
            raise ImportError95('Date invalide ou absente, dossier %s. Corriger avant publication.' % d['number'])
        y = date.year - (date.month < cfg.get('year_start_month', 7))
        semester = 1 if date.month >= cfg.get('year_start_month', 7) else 2
        lieutenant = text_value(champs, root['lieutenant'], True).upper()
        circo = text_value(champs, root['circonscription'], True)
        cm = re.match(r'\s*(\d+)', circo)
        circo = str(int(cm.group(1))) if cm else circo
        if not lieutenant or not circo:
            raise ImportError95('Lieutenant/circonscription absents, dossier %s.' % d['number'])
        used_groups = set()
        nrows = 0
        for group in cfg['groups']:
            block = select(champs, group['selector'])
            if not block:
                continue  # Un ancien formulaire peut ne pas avoir ce bloc.
            if block.get('id') in used_groups:
                raise ImportError95('Deux groupes sélectionnent le même bloc répétable.')
            used_groups.add(block.get('id'))
            if block.get('__typename') != 'RepetitionChamp' or 'rows' not in block:
                raise ImportError95('Le groupe configuré ne correspond pas à un bloc répétable.')
            for row in block['rows']:
                fields = row.get('champs', [])
                # Ligne vide ajoutée automatiquement par le formulaire.
                if not any(str(c.get('stringValue') or '').strip() for c in fields):
                    continue
                rid = row.get('id')
                if not rid:
                    raise ImportError95('Identifiant de ligne répétable absent.')
                ident = '%s/%s/%s' % (d['id'], block['champDescriptorId'], rid)
                if ident in seen:
                    raise ImportError95('Identifiant de ligne répété : import interrompu.')
                seen.add(ident)
                nrows += 1
                raw_commune = text_value(fields, group['fields']['commune'], True)
                aliases = cfg.get('commune_aliases', {})
                lookup = aliases.get(key(raw_commune), raw_commune)
                ref = by_code.get(lookup) or by_name.get(key(lookup))
                if not ref:
                    issues.append({'dossier': d['number'], 'ligne_id': ident, 'type': 'commune_inconnue', 'detail': raw_commune})
                sp = text_value(fields, group['fields'].get('espece'))
                sp = sp or text_value(champs, root[group['species_fallback']])
                sp = species(sp, cfg)
                if sp == 'Non renseignée':
                    issues.append({'dossier': d['number'], 'ligne_id': ident, 'type': 'espece_absente', 'detail': ''})
                nums = {}
                for n in ('vus', 'preleves', 'balles'):
                    sel = group['fields'].get(n)
                    if sel is None:  # Observation: aucun tir/prélèvement par définition du bloc.
                        nums[n] = 0
                        continue
                    value = text_value(fields, sel, True)
                    try:
                        nums[n] = integer(value)
                    except ValueError:
                        raise ImportError95('Valeur %s invalide, dossier %s. Corriger avant publication.' % (n, d['number']))
                    if nums[n] is None:
                        issues.append({'dossier': d['number'], 'ligne_id': ident, 'type': 'compte_absent', 'detail': n})
                records.append({'ligne_id': ident, 'dossier_id': d['id'], 'dossier': d['number'],
                    'etat': d['state'], 'archive': int(d.get('archived', False)),
                    'date': date.isoformat(), 'annee': '%s_%s' % (y, y + 1), 'semestre': semester,
                    'lieutenant': lieutenant, 'circonscription': circo,
                    'arrete': text_value(champs, root.get('arrete')), 'type': group['kind'],
                    'espece': sp, 'commune_saisie': raw_commune,
                    'insee_com': ref['insee_com'] if ref else None,
                    'commune': ref['commune'] if ref else raw_commune, **nums})
        if not nrows:
            issues.append({'dossier': d['number'], 'ligne_id': '', 'type': 'aucune_ligne', 'detail': 'Vérifier les blocs ou un dossier sans activité.'})
    if snapshot['dossiers'] and not records:
        raise ImportError95('Aucune ligne exploitable : vérifier les états et les correspondances des blocs.')
    return records, issues, excluded

RECORD_COLS = {'ligne_id':'TEXT', 'dossier_id':'TEXT', 'dossier':'INTEGER', 'etat':'TEXT', 'archive':'INTEGER',
 'date':'TEXT', 'annee':'TEXT', 'semestre':'INTEGER', 'lieutenant':'TEXT', 'circonscription':'TEXT',
 'arrete':'TEXT', 'type':'TEXT', 'espece':'TEXT', 'commune_saisie':'TEXT', 'insee_com':'TEXT', 'commune':'TEXT',
 'vus':'INTEGER', 'preleves':'INTEGER', 'balles':'INTEGER'}
SUM_COLS = {'nb_lignes':'INTEGER', 'nb_lignes_prelevement':'INTEGER', 'nb_dossiers':'INTEGER', 'nb_lieutenants':'INTEGER',
 'vus':'INTEGER', 'preleves':'INTEGER', 'balles':'INTEGER', 'comptes_absents':'INTEGER', 'preleves_par_100_balles':'REAL'}

def aggregate(records, dimensions):
    groups = {}
    for r in records:
        k = tuple(r[d] for d in dimensions)
        groups.setdefault(k, []).append(r)
    result = []
    for k, rows in groups.items():
        totals = {n: sum(r[n] or 0 for r in rows) for n in ('vus', 'preleves', 'balles')}
        missing = sum(r[n] is None for r in rows for n in ('vus', 'preleves', 'balles'))
        ratio = 100 * totals['preleves'] / totals['balles'] if totals['balles'] and not missing else None
        result.append({**dict(zip(dimensions, k)), 'nb_lignes':len(rows),
            'nb_lignes_prelevement':sum(r['type']=='prelevement' for r in rows),
            'nb_dossiers':len({r['dossier_id'] for r in rows}), 'nb_lieutenants':len({r['lieutenant'] for r in rows}),
            **totals, 'comptes_absents':missing, 'preleves_par_100_balles':ratio})
    return result

DIMENSIONS = {
 'bilan_commune': ['annee','semestre','espece','insee_com','commune'],
 'bilan_lieutenant': ['annee','semestre','espece','lieutenant'],
 'bilan_circonscription': ['annee','semestre','espece','circonscription'],
 'bilan_annuel_commune': ['annee','espece','insee_com','commune'],
}

def create_gpkg(path, records, issues, excluded):
    """GeoPackage de tables attributaires conformes; géométries ajoutées par QGIS."""
    db = sqlite3.connect(str(path))
    try:
        db.execute('PRAGMA application_id=1196444487')
        db.execute('PRAGMA user_version=10300')
        db.executescript('''CREATE TABLE gpkg_spatial_ref_sys (
        srs_name TEXT NOT NULL, srs_id INTEGER NOT NULL PRIMARY KEY,
        organization TEXT NOT NULL, organization_coordsys_id INTEGER NOT NULL,
        definition TEXT NOT NULL, description TEXT);
        INSERT INTO gpkg_spatial_ref_sys VALUES ('Undefined Cartesian',-1,'NONE',-1,'undefined',NULL);
        INSERT INTO gpkg_spatial_ref_sys VALUES ('Undefined geographic',0,'NONE',0,'undefined',NULL);
        INSERT INTO gpkg_spatial_ref_sys VALUES ('WGS 84',4326,'EPSG',4326,
        'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433],AUTHORITY["EPSG","4326"]]',NULL);
        CREATE TABLE gpkg_contents (table_name TEXT NOT NULL PRIMARY KEY,
        data_type TEXT NOT NULL, identifier TEXT UNIQUE, description TEXT DEFAULT '',
        last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE, srs_id INTEGER,
        FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id));''')
        tables = {'operations':(records, RECORD_COLS),
                  'controles':(issues, {'dossier':'INTEGER','ligne_id':'TEXT','type':'TEXT','detail':'TEXT'}),
                  'dossiers_exclus':(excluded, {'dossier':'INTEGER','etat':'TEXT'})}
        for name, dims in DIMENSIONS.items():
            tables[name] = (aggregate(records, dims), {**{d:RECORD_COLS[d] for d in dims}, **SUM_COLS})
        for name, (rows, cols) in tables.items():
            db.execute('CREATE TABLE "%s" (fid INTEGER PRIMARY KEY AUTOINCREMENT, %s)' % (
                name, ','.join('"%s" %s' % (c, typ) for c, typ in cols.items())))
            db.executemany('INSERT INTO "%s" (%s) VALUES (%s)' % (name, ','.join('"%s"'%c for c in cols), ','.join('?' for _ in cols)),
                           [tuple(r.get(c) for c in cols) for r in rows])
            db.execute('INSERT INTO gpkg_contents(table_name,data_type,identifier) VALUES (?,?,?)', (name,'attributes',name))
        db.execute('CREATE UNIQUE INDEX operation_identifiant ON operations(ligne_id)')
        db.execute('CREATE INDEX operation_filtre ON operations(annee,espece,semestre)')
        db.commit()
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ImportError95('GeoPackage invalide.')
        return tables
    finally:
        db.close()

def csv_write(path, rows, cols):
    with open(path, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(cols), delimiter=';')
        w.writeheader()
        for r in rows:
            # Empêcher les valeurs texte d'être interprétées comme des formules Excel.
            w.writerow({k: ("'" + v if isinstance(v, str) and v.lstrip().startswith(('=','+','-','@')) else v) for k,v in r.items() if k in cols})

def publish(snapshot, cfg, ref_path, output_dir, demo=False, progress=lambda msg: None, cancel=lambda: False):
    """Un répertoire immuable par synchronisation; l'ancien fichier reste disponible."""
    records, issues, excluded = normalize(snapshot, cfg, ref_path, allow_unconfirmed=demo)
    if cancel():
        raise ImportError95('Import annulé.')
    folder = Path(output_dir) / (('DEMO_' if demo else 'SYNC_') + dt.datetime.now().strftime('%Y%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6])
    folder.mkdir(parents=True)
    try:
        progress('Création des bilans et contrôles…')
        tables = create_gpkg(folder/'louveterie95.gpkg', records, issues, excluded)
        for name, (rows, cols) in tables.items():
            csv_write(folder/(name+'.csv'), rows, cols)
        # Instantané protégé par les permissions du dossier local, contient les valeurs de champs.
        json_write(folder/'source_dn.json', snapshot)
        json_write(folder/'mapping_utilise.json', cfg)
        report = {'mode':'DONNEES FICTIVES' if demo else 'DONNEES DN', 'demarche':snapshot['number'],
            'recupere_le': snapshot.get('fetched_at'), 'dossiers_recuperes':len(snapshot['dossiers']),
            'dossiers_exclus':len(excluded), 'lignes':len(records), 'controles':len(issues),
            'communes_inconnues':len({r['commune_saisie'] for r in records if not r['insee_com']}),
            'totaux':{n:sum(r[n] or 0 for r in records) for n in ('vus','preleves','balles')},
            'comptes_absents':sum(r[n] is None for r in records for n in ('vus','preleves','balles')),
            'source_sha256':hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest()}
        json_write(folder/'rapport.json', report)
        dashboard(folder/'bilan.html', records, report)
        if cancel():
            raise ImportError95('Import annulé. Le dossier incomplet ne doit pas être utilisé.')
        json_write(folder/'TERMINE.json', {'publication_complete':True})
        progress('Terminé : %s lignes, %s contrôles.' % (len(records),len(issues)))
        return str(folder), report
    except Exception:
        # Un résultat incomplet ne porte jamais le marqueur TERMINE.
        raise

def dashboard(path, rows, report):  # conservé du kit; non utilisé par la page web
    template = Path(__file__).with_name('dashboard.html').read_text(encoding='utf-8')
    # Empêche une valeur de champ de fermer un élément script.
    payload = json.dumps({'rows':rows,'report':report},ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    Path(path).write_text(template.replace('__PAYLOAD__',payload),encoding='utf-8')

def main():
    p = argparse.ArgumentParser(description='Louveterie 95 • API → GeoPackage → bilans')
    p.add_argument('--mode',choices=['demo','inspect','sync','offline'],default='demo')
    p.add_argument('--config',default=str(Path(__file__).with_name('mapping.json')))
    p.add_argument('--communes',default=str(Path(__file__).with_name('communes_demo.csv')))
    p.add_argument('--source', help='Instantané JSON pour mode offline')
    p.add_argument('--output',default='resultats_louveterie')
    args = p.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding='utf-8'))
    try:
        if args.mode == 'demo':
            snapshot = json.loads(Path(__file__).with_name('demo.json').read_text(encoding='utf-8'))
        elif args.mode == 'offline':
            if not args.source:
                raise ImportError95('--source requis pour offline.')
            snapshot = json.loads(Path(args.source).read_text(encoding='utf-8'))
        else:
            snapshot = fetch_all(os.getenv('DN_API_TOKEN',''),cfg['demarche'],print)
        if args.mode == 'inspect':
            folder = Path(args.output) / ('INSPECTION_'+dt.datetime.now().strftime('%Y%m%d_%H%M%S'))
            folder.mkdir(parents=True,exist_ok=True)
            json_write(folder/'inventaire_champs.json',inventory(snapshot))
            json_write(folder/'source_dn.json',snapshot)
            print('Inventaire sans clé enregistré dans',folder)
        else:
            folder, report = publish(snapshot,cfg,args.communes,args.output,args.mode=='demo',print)
            print(folder)
            print(json.dumps(report,ensure_ascii=False,indent=2))
    except ImportError95 as e:
        p.exit(1,str(e)+'\n')

if __name__ == '__main__':
    main()
