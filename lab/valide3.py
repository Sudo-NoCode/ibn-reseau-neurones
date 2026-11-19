import csv
import glob
import os
import sys

import numpy as np

from dqn import Reseau
import dqn
from env3 import ACTIONS, AGENTS, DIM_OBS, GENEUR_DE, INTENTION_DEFAUT, Env3, Regle, masque_obs, obs_pour, politique_rien
from jumeau3 import DELAI_BASE, GENEURS, LIENS, SERVICES
from reseau3 import Reseau3, etat_lien
from valide_mininet import GardeGel

dqn.CACHE = 128
GEL_MS, CONTAMINATION_MAX = 20, 5.0


class Reel:

    def __init__(self, r):
        self.r = r
        self.chemin = {s: 0 for s in SERVICES}
        self.prio = {s: False for s in SERVICES}
        self.limite = {h: False for h in GENEURS}
        self.netem = {l: (DELAI_BASE, 0.0, 0.0) for l in LIENS}
        self.geneurs = {h: None for h in GENEURS}
        self._netem_pose, self._geneurs_poses = dict(self.netem), dict(self.geneurs)
        r.leviers.reinitialiser()
        r.calmer()
        for l in LIENS:
            etat_lien(r.net, *l)

    def panne(self, a, b, delai=DELAI_BASE, gigue=0.0, pertes=0.0):
        self.netem[a, b] = (delai, gigue, pertes)

    def gener(self, hote, mbps=0.0, tcp=0):
        self.geneurs[hote] = ('tcp', tcp) if tcp else ('udp', mbps) if mbps else None

    def _synchroniser(self):
        r, lv = self.r, self.r.leviers
        for s in SERVICES:
            if lv.chemin_de[s] != self.chemin[s]:
                lv.chemin(s, self.chemin[s])
            lv.priorite(s, self.prio[s])
        for h in GENEURS:
            lv.limiter(h, self.limite[h])
        for l in LIENS:
            if self.netem[l] != self._netem_pose[l]:
                etat_lien(r.net, *l, *self.netem[l])
                self._netem_pose[l] = self.netem[l]
        if self.geneurs != self._geneurs_poses:
            r.calmer()
            for h, g in self.geneurs.items():
                if g:
                    r.gener(h, 0 if g[0] == 'tcp' else round(g[1], 2), 400, tcp=g[1] if g[0] == 'tcp' else 0)
            self._geneurs_poses = dict(self.geneurs)

    def seconde(self, evenement=None):
        if evenement:
            evenement()
        self._synchroniser()
        m = self.r.tick()
        m['liens'] = {l: (e or (0.0, 0, 0)) for l, e in m['liens'].items()}
        return m


class EnvReel(Env3):
    def __init__(self, r, **kw):
        super().__init__(**kw)
        self.r = r

    def _nouveau_reseau(self):
        return Reel(self.r)


def agent_ensemble(dossier='.'):
    nets = []
    for f in sorted(glob.glob(os.path.join(dossier, 'agent3_graine*.npz'))):
        n = Reseau(DIM_OBS, len(ACTIONS), np.random.default_rng(0))
        n.p = dict(np.load(f))
        nets.append(n)
    assert nets, 'aucun poids agent3_graine*.npz'

    def pol(obs, env, s):
        q = np.mean([n.avant(obs_pour(n.p['W1'].shape[0], obs)[None])[0] for n in nets], 0)
        return int(np.argmax(np.where(masque_obs(obs), q, -1e9)))
    return pol, len(nets)


def main():
    from mininet.log import setLogLevel
    setLogLevel('warning')
    programmes = [int(x) for x in sys.argv[1].split(',')] if len(sys.argv) > 1 else [0]
    voulues = sys.argv[2].split(',') if len(sys.argv) > 2 else ['agent', 'regle']
    duree = int(os.environ.get('DUREE', 600))
    agent, n = agent_ensemble()
    politiques = {'agent': agent, 'regle': Regle(), 'rien': politique_rien}
    print(f'agent : ensemble de {n} graines ; programmes {programmes} ; politiques {voulues} ; {duree} s par épisode', flush=True)
    r, garde = Reseau3(), GardeGel()
    os.makedirs('valid', exist_ok=True)
    colonnes = (['politique', 'programme', 't', 'situation', 'gel_ms']
                + [f'{s}_{k}' for s in AGENTS for k in ('action', 'chemin', 'prio', 'bride', 'ok', 'r', 'lat', 'pertes', 'gigue', 'debit',
                                                        'sonde0', 'sonde1', 'sonde2', 'sonde3')])
    f = open('valid/valid3.csv', 'a', newline='')
    w = csv.DictWriter(f, fieldnames=colonnes)
    if f.tell() == 0:
        w.writeheader()
    try:
        for g in programmes:
            for nom in voulues:
                pol = politiques[nom]
                env = EnvReel(r, duree=duree, randomiser=False, graine=1000 + g, intentions=INTENTION_DEFAUT)
                obs, fin, total, hors, chg, contamines = env.reset(), False, 0.0, {s: 0 for s in AGENTS}, 0, 0
                if nom == voulues[0]:
                    print(f'programme {g} : ' + ' | '.join(f'{t} s {sit}' for t, sit, _ in env.prog if sit != 'normal'), flush=True)
                while not fin:
                    actions = {s: pol(obs[s], env, s) for s in AGENTS}
                    obs, rec, fin, info = env.step(actions)
                    gel = garde.seconde(r.T)
                    contamines += gel > GEL_MS
                    total += sum(rec.values()) / len(AGENTS); chg += sum(info['changes'].values())
                    ligne = dict(politique=nom, programme=g, t=env.t, situation=info['situation'], gel_ms=round(gel))
                    for s in AGENTS:
                        hors[s] += not info['ok'][s]
                        m = info['mesure'][s]
                        ligne.update({f'{s}_action': ACTIONS[actions[s]] if info['changes'][s] else '', f'{s}_chemin': info['chemin'][s],
                                      f'{s}_prio': int(info['prio'][s]), f'{s}_bride': int(info['limite'][GENEUR_DE[s]]),
                                      f'{s}_ok': int(info['ok'][s]), f'{s}_r': round(rec[s], 2), f'{s}_lat': round(m['lat'], 1),
                                      f'{s}_pertes': round(m['pertes'], 2), f'{s}_gigue': round(m['gigue'], 2), f'{s}_debit': round(m['debit'], 3),
                                      **{f'{s}_sonde{k}': round(v, 1) for k, v in enumerate(m['sondes'])}})
                    w.writerow(ligne)
                    if env.t % 60 == 0:
                        f.flush()
                        print(f'  {nom} programme {g} : {env.t} s, hors contrat visio {100 * hors["visio"] / env.t:.1f} % voix {100 * hors["voix"] / env.t:.1f} %', flush=True)
                pc = 100 * contamines / env.duree
                print(f'{"CONTAMINÉ " if pc > CONTAMINATION_MAX else ""}{nom:<6} programme {g} : récompense {total:5.0f}   hors contrat visio '
                      f'{100 * hors["visio"] / env.duree:4.1f} %  voix {100 * hors["voix"] / env.duree:4.1f} %   changements {chg}   '
                      f'secondes contaminées {pc:.1f} %', flush=True)
    finally:
        garde.stop.set()
        f.close()
        r.arreter()
    print('FIN : valid/valid3.csv', flush=True)


if __name__ == '__main__':
    main()
