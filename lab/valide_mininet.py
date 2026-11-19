import csv
import os
import statistics
import sys
import threading
import time

import numpy as np
from mininet.log import setLogLevel

from dqn import Reseau, politique_de
from jumeau import (ACTIONS, COUT, COUT_CHANGEMENT, LAT_ABSENTE, MAINTIEN, MODE_MAINTIEN, Jumeau,
                    RegleManuelle, violation)
from mesure import Ping, Visio
from scenarios import Moteur
from topo2 import NETEM_CIBLES, Actions, construire, demarrer, etat_secours

DECALAGE = 0.6
GEL_MS, CONTAMINATION_MAX = 20, 5.0


class GardeGel:

    def __init__(self):
        self.max_par_seconde, self.lock, self.stop = {}, threading.Lock(), threading.Event()
        for c in range(os.cpu_count() or 1):
            threading.Thread(target=self._veiller, args=(c,), daemon=True).start()

    def _veiller(self, cpu):
        os.sched_setaffinity(0, {cpu})
        prev = time.monotonic()
        while not self.stop.is_set():
            time.sleep(0.01)
            now = time.monotonic()
            retard = (now - prev - 0.01) * 1000
            if retard > 1:
                fin = time.time()
                with self.lock:
                    for s in range(int(fin - retard / 1000), int(fin) + 1):
                        self.max_par_seconde[s] = max(self.max_par_seconde.get(s, 0), retard)
            prev = now

    def seconde(self, T):
        with self.lock:
            return self.max_par_seconde.pop(T - 1, 0.0)


class ReseauReel:

    def __init__(self, net, moteur, actions, ping, sonde, visio, duree=600):
        self.net, self.moteur, self.act, self.duree = net, moteur, actions, duree
        self.ping, self.sonde_ping, self.visio = ping, sonde, visio
        self.h2, self.h4 = net.get('h2', 'h4')
        self.flux = None

    def _poser_situation(self, sit, x):
        if self.flux is not None and self.flux.poll() is None:
            self.flux.terminate()
        self.flux = None
        etat_secours()
        if sit in ('degrade', 'secours_degrade'):
            self.moteur._netem(100 * x, 1, 5 * x)
            if sit == 'secours_degrade':
                etat_secours(60 + 60 * x, 4 * x)
        elif sit == 'instable':
            self.moteur._netem(25, 20 * x, 0.2)
        else:
            self.moteur._netem(5)
        fin = next((p[0] for p in self.prog if p[0] > self.t), self.duree) - self.t + 2
        if sit == 'congestion_udp':
            self.flux = self.moteur._flux(self.h2, self.h4, 25 * x, fin)
        elif sit == 'congestion_tcp':
            self.flux = self.moteur._flux(self.h2, self.h4, 0, fin, tcp=True, paralleles=max(1, round(4 * x)))
        self.sit_courante = (sit, x)

    def _situation(self, t):
        cur = self.prog[0]
        for p in self.prog:
            if p[0] <= t:
                cur = p
        return cur[1], cur[2]

    def _mesurer(self, T):
        p, v = self.ping.fenetre(T - 1, T), self.visio.seconde(T)
        recus = p[0] if p else []
        m = dict(lat=statistics.median(recus) if recus else LAT_ABSENTE,
                 pertes=v[2] if v else 100.0, gigue=v[1] if v else 0.0, debit=v[0] if v else 0.0)
        if self.act.deroute:
            s = self.sonde_ping.fenetre(T - 1, T)
            sonde = statistics.median(s[0]) if s and s[0] else LAT_ABSENTE
        elif self.vient_de_revenir and self.sonde:
            self.sonde_ping.fenetre(T - 1, T)
            sonde = self.sonde[-1]
        else:
            self.sonde_ping.fenetre(T - 1, T)
            sonde = m['lat']
        return m, sonde

    def _attendre_tick(self):
        self.T += 1
        time.sleep(max(0, self.T + DECALAGE - time.time()))

    def reset(self, prog):
        self.prog, self.t = prog, 0
        self.act.appliquer(ACTIONS.index('reinitialiser'))
        self.leviers = dict(limite=False, priorite=False, deroute=False)
        self.depuis_action = 30
        self.vient_de_revenir = False
        self.mur0, self.mono0 = time.time(), time.monotonic()
        self._poser_situation(*self._situation(0))
        time.sleep(3)
        self.T = round(time.time())
        self.histo, self.sonde = [], []
        for _ in range(3):
            self._attendre_tick()
            m, s = self._mesurer(self.T)
            self.histo.append(m); self.sonde.append(s)
        return self._obs()

    _obs = Jumeau._obs

    def step(self, a):
        nom = ACTIONS[a]
        voulu = dict(self.leviers)
        if nom == 'limiter_h2':
            voulu['limite'] = True
        elif nom == 'priorite_visio':
            voulu['priorite'] = True
        elif nom == 'deroute_visio':
            voulu['deroute'] = True
        elif nom == 'reinitialiser':
            voulu = dict(limite=False, priorite=False, deroute=False)
        changes = [k for k in voulu if voulu[k] != self.leviers[k]]
        retour = any(self.leviers[k] and not voulu[k] for k in changes)
        if changes and self.depuis_action < MAINTIEN and (MODE_MAINTIEN == 'tout' or retour):
            changes, voulu = [], dict(self.leviers)
        self.vient_de_revenir = self.leviers['deroute'] and not voulu['deroute']
        self.act.set_limite(voulu['limite']); self.act.set_priorite(voulu['priorite']); self.act.set_deroute(voulu['deroute'])
        self.leviers = voulu
        self.depuis_action = 0 if changes else self.depuis_action + 1

        self.t += 1
        if self._situation(self.t) != self.sit_courante:
            self._poser_situation(*self._situation(self.t))
        self._attendre_tick()
        m, s = self._mesurer(self.T)
        self.histo = (self.histo + [m])[-3:]
        self.sonde = (self.sonde + [s])[-3:]
        viol = violation(m)
        r = (-1.0 if viol else 1.0) - sum(COUT[k] for k in COUT if self.leviers[k]) - COUT_CHANGEMENT * len(changes)
        valide = abs((time.time() - self.mur0) - (time.monotonic() - self.mono0)) < 1.0
        return self._obs(), r, self.t >= self.duree, dict(situation=self.sit_courante[0], intensite=self.sit_courante[1],
                                                           violation=viol, leviers=dict(self.leviers), mesure=m,
                                                           sonde=s, changes=len(changes), valide=valide)


def main():
    g_dqn = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    programmes = [int(x) for x in sys.argv[2].split(',')] if len(sys.argv) > 2 else [0, 1]
    net_dqn = Reseau(Jumeau.DIM_OBS, len(ACTIONS), np.random.default_rng(0))
    net_dqn.p = dict(np.load(f'dqn_graine{g_dqn}.npz'))
    politiques = [(f'dqn_g{g_dqn}', politique_de(net_dqn)), ('regle', RegleManuelle())]
    if len(sys.argv) > 3:
        politiques = [p for p in politiques if p[0].split('_')[0] in sys.argv[3].split(',')]
    garde = GardeGel()

    setLogLevel('warning')
    net = construire(); demarrer(net)
    h1, h3 = net.get('h1', 'h3')
    moteur = Moteur(net, graine=0, bw_mbps=20, duree_s=0, combos=False, pics=False, fond=False, netem_cibles=NETEM_CIBLES)
    actions = Actions()
    ping, sonde, visio = Ping(h1), Ping(h1, '10.0.0.4'), Visio(h1, h3, 3600 * 4)
    ping.start(); sonde.start(); visio.start()
    time.sleep(4)
    reel = ReseauReel(net, moteur, actions, ping, sonde, visio, duree=int(os.environ.get('DUREE', 600)))
    os.makedirs('valid', exist_ok=True)
    f = open('valid/valid.csv', 'a', newline='')
    w = csv.DictWriter(f, fieldnames=['politique', 'programme', 't', 'situation', 'intensite', 'action', 'limite',
                                      'priorite', 'deroute', 'lat', 'pertes', 'gigue', 'debit', 'sonde', 'viol', 'r', 'valide',
                                      'gel_ms'])
    if f.tell() == 0:
        w.writeheader()
    try:
        for g in programmes:
            for nom, pol in politiques:
                prog = Jumeau(series={'_': None}, randomiser=False, graine=1000 + g)._programme()
                obs, fin, total, viols, chg, sec, ok = reel.reset(prog), False, 0.0, 0, 0, 0, True
                contamines = 0
                while not fin:
                    a = pol(obs, reel)
                    obs, r, fin, info = reel.step(a)
                    gel = garde.seconde(reel.T)
                    contamines += gel > GEL_MS
                    total += r; viols += info['violation']; chg += info['changes']; sec += info['leviers']['deroute']
                    ok &= info['valide']
                    m = info['mesure']
                    w.writerow(dict(politique=nom, programme=g, t=reel.t, situation=info['situation'],
                                    intensite=round(info['intensite'], 2), action=ACTIONS[a], **info['leviers'],
                                    lat=round(m['lat'], 1), pertes=round(m['pertes'], 2), gigue=round(m['gigue'], 2),
                                    debit=round(m['debit'], 2), sonde=round(info['sonde'], 1), viol=int(info['violation']),
                                    r=round(r, 2), valide=int(info['valide']), gel_ms=round(gel)))
                    if reel.t % 60 == 0:
                        f.flush()
                        print(f'  {nom} programme {g} : {reel.t} s, violation {100 * viols / reel.t:.1f} %', flush=True)
                pc = 100 * contamines / reel.duree
                etat = 'INVALIDE ' if not ok else ('CONTAMINÉ ' if pc > CONTAMINATION_MAX else '')
                print(f'{etat}{nom:<8} programme {g} : récompense {total:6.0f}  violation '
                      f'{100 * viols / reel.duree:5.1f} %  secours {100 * sec / reel.duree:5.1f} %  changements {chg}  '
                      f'secondes contaminées {pc:.1f} %', flush=True)
    finally:
        garde.stop.set()
        f.close()
        if reel.flux is not None and reel.flux.poll() is None:
            reel.flux.terminate()
        actions.appliquer(ACTIONS.index('reinitialiser'))
        moteur.nettoyer(); visio.arreter(); ping.arreter(); sonde.arreter(); net.stop()
    print('écrit : valid/valid.csv')


if __name__ == '__main__':
    main()
