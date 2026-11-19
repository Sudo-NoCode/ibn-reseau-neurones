import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from jumeau3 import DELAI_BASE, SERVICES, Physique

LAB = Path(__file__).resolve().parent.parent / 'lab'
T_PANNE, T_LEVIERS, T_FIN, T_TOTAL = 6, 18, 30, 34
PANNE = {'degrade': lambda x: (1.5 + 100 * x, 1.0, 5 * x), 'instable': lambda x: (25.0, 20 * x, 0.2)}
CONTRAT = {'visio': dict(lat=60, pertes=2, gigue=15, debit=3.6), 'voix': dict(lat=60, pertes=2, gigue=15, debit=0.9)}


def viole(m, s):
    c = CONTRAT[s]
    return m['lat'] > c['lat'] or m['pertes'] > c['pertes'] or m['gigue'] > c['gigue'] or m['debit'] < c['debit']


def charger():
    d = pd.read_csv(LAB / 'calib3.csv')
    bis = LAB / 'calib3_bis.csv'
    if bis.exists():
        b = pd.read_csv(bis)
        d = pd.concat([d[~d.nom.isin(b.nom.unique())], b])
    d['essai'] = d.nom.map({n: i for i, n in enumerate(dict.fromkeys(pd.read_csv(LAB / 'calib3.csv').nom))})
    return d.sort_values(['essai', 't']).reset_index(drop=True)


def rejouer(config, graine=0, alea=1.0):
    ph = Physique(graine=graine, alea=alea)
    lignes = []
    for t in range(T_TOTAL):
        ev = None
        if t == T_PANNE:
            def ev():
                for a, b, typ, x in config['pannes']:
                    ph.panne(a, b, *PANNE[typ](x))
                for h, debit in config['geneurs']:
                    ph.gener(h, tcp=int(debit[3:])) if isinstance(debit, str) else ph.gener(h, mbps=debit)
        elif t == T_LEVIERS:
            def ev():
                for l in config['leviers']:
                    if l[0] == 'priorite':
                        ph.prio[l[1]] = True
                    elif l[0] == 'chemin':
                        ph.chemin[l[1]] = l[2]
                    else:
                        ph.limite[l[1]] = True
        elif t == T_FIN:
            def ev():
                for a, b, _, _ in config['pannes']:
                    ph.panne(a, b)
                for h in ph.geneurs:
                    ph.gener(h)
        m = ph.seconde(ev)
        ligne = dict(t=t)
        for s in SERVICES:
            ligne.update({f'{s}_{k}': m[s][k] for k in ('lat', 'pertes', 'gigue', 'debit')})
            ligne.update({f'{s}_sonde{k}': v for k, v in enumerate(m[s]['sondes'])})
        lignes.append(ligne)
    return pd.DataFrame(lignes)


def phase_de(t, leviers):
    return 'avant' if t < T_PANNE else 'panne' if t < T_LEVIERS or not leviers else 'leviers' if t < T_FIN else 'apres'


def main():
    reel = charger()
    if '-v' in sys.argv:
        i = int(sys.argv[sys.argv.index('-v') + 1])
        r = reel[reel.essai == i].reset_index(drop=True)
        j = rejouer(json.loads(r.config[0]))
        print(r.nom[0])
        print(f'{"t":>3}  {"visio lat réel/jumeau":>24} {"pertes":>14} {"voix lat":>18} {"pertes":>14}   sondes visio réel | jumeau')
        for t in range(T_TOTAL):
            a, b = r.iloc[t], j.iloc[t]
            print(f'{t:>3}  {a.visio_lat:>11.0f} / {b.visio_lat:<10.0f} {a.visio_pertes:>6.1f} / {b.visio_pertes:<5.1f} '
                  f'{a.voix_lat:>8.0f} / {b.voix_lat:<7.0f} {a.voix_pertes:>6.1f} / {b.voix_pertes:<5.1f}   '
                  + ' '.join(f'{a[f"visio_sonde{k}"]:5.0f}' for k in range(4)) + ' | ' + ' '.join(f'{b[f"visio_sonde{k}"]:5.0f}' for k in range(4)))
        return

    print(f'{"essai":<56}{"phase":<9}{"visio latence":>16}{"visio pertes":>15}{"voix latence":>16}{"voix pertes":>14}{"contrat : même verdict":>25}')
    print(f'{"":<56}{"":<9}{"réel / jumeau":>16}{"réel / jumeau":>15}{"réel / jumeau":>16}{"réel / jumeau":>14}{"visio   voix":>25}')
    bilan = {}
    for i, r in reel.groupby('essai'):
        r = r.reset_index(drop=True)
        config = json.loads(r.config[0])
        js = [rejouer(config, graine=g) for g in range(5)]
        ph = [phase_de(t, config['leviers']) for t in range(T_TOTAL)]
        accord = {}
        for s in SERVICES:
            vr = np.array([viole({k: r[f'{s}_{k}'][t] for k in ('lat', 'pertes', 'gigue', 'debit')}, s) for t in range(T_TOTAL)])
            vj = np.mean([[viole({k: j[f'{s}_{k}'][t] for k in ('lat', 'pertes', 'gigue', 'debit')}, s) for t in range(T_TOTAL)] for j in js], 0)
            accord[s] = 100 * np.mean(np.where(vr, vj, 1 - vj))
            bilan.setdefault(r.nom[0][0], {}).setdefault(s, []).append(accord[s])
        for phase in ('panne', 'leviers'):
            ts = [t for t in range(T_TOTAL) if ph[t] == phase][4:]
            if not ts:
                continue
            med = lambda col: (r[col][ts].median(), np.median([j[col][ts].median() for j in js]))
            cols = [med(f'{s}_{k}') for s in SERVICES for k in ('lat', 'pertes')]
            fin = f'{accord["visio"]:>14.0f} % {accord["voix"]:>5.0f} %' if phase == 'panne' else ''
            print(f'{r.nom[0][:55] if phase == "panne" else "":<56}{phase:<9}'
                  f'{cols[0][0]:>8.0f} / {cols[0][1]:<5.0f}{cols[1][0]:>8.1f} / {cols[1][1]:<4.1f}'
                  f'{cols[2][0]:>9.0f} / {cols[2][1]:<5.0f}{cols[3][0]:>7.1f} / {cols[3][1]:<4.1f}{fin}')
    print('\nMême verdict sur le contrat, seconde par seconde (moyenne par famille d\'essais) :')
    for fam, nom in (('A', 'A. un lien abîmé'), ('B', 'B. un lien saturé'), ('C', 'C. cas composés (jamais utilisés pour régler)')):
        if fam in bilan:
            print(f'  {nom:<48} visio {np.mean(bilan[fam]["visio"]):5.1f} %   voix {np.mean(bilan[fam]["voix"]):5.1f} %')


if __name__ == '__main__':
    main()
