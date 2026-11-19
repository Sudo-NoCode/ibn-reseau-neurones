import csv
import os
import random
import sys

from detecteur3 import enregistrer, programme_progressif
from env3 import INTENTION_DEFAUT
from reseau3 import Reseau3
from valide3 import EnvReel
from valide_mininet import GardeGel

GEL_MS = 20


def main():
    from mininet.log import setLogLevel
    setLogLevel('warning')
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 6
    premiere = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    duree = int(os.environ.get('DUREE', 600))
    r, garde = Reseau3(), GardeGel()
    os.makedirs('valid', exist_ok=True)
    f, w = open('valid/det3_reel.csv', 'a', newline=''), None
    try:
        for i in range(premiere, premiere + n):
            g = 90_000 + i
            prog = programme_progressif(random.Random(g), duree)
            print(f'session r{g} : ' + ' | '.join(f'{t} s {sit}' for t, sit, _ in prog if sit != 'normal' and (sit.endswith('brusque') or True))[:300], flush=True)
            env = EnvReel(r, duree=duree, randomiser=False, graine=g, intentions=INTENTION_DEFAUT)
            lignes = enregistrer(env, prog, f'r{g}')
            geles = sum(garde.seconde(T) > GEL_MS for T in range(r.T - duree + 1, r.T + 1))
            for l in lignes:
                l['gels_session_pct'] = round(100 * geles / duree, 1)
            if w is None:
                w = csv.DictWriter(f, fieldnames=list(lignes[0]))
                if f.tell() == 0:
                    w.writeheader()
            w.writerows(lignes); f.flush()
            viol = 100 * sum(l['violation'] for l in lignes) / len(lignes)
            print(f'session r{g} finie : {len(lignes)} lignes, {viol:.0f} % de secondes hors contrat, {100 * geles / duree:.1f} % de secondes gelées', flush=True)
    finally:
        garde.stop.set(); f.close()
        r.arreter()
    print('FIN : valid/det3_reel.csv', flush=True)


if __name__ == '__main__':
    main()
