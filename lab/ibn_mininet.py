import csv
import os
import re
import statistics
import sys
import time

import numpy as np
from mininet.log import setLogLevel

from agent_intention import autorisees, init_memoire, maj_memoire, obs_intention, seuils_ok
from detection import Detecteur
from ibn import AgentIntention, Journal, accepter
from jumeau import ACTIONS, LAT_ABSENTE, Jumeau, charger
from mesure import Lien, Ping, Visio
from scenarios import Moteur
from topo2 import NETEM_CIBLES, Actions, construire, demarrer
from valide_mininet import GardeGel, ReseauReel

DEMO = [(0, 'normal', 1.0), (30, 'congestion_udp', 1.0), (90, 'normal', 1.0), (130, 'degrade', 1.0), (190, 'normal', 1.0),
        (230, 'congestion_tcp', 1.0), (290, 'normal', 1.0), (330, 'instable', 1.0), (390, 'normal', 1.0)]
DUREE_DEMO = 420


class ReseauIBN(ReseauReel):

    def __init__(self, *a, intention, lien, detecteur, **kw):
        super().__init__(*a, **kw)
        self.seuils, self.interdits = dict(intention.seuils), set(intention.interdits)
        self.lien, self.det = lien, detecteur
        self.bloques, self.det_out, self.feat = 0, None, {}

    def _mesurer(self, T):
        l = self.lien.echantillon()
        p, v = self.ping.fenetre(T - 1, T), self.visio.seconde(T)
        recus = p[0] if p else []
        m = dict(lat=statistics.median(recus) if recus else LAT_ABSENTE,
                 pertes=v[2] if v else 100.0, gigue=v[1] if v else 0.0, debit=v[0] if v else 0.0)
        f = {}
        if p:
            if recus:
                f.update(lat_moy_ms=statistics.fmean(recus), lat_med_ms=m['lat'], lat_max_ms=max(recus))
            f['lat_pertes_pct'] = 100 * (p[1] - len(recus)) / p[1]
        if v:
            f.update(visio_debit_mbps=v[0], visio_gigue_ms=v[1], visio_pertes_pct=v[2])
        if l:
            f.update(lien_occup_pct=l[0], file_paquets=l[1], file_jetes=l[2])
        self.feat, self.det_out = f, self.det.ajouter(f)
        s = self.sonde_ping.fenetre(T - 1, T)
        if self.act.deroute:
            sonde = statistics.median(s[0]) if s and s[0] else LAT_ABSENTE
        elif self.vient_de_revenir and self.sonde:
            sonde = self.sonde[-1]
        else:
            sonde = m['lat']
        return m, sonde

    def _obs(self):
        return obs_intention(self, Jumeau._obs(self))

    def reset(self, prog):
        init_memoire(self)
        return super().reset(prog)

    def step(self, a):
        if not autorisees(self.interdits)[a]:
            self.bloques += 1
            a = 0
        obs, r, fin, info = super().step(a)
        info['violation'] = not seuils_ok(info['mesure'], self.seuils)
        maj_memoire(self, not info['violation'])
        return self._obs(), r, fin, info


def main():
    texte = next((x for x in sys.argv[1:] if not x.startswith('--')), 'visio correcte')
    agent = AgentIntention(dossier='.')
    it, cout = accepter(texte, agent, charger(),
                        confirmer=None if '--oui' in sys.argv else (lambda q: input(q).strip().lower().startswith('o')))
    if it is None:
        print('Intention', cout); return

    setLogLevel('warning')
    garde = GardeGel()
    net = construire(); demarrer(net)
    h1, h3 = net.get('h1', 'h3')
    moteur = Moteur(net, graine=0, bw_mbps=20, duree_s=0, combos=False, pics=False, fond=False, netem_cibles=NETEM_CIBLES)
    actions = Actions()
    ping, sonde, visio = Ping(h1), Ping(h1, '10.0.0.4'), Visio(h1, h3, 3600)
    ping.start(); sonde.start(); visio.start(); time.sleep(4)
    reel = ReseauIBN(net, moteur, actions, ping, sonde, visio, duree=DUREE_DEMO,
                     intention=it, lien=Lien(20), detecteur=Detecteur())
    os.makedirs('valid', exist_ok=True)
    n = 1 + max([int(x) for f in os.listdir('valid') for x in re.findall(r'^ibn_(\d+)\.csv$', f)] or [0])
    f = open(f'valid/ibn_{n}.csv', 'w', newline='')
    w = csv.writer(f)
    w.writerow(['t', 'situation', 'action', 'limite', 'priorite', 'deroute', 'lat', 'pertes', 'gigue', 'debit', 'ok',
                'alerte', 'p_futur', 'classe', 'gel_ms'])
    journal = Journal(it)
    contamines = 0
    print(f'\n--- boucle IBN sur Mininet : {DUREE_DEMO} s de pannes réelles ---')
    try:
        obs, fin = reel.reset(DEMO), False
        while not fin:
            a = agent(obs)
            avant = dict(reel.leviers)
            obs, r, fin, info = reel.step(a)
            gel = garde.seconde(reel.T); contamines += gel > 20
            ok, det, m = not info['violation'], reel.det_out, info['mesure']
            journal.seconde(reel.t, ok, det, avant, info['leviers'], vraie_cause=info['situation'] if info['situation'] != 'normal' else None)
            w.writerow([reel.t, info['situation'], ACTIONS[a], *[int(info['leviers'][k]) for k in ('limite', 'priorite', 'deroute')],
                        round(m['lat'], 1), round(m['pertes'], 2), round(m['gigue'], 2), round(m['debit'], 2), int(ok),
                        int(bool(det and det['alerte'])), round(det['p_futur'], 3) if det else '', det['classe'] if det else '',
                        round(gel)])
    finally:
        garde.stop.set(); f.close()
        if reel.flux is not None and reel.flux.poll() is None:
            reel.flux.terminate()
        actions.appliquer(ACTIONS.index('reinitialiser'))
        moteur.nettoyer(); visio.arreter(); ping.arreter(); sonde.arreter(); net.stop()
    rap = journal.rapport(cout_prevu=cout, bloques=reel.bloques)
    rap += f'\nQualité de mesure : {100 * contamines / DUREE_DEMO:.1f} % de secondes contaminées par un gel de la VM'
    print('\n' + rap)
    open(f'valid/ibn_{n}_rapport.txt', 'w', encoding='utf-8').write(rap + '\n\nJOURNAL\n' + '\n'.join(journal.lignes) + '\n')
    print(f'écrit : valid/ibn_{n}.csv, valid/ibn_{n}_rapport.txt')


if __name__ == '__main__':
    main()
