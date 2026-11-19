import csv
import json
import sys
import time

from reseau3 import LIENS_MONTANTS, SERVICES, Reseau3, etat_lien
from valide_mininet import GardeGel

T_PANNE, T_LEVIERS, T_FIN, T_TOTAL = 6, 18, 30, 34
PANNE = {'degrade': lambda x: (1.5 + 100 * x, 1.0, 5 * x),
         'instable': lambda x: (25.0, 20 * x, 0.2)}


def essai(nom, pannes=(), geneurs=(), leviers=()):
    return dict(nom=nom, pannes=list(pannes), geneurs=list(geneurs), leviers=list(leviers))


ESSAIS = (
    [essai(f'A degrade {x} d1-c1', pannes=[('d1', 'c1', 'degrade', x)]) for x in (0.4, 0.7, 1.0, 1.5)]
    + [essai(f'A instable {x} d1-c1', pannes=[('d1', 'c1', 'instable', x)]) for x in (0.4, 0.7, 1.0, 1.5)]
    + [essai('A degrade 1.0 a1-d1', pannes=[('a1', 'd1', 'degrade', 1.0)]),
       essai('A instable 1.0 c1-a3', pannes=[('c1', 'a3', 'instable', 1.0)]),
       essai('A degrade 1.0 d1-c1 puis visio par d2-c2', pannes=[('d1', 'c1', 'degrade', 1.0)], leviers=[('chemin', 'visio', 3)]),
       essai('A instable 1.0 d1-c1 puis voix par d1-c2', pannes=[('d1', 'c1', 'instable', 1.0)], leviers=[('chemin', 'voix', 1)])]
    + [essai(f'B h2 {r} Mb/s', geneurs=[('h2', r)]) for r in (12, 17, 20, 25, 35)]
    + [essai(f'B h2 {r} Mb/s puis priorite visio', geneurs=[('h2', r)], leviers=[('priorite', 'visio')]) for r in (20, 25, 35)]
    + [essai('B h2 25 Mb/s puis h2 bride', geneurs=[('h2', 25)], leviers=[('limiter', 'h2')]),
       essai('B h2 25 Mb/s puis visio par d2-c2', geneurs=[('h2', 25)], leviers=[('chemin', 'visio', 3)]),
       essai('B h2 25 Mb/s puis visio par d1-c2', geneurs=[('h2', 25)], leviers=[('chemin', 'visio', 1)]),
       essai('B h4 25 Mb/s', geneurs=[('h4', 25)]),
       essai('B h4 25 Mb/s puis priorite voix', geneurs=[('h4', 25)], leviers=[('priorite', 'voix')]),
       essai('B h2 15 + h4 15 Mb/s', geneurs=[('h2', 15), ('h4', 15)]),
       essai('B h2 15 + h4 15 Mb/s puis les deux priorites', geneurs=[('h2', 15), ('h4', 15)],
             leviers=[('priorite', 'visio'), ('priorite', 'voix')]),
       essai('B h2 tcp4', geneurs=[('h2', 'tcp4')]),
       essai('B h2 tcp4 puis priorite visio', geneurs=[('h2', 'tcp4')], leviers=[('priorite', 'visio')]),
       essai('B h2 tcp4 puis h2 bride', geneurs=[('h2', 'tcp4')], leviers=[('limiter', 'h2')])]
    + [essai('C degrade 0.7 a1-d1 + degrade 0.7 d1-c1', pannes=[('a1', 'd1', 'degrade', 0.7), ('d1', 'c1', 'degrade', 0.7)]),
       essai('C instable 1.0 c1-a3 + degrade 1.0 d1-c1', pannes=[('c1', 'a3', 'instable', 1.0), ('d1', 'c1', 'degrade', 1.0)]),
       essai('C degrade 1.0 d1-c1 + h2 25 Mb/s', pannes=[('d1', 'c1', 'degrade', 1.0)], geneurs=[('h2', 25)]),
       essai('C degrade 1.0 d2-c2 + h2 25 Mb/s puis visio par d2-c2', pannes=[('d2', 'c2', 'degrade', 1.0)],
             geneurs=[('h2', 25)], leviers=[('chemin', 'visio', 3)]),
       essai('C h2 25 + h4 25 Mb/s puis priorite visio', geneurs=[('h2', 25), ('h4', 25)], leviers=[('priorite', 'visio')])]
)

COLONNES = (['essai', 'nom', 't', 'phase', 'gel_ms']
            + [f'{s}_{k}' for s in SERVICES for k in ('lat', 'pertes', 'gigue', 'debit', 'sonde0', 'sonde1', 'sonde2', 'sonde3')]
            + [f'{k}_{a}_{b}' for a, b in LIENS_MONTANTS for k in ('occ', 'file', 'jetes')] + ['config'])


def jouer(r, garde, w, i, e):
    lv = r.leviers
    for t in range(T_TOTAL):
        if t == T_PANNE:
            for a, b, typ, x in e['pannes']:
                etat_lien(r.net, a, b, *PANNE[typ](x))
            for h, debit in e['geneurs']:
                tcp = int(debit[3:]) if isinstance(debit, str) else 0
                r.gener(h, 0 if tcp else debit, T_FIN - T_PANNE, tcp=tcp)
        if t == T_LEVIERS:
            for l in e['leviers']:
                {'priorite': lambda s: lv.priorite(s, True), 'chemin': lv.chemin, 'limiter': lambda h: lv.limiter(h, True)}[l[0]](*l[1:])
        if t == T_FIN:
            for a, b, _, _ in e['pannes']:
                etat_lien(r.net, a, b)
            r.calmer()
        m = r.tick()
        phase = 'avant' if t < T_PANNE else 'panne' if t < T_LEVIERS else ('leviers' if e['leviers'] else 'panne') if t < T_FIN else 'apres'
        ligne = dict(essai=i, nom=e['nom'], t=t, phase=phase, gel_ms=round(garde.seconde(r.T)), config=json.dumps(e, ensure_ascii=False))
        for s in SERVICES:
            x = m[s]
            ligne.update({f'{s}_lat': round(x['lat'], 1), f'{s}_pertes': round(x['pertes'], 2), f'{s}_gigue': round(x['gigue'], 2),
                          f'{s}_debit': round(x['debit'], 3), **{f'{s}_sonde{k}': round(v, 1) for k, v in enumerate(x['sondes'])}})
        for (a, b), ech in m['liens'].items():
            if ech:
                ligne.update({f'occ_{a}_{b}': ech[0], f'file_{a}_{b}': ech[1], f'jetes_{a}_{b}': ech[2]})
        w.writerow(ligne)
    lv.reinitialiser()
    for _ in range(3):
        r.tick()


def main():
    from mininet.log import setLogLevel
    setLogLevel('warning')
    sortie, numeros = 'calib3.csv', list(range(len(ESSAIS)))
    if '--refaire' in sys.argv:
        sortie, numeros = 'calib3_bis.csv', [int(x) for x in sys.argv[sys.argv.index('--refaire') + 1].split(',')]
    elif len(sys.argv) > 1:
        numeros = numeros[:int(sys.argv[1])]
    essais = [ESSAIS[i] for i in numeros]
    print(f'{len(essais)} essais, environ {len(essais) * (T_TOTAL + 4) / 60:.0f} min', flush=True)
    r, garde = Reseau3(), GardeGel()
    try:
        with open(sortie, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=COLONNES)
            w.writeheader()
            for n, (i, e) in enumerate(zip(numeros, essais)):
                print(f'[{time.strftime("%H:%M:%S")}] essai {n + 1}/{len(essais)} : {e["nom"]}', flush=True)
                jouer(r, garde, w, i, e)
                f.flush()
    finally:
        garde.stop.set()
        r.arreter()
    print(f'FIN : {sortie}', flush=True)


if __name__ == '__main__':
    main()
