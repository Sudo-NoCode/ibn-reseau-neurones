import json
import re
import sys
from pathlib import Path

import pandas as pd

ICI = Path(__file__).resolve().parent
sys.path.insert(0, str(ICI.parent / 'phase4'))
from ibn3 import DEMO, DEMO_PROGRESSIF
from jumeau3 import chemins_de

DEMOS = [
    (2, 'Pannes brusques', DEMO),
    (6, 'Pannes qui montent progressivement', DEMO_PROGRESSIF),
    (4, 'Visio exigeante, voix privée de c2', DEMO),
    (5, 'Leviers interdits', DEMO),
]
NOMS = {'congestion_udp': 'congestion (UDP)', 'congestion_tcp': 'congestion (TCP)', 'degrade': 'lien dégradé', 'instable': 'lien instable',
        'double_panne': 'deux liens en panne', 'double_congestion': 'deux gêneurs', 'normal': 'aucune panne'}


def piece(prog, t):
    cur = prog[0]
    for p in prog:
        if p[0] <= t:
            cur = p
    return cur


def une_demo(n, titre, prog):
    d = pd.read_csv(ICI.parent / 'valid_demo' / 'phase4' / f'ibn3_{n}.csv')
    rap = (ICI.parent / 'valid_demo' / 'phase4' / f'ibn3_{n}_rapport.txt').read_text(encoding='utf-8')
    intention = re.search(r'Intentions : « (.*) »', rap).group(1)
    contrats = {}
    for s in ('visio', 'voix'):
        m = re.search(rf'{s.upper()} — contrat : latence ≤ ([\d.]+) ms, pertes ≤ ([\d.]+) %, gigue ≤ ([\d.]+) ms, débit ≥ ([\d.]+) %', rap)
        tenu = re.search(rf'{s.upper()} — contrat.*?\n\s+Contrat tenu : ([\d.]+) %', rap, re.S)
        contrats[s] = dict(lat=float(m.group(1)), pertes=float(m.group(2)), gigue=float(m.group(3)), debit=float(m.group(4)),
                           tenu=float(tenu.group(1)))
    journal = []
    for l in rap.split('JOURNAL', 1)[1].strip().splitlines():
        g = re.match(r'\[\s*(\d+) s\] (.*)', l)
        if g:
            journal.append([int(g.group(1)), g.group(2).strip()])
    secondes = []
    for r in d.itertuples():
        _, sit, detail = piece(prog, r.t)
        liens, geneurs = [], []
        for cible, typ, x in detail:
            if typ in ('udp', 'tcp'):
                geneurs.append(dict(h=cible, type=typ, debit=round(x, 1) if typ == 'udp' else None,
                                    liens=[list(l) for l in chemins_de(cible)[0]]))
            else:
                liens.append(dict(lien=list(cible), type=typ, x=round(x, 2)))
        base = sit.split('|')[0]
        secondes.append(dict(t=int(r.t), panne=NOMS.get(base, base), progressive='|progressive' in sit, liens=liens, geneurs=geneurs,
                             **{s: dict(chemin=int(getattr(r, f'{s}_chemin')), prio=int(getattr(r, f'{s}_prio')),
                                        bride=int(getattr(r, f'{s}_bride')), ok=int(getattr(r, f'{s}_ok')),
                                        lat=float(getattr(r, f'{s}_lat')), pertes=float(getattr(r, f'{s}_pertes')),
                                        gigue=float(getattr(r, f'{s}_gigue')), debit=float(getattr(r, f'{s}_debit')))
                                for s in ('visio', 'voix')}))
    return dict(id=n, titre=titre, intention=intention, contrats=contrats, journal=journal, secondes=secondes)


donnees = dict(demos=[une_demo(*x) for x in DEMOS],
               chemins={s: [[list(l) for l in p] for p in chemins_de(s)] for s in ('visio', 'voix')})
(ICI / 'donnees.js').write_text('const DONNEES = ' + json.dumps(donnees, ensure_ascii=False) + ';\n', encoding='utf-8')
print(f'écrit : {ICI / "donnees.js"} ({len(donnees["demos"])} démos)')
