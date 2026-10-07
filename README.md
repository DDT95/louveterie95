# Louveterie 95 — page de l'Atlas territorial

Page statique (`index.html`) sur le modèle des autres pages de l'atlas : barre
`atlas-nav` partagée, bilans par commune / circonscription / lieutenant, cartes
choroplèthes (prélèvements, balles, vus), contrôles qualité.

## Principe : le jeton ne quitte jamais le serveur

```
Démarche Numérique ──(DN_API_TOKEN, IP fixe)──► engine/export_web.py ──► data/operations.json ──► page
```

- `engine/` : moteur Python du kit v0.1.0 (lecture API, règles de calcul), sans dépendance.
- La page ne lit que `data/operations.json` (réel) ou, à défaut, `data/demo_operations.json` (**fictif**).
- `data/operations.json` est dans `.gitignore` : il contient des noms de lieutenants, **ne jamais le versionner ni le publier sur un site public**.

## Utilisation

```bash
# démonstration (déjà générée)
python3 engine/export_web.py --mode demo

# référentiel des communes du 95
python3 engine/fetch_communes.py

# 1. inspecter le formulaire, puis copier engine/mapping.json en mapping_valide.json,
#    associer les champs et passer confirmed à true
export DN_API_TOKEN=...   # jeton lecture seule, démarche 101278 ; jamais dans un fichier
python3 engine/core.py --mode inspect --config engine/mapping.json --output resultats

# 2. produire les données de la page
python3 engine/export_web.py --mode sync --config mapping_valide.json --communes engine/communes95.csv

# hors ligne, depuis un instantané
python3 engine/export_web.py --mode offline --source resultats/INSPECTION_x/source_dn.json \
  --config mapping_valide.json --communes engine/communes95.csv

# visualiser
python3 -m http.server 8000   # puis http://localhost:8000/
```

Le jeton DN impose une **restriction IP** : la synchronisation doit tourner depuis une
machine à IP fixe (poste ou serveur interne), pas depuis GitHub Actions. Planifier
`export_web.py --mode sync` (cron / planificateur Windows) sur cette machine, qui sert aussi le dossier.

## Hébergement interne

GitHub Pages est public (sauf offre Enterprise). Pour un usage interne, servir ce dossier
depuis un intranet ou un serveur restreint. Publié sur GitHub Pages, le site n'affiche que la
démonstration fictive, car `operations.json` n'est jamais dans le dépôt.

## Règles de calcul

Année cynégétique 1er juillet–30 juin (S1 juil.–déc., S2 janv.–juin) ; cases vides = NULL
(jamais 0) ; ratio = 100 × prélevés / balles sans plafond ; communes inconnues conservées dans
les totaux mais non cartographiées. Détail dans le README du kit v0.1.0.

## Limites à valider

- Requête API jamais exécutée sur la démarche 101278 (pas de jeton) ; correspondances de champs à configurer.
- Carte : contours via geo.api.gouv.fr et fond CARTO chargés depuis le navigateur ; non testée visuellement (réseau du bac à sable bloqué).
- Historique PENB non importé.
