import csv
import statistics
import time

from mininet.log import setLogLevel

from detection import Detecteur
from mesure import Lien, Ping, Visio
from scenarios import Moteur
from topo2 import NETEM_CIBLES, construire, demarrer, etat_secours

PROGRAMME = [('normal', 40), ('congestion_udp', 60), ('normal', 40), ('congestion_tcp', 60), ('normal', 40),
             ('degrade', 60), ('normal', 40), ('instable', 60), ('normal', 40)]
CLASSE = dict(normal='normal', congestion_udp='congestion', congestion_tcp='congestion', degrade='lien', instable='instable')
SEUILS = dict(lat=60.0, pertes=2.0, gigue=15.0, debit=3.6)


def main():
    setLogLevel('warning')
    net = construire(); demarrer(net)
    h1, h2, h3, h4 = net.get('h1', 'h2', 'h3', 'h4')
    moteur = Moteur(net, graine=0, bw_mbps=20, duree_s=0, combos=False, pics=False, fond=False, netem_cibles=NETEM_CIBLES)
    ping, visio, lien = Ping(h1), Visio(h1, h3, 3600), Lien(20)
    ping.start(); visio.start(); time.sleep(5)
    det = Detecteur()
    lignes = []
    flux = None
    try:
        T = round(time.time()) + 1
        lien.echantillon()
        for sit, duree in PROGRAMME:
            if flux is not None and flux.poll() is None:
                flux.terminate()
            flux = None
            etat_secours()
            if sit == 'degrade':
                moteur._netem(100, 1, 5)
            elif sit == 'instable':
                moteur._netem(25, 20, 0.2)
            else:
                moteur._netem(5)
            if sit == 'congestion_udp':
                flux = moteur._flux(h2, h4, 25, duree + 2)
            elif sit == 'congestion_tcp':
                flux = moteur._flux(h2, h4, 0, duree + 2, tcp=True, paralleles=4)
            for k in range(duree):
                time.sleep(max(0, T - time.time()))
                l = lien.echantillon()
                time.sleep(max(0, T + 0.6 - time.time()))
                p, v = ping.fenetre(T - 1, T), visio.seconde(T)
                m = {}
                if p:
                    recus, envoyes = p
                    if recus:
                        m.update(lat_moy_ms=statistics.fmean(recus), lat_med_ms=statistics.median(recus), lat_max_ms=max(recus))
                    m['lat_pertes_pct'] = 100 * (envoyes - len(recus)) / envoyes
                if v:
                    m.update(visio_debit_mbps=v[0], visio_gigue_ms=v[1], visio_pertes_pct=v[2])
                if l:
                    m.update(lien_occup_pct=l[0], file_paquets=l[1], file_jetes=l[2])
                out = det.ajouter(m)
                viol = int(m.get('lat_med_ms', 1e9) > SEUILS['lat'] or m.get('visio_pertes_pct', 100) > SEUILS['pertes']
                           or m.get('visio_gigue_ms', 0) > SEUILS['gigue'] or m.get('visio_debit_mbps', 0) < SEUILS['debit'])
                lignes.append(dict(T=T, situation=sit, k=k, vraie_classe=CLASSE[sit], viol=viol,
                                   **{c: round(x, 2) for c, x in m.items()},
                                   **({} if out is None else dict(p_futur=round(out['p_futur'], 3), alerte=int(out['alerte']),
                                                                   classe=out['classe'],
                                                                   **{f'p_{c}': round(x, 3) for c, x in out['p_classes'].items()}))))
                if out and k % 10 == 9:
                    print(f'{sit:<15} k={k:2d}  viol={viol}  alerte={int(out["alerte"])}  p_futur={out["p_futur"]:.2f}  '
                          f'classe={out["classe"]}', flush=True)
                T += 1
    finally:
        if flux is not None and flux.poll() is None:
            flux.terminate()
        moteur.nettoyer(); visio.arreter(); ping.arreter(); net.stop()
        cols = sorted({c for l in lignes for c in l}, key=lambda c: list(lignes[-1]).index(c) if c in lignes[-1] else 99)
        with open('valid/detection_live.csv', 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=cols); w.writeheader(); w.writerows(lignes)
        print('écrit : valid/detection_live.csv')


if __name__ == '__main__':
    main()
