"""Produit data/operations.json pour la page web (aucune clé n'est écrite).

Modes :
  demo     données fictives            -> data/demo_operations.json
  sync     lecture API (DN_API_TOKEN)  -> data/operations.json
  offline  instantané source_dn.json   -> data/operations.json
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import core  # noqa: E402

HERE = Path(__file__).parent
DATA = HERE.parent / 'data'


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument('--mode', choices=['demo', 'sync', 'offline'], default='demo')
    p.add_argument('--config', default=str(HERE / 'mapping.json'))
    p.add_argument('--communes', default=str(HERE / 'communes_demo.csv'))
    p.add_argument('--source', help='Instantané JSON (mode offline)')
    a = p.parse_args()
    cfg = json.loads(Path(a.config).read_text(encoding='utf-8'))
    demo = a.mode == 'demo'
    try:
        if demo:
            snap = json.loads((HERE / 'demo.json').read_text(encoding='utf-8'))
        elif a.mode == 'offline':
            if not a.source:
                raise core.ImportError95('--source requis pour offline.')
            snap = json.loads(Path(a.source).read_text(encoding='utf-8'))
        else:
            snap = core.fetch_all(os.getenv('DN_API_TOKEN', ''), cfg['demarche'], print)
        records, issues, excluded = core.normalize(snap, cfg, a.communes, allow_unconfirmed=demo)
    except core.ImportError95 as e:
        sys.exit(str(e))
    report = {
        'mode': 'DONNEES FICTIVES' if demo else 'DONNEES DN',
        'demarche': snap['number'], 'recupere_le': snap.get('fetched_at'),
        'dossiers_recuperes': len(snap['dossiers']), 'dossiers_exclus': len(excluded),
        'lignes': len(records), 'controles': len(issues),
        'totaux': {n: sum(r[n] or 0 for r in records) for n in ('vus', 'preleves', 'balles')},
    }
    DATA.mkdir(exist_ok=True)
    out = DATA / ('demo_operations.json' if demo else 'operations.json')
    # Écriture atomique : la page ne lit jamais un fichier à moitié écrit.
    tmp = out.with_suffix('.tmp')
    tmp.write_text(json.dumps({'report': report, 'rows': records, 'issues': issues, 'excluded': excluded},
                              ensure_ascii=False), encoding='utf-8')
    tmp.replace(out)
    print('OK', out, report['lignes'], 'lignes,', report['controles'], 'contrôles')


if __name__ == '__main__':
    main()
