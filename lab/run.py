import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from subprocess import DEVNULL

from mininet.log import setLogLevel

import pandas as pd

from mesure import SautHorloge, collecter
from scenarios import Moteur
from topo import construire

ICI = os.path.dirname(os.path.abspath(__file__))
SORTIE = os.path.join(ICI, 'data', 'raw')
MANIFESTE = os.path.join(SORTIE, 'manifest.json')

PLANS = {
    'complet': [(f's{i:02d}', 1000 + i, 20, 600, 'principal') for i in range(1, 21)]
    + [('g10a', 2001, 10, 600, 'generalisation'), ('g10b', 2002, 10, 600, 'generalisation'),
       ('g50a', 2003, 50, 600, 'generalisation'), ('g50b', 2004, 50, 600, 'generalisation')],
    'court': [('t20', 901, 20, 60, 'test'), ('t10', 902, 10, 60, 'test'), ('t50', 903, 50, 60, 'test')],
    'verif': [('v20', 950, 20, 600, 'test')],
    'progressif': [(f'p{i:02d}', 3000 + i, 20, 600, 'progressif') for i in range(1, 29)],
    'progressif_court': [('pt1', 3901, 20, 90, 'test')],
}
GEL_MS, CONTAMINATION_MAX = 20, 5.0


MAX_VIDES_PCT = 5
ESSAIS = 3


class SessionRejetee(Exception):
    pass


def journal(msg):
    print(f'[{datetime.now():%H:%M:%S}] {msg}', flush=True)


def empreinte_scripts():
    h = hashlib.sha256()
    for nom in ('topo.py', 'topo2.py', 'scenarios.py', 'mesure.py', 'run.py'):
        with open(os.path.join(ICI, nom), 'rb') as f:
            h.update(f.read())
    return h.hexdigest()[:12]


def lire_manifeste():
    if os.path.exists(MANIFESTE):
        with open(MANIFESTE) as f:
            return json.load(f)
    return {}


def ecrire_manifeste(m):
    tmp = MANIFESTE + '.part'
    with open(tmp, 'w') as f:
        json.dump(m, f, indent=2, ensure_ascii=False)
    os.replace(tmp, MANIFESTE)


def une_session(sid, graine, bw, duree, progressif=False, garde=None):
    final = os.path.join(SORTIE, f'{sid}.csv')
    partiel = final + '.part'
    subprocess.run(['mn', '-c'], stdout=DEVNULL, stderr=DEVNULL)
    if progressif:
        import topo2
        net = topo2.construire(bw=bw)
        topo2.demarrer(net)
        moteur_kw = dict(netem_cibles=topo2.NETEM_CIBLES, progressif=True)
    else:
        net = construire(bw=bw)
        net.start()
        moteur_kw = {}
    t_debut = int(time.time())
    try:
        moteur = Moteur(net, graine=graine, bw_mbps=bw, duree_s=duree + 30, **moteur_kw)
        debut = time.time()
        lignes, relances = collecter(net, moteur, duree, partiel, sid, graine, bw, journal=lambda m: journal(f'{sid} {m}'))
    finally:
        subprocess.run(['pkill', '-x', 'iperf3'], stdout=DEVNULL, stderr=DEVNULL)
        net.stop()
    duree_reelle = round(time.time() - debut)
    df = pd.read_csv(partiel)
    vides = 100 * max(df.visio_debit_mbps.isna().mean(), df.lat_pertes_pct.isna().mean())
    gels = 0.0
    if garde is not None:
        with garde.lock:
            gels = 100 * sum(1 for s, g in garde.max_par_seconde.items() if s >= t_debut and g > GEL_MS) / max(1, duree_reelle)
            garde.max_par_seconde.clear()
    if lignes != duree or vides > MAX_VIDES_PCT or duree_reelle > duree + 120 or gels > CONTAMINATION_MAX:
        raise SessionRejetee(f'{lignes} lignes, {vides:.0f} % de mesures vides, {duree_reelle} s réelles, '
                             f'{gels:.1f} % de secondes gelées')
    os.replace(partiel, final)
    return dict(lignes=lignes, relances_visio=relances, duree_reelle_s=duree_reelle, vides_pct=round(vides, 1),
                gels_pct=round(gels, 1))


def main():
    setLogLevel('warning')
    nom_plan = sys.argv[sys.argv.index('--plan') + 1] if '--plan' in sys.argv else 'complet'
    plan = PLANS[nom_plan]
    progressif = nom_plan.startswith('progressif')
    garde = None
    if progressif:
        from valide_mininet import GardeGel
        garde = GardeGel()
    os.makedirs(SORTIE, exist_ok=True)
    manifeste = lire_manifeste()
    empreinte = empreinte_scripts()
    journal(f'{len(plan)} sessions prévues, empreinte des scripts {empreinte}')

    for sid, graine, bw, duree, groupe in plan:
        if os.path.exists(os.path.join(SORTIE, f'{sid}.csv')):
            journal(f'{sid} déjà faite, sautée')
            continue
        for essai in range(1, ESSAIS + 1):
            journal(f'{sid} démarre (essai {essai}/{ESSAIS}) : graine={graine} goulot={bw} Mbps durée={duree} s')
            try:
                res = une_session(sid, graine, bw, duree, progressif, garde)
            except (SautHorloge, SessionRejetee) as err:
                journal(f'{sid} REJETÉE : {err}')
                partiel = os.path.join(SORTIE, f'{sid}.csv.part')
                if os.path.exists(partiel):
                    os.remove(partiel)
                continue
            manifeste[sid] = dict(graine=graine, bw_mbps=bw, duree_s=duree, groupe=groupe, essai=essai,
                                  empreinte_scripts=empreinte, fin=datetime.now().isoformat(timespec='seconds'), **res)
            ecrire_manifeste(manifeste)
            journal(f'{sid} terminée : {res}')
            break
        else:
            journal(f'{sid} ABANDONNÉE après {ESSAIS} essais')
    journal('plan terminé')


if __name__ == '__main__':
    main()
