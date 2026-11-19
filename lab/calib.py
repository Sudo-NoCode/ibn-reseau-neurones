import csv
import random
import statistics
import sys
import time

from mininet.log import setLogLevel

from mesure import Ping, Visio
from scenarios import Moteur
from topo2 import NETEM_CIBLES, Actions, construire, demarrer, etat_secours

SITUATIONS = ['normal', 'congestion_udp', 'congestion_tcp', 'degrade', 'instable', 'secours_degrade']
ACTIONS = ['rien', 'limiter_h2', 'priorite_visio', 'deroute_visio']
AVANT, APRES, INSTALLATION, GRACE = 5, 15, 6, 3
SEUILS = dict(lat=60.0, pertes=2.0, gigue=15.0, debit=3.6)


def violation(m):
    return int(m['lat'] is None or m['lat'] > SEUILS['lat'] or m['vpertes'] is None or m['vpertes'] > SEUILS['pertes']
               or m['vgigue'] > SEUILS['gigue'] or m['vdebit'] < SEUILS['debit'])


def main():
    reps = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 1
    varie = '--varie' in sys.argv
    rng = random.Random(42)
    sortie = 'calib_varie.csv' if varie else 'calib.csv'
    garde = None
    if '--garde' in sys.argv:
        from valide_mininet import GardeGel
        garde, sortie = GardeGel(), sortie.replace('.csv', '_garde.csv')
    setLogLevel('warning')
    net = construire()
    demarrer(net)
    h1, h2, h3, h4 = net.get('h1', 'h2', 'h3', 'h4')
    moteur = Moteur(net, graine=0, bw_mbps=20, duree_s=0, combos=False, pics=False, fond=False, netem_cibles=NETEM_CIBLES)
    actions = Actions()
    ping = Ping(h1)
    ping.start()
    visio = Visio(h1, h3, 3600 * 3)
    visio.start()
    time.sleep(4)
    resume = []
    f = open(sortie, 'w', newline='')
    w = csv.DictWriter(f, fieldnames=['rep', 'situation', 'action', 'intensite', 'valide', 'k',
                                      'lat', 'vpertes', 'vgigue', 'vdebit', 'viol', 'gel_ms'])
    w.writeheader()
    try:
        for rep in range(reps):
            for sit in SITUATIONS:
                for act in ACTIONS:
                    proc = None
                    x = rng.uniform(0.4, 1.5) if varie else 1.0
                    if sit in ('degrade', 'secours_degrade'):
                        moteur._netem(100 * x, 1, 5 * x)
                        if sit == 'secours_degrade':
                            etat_secours(60 + 60 * x, 4 * x)
                    elif sit == 'instable':
                        moteur._netem(25, 20 * x, 0.2)
                    else:
                        moteur._netem(5)
                    duree_cong = INSTALLATION + AVANT + APRES + 5
                    if sit == 'congestion_udp':
                        proc = moteur._flux(h2, h4, 25 * x, duree_cong)
                    elif sit == 'congestion_tcp':
                        proc = moteur._flux(h2, h4, 0, duree_cong, tcp=True, paralleles=max(1, round(4 * x)))
                    mur0, mono0 = time.time(), time.monotonic()
                    time.sleep(INSTALLATION)
                    t_act = round(time.time() - 0.5) + AVANT
                    time.sleep(max(0, t_act - time.time()))
                    actions.appliquer(ACTIONS.index(act))
                    time.sleep(max(0, t_act + APRES + GRACE - time.time()))
                    valide = int(abs((time.time() - mur0) - (time.monotonic() - mono0)) < 1.0)
                    serie = []
                    for k in range(-AVANT, APRES):
                        debut = t_act + k
                        p, v = ping.fenetre(debut, debut + 1), visio.seconde(debut + 1)
                        recus = p[0] if p else []
                        m = dict(lat=statistics.median(recus) if recus else None,
                                 vpertes=v[2] if v else None, vgigue=v[1] if v else 0.0, vdebit=v[0] if v else 0.0)
                        m['viol'] = violation(m)
                        m['gel_ms'] = round(garde.max_par_seconde.get(debut, 0.0)) if garde else ''
                        serie.append(m)
                        w.writerow(dict(rep=rep, situation=sit, action=act, intensite=round(x, 2), valide=valide, k=k, **m))
                    f.flush()
                    avant = [s['viol'] for s in serie[:AVANT]]
                    apres = [s['viol'] for s in serie[AVANT + 5:]]
                    delai = next((k for k in range(APRES - 2) if not any(s['viol'] for s in serie[AVANT + k:AVANT + k + 3])), None)
                    lat_apres = [s['lat'] for s in serie[AVANT + 5:] if s['lat'] is not None]
                    resume.append((sit, act, 100 * sum(avant) / AVANT, 100 * sum(apres) / len(apres),
                                   delai, statistics.median(lat_apres) if lat_apres else None))
                    print(f'{"" if valide else "INVALIDE "}r{rep} x={x:.2f} {sit:<15}{act:<16} violation avant {resume[-1][2]:5.0f} %   après {resume[-1][3]:5.0f} %   '
                          f'réparé en {"-" if delai is None else f"{delai} s":>5}   latence après '
                          f'{"-" if resume[-1][5] is None else f"{resume[-1][5]:.0f} ms"}', flush=True)
                    if proc is not None and proc.poll() is None:
                        proc.terminate()
                    actions.appliquer(Actions.NOMS.index('reinitialiser'))
                    moteur._netem(5)
                    etat_secours()
                    time.sleep(3)
    finally:
        if garde:
            garde.stop.set()
        f.close()
        moteur.nettoyer()
        visio.arreter()
        ping.arreter()
        net.stop()
    print(f'\nécrit : {sortie}')


if __name__ == '__main__':
    main()
