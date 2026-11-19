import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'phase2'))
import dqn
from dqn import BATCH, CIBLE_TOUS, DEBUT, EPS_DEBUT, EPS_DUREE, EPS_FIN, GAMMA, MEMOIRE, VALID_TOUS, Memoire, Reseau
from jumeau import ACTIONS, LAT_ABSENTE, R_OK, R_VIOL, Jumeau, charger, violation

from intention import LAT_SECOURS, traduire

dqn.CACHE = 128
LEVIER_DE = {'limiter_h2': 'limite', 'priorite_visio': 'priorite', 'deroute_visio': 'deroute'}
ORDRE = ('limite', 'priorite', 'deroute')
NORME = dict(lat=100, pertes=5, gigue=30, debit=4)
MEMOIRE_OBS = 2
SANS_SECOURS_MAX, MAUVAIS_MAX = 60, 30

INTENTIONS_TEST = [
    'visio correcte',
    'visio excellente sans utiliser le lien de secours',
    "latence < 80 ms, pertes au plus 1 %, débit d'au moins 3.8 Mb/s",
    'réunion fluide, gigue max 12 ms, ne pas brider h2',
    'la visio doit avoir une latence sous 25 ms',
    'visio acceptable, jamais de priorité',
]


def seuils_ok(m, s):
    return not (m['lat'] > s['lat'] or m['pertes'] > s['pertes'] or m['gigue'] > s['gigue'] or m['debit'] < s['debit'])


class JumeauIntention(Jumeau):
    DIM_OBS = Jumeau.DIM_OBS + 4 + 3 * 4 + MEMOIRE_OBS + 3

    def __init__(self, intention=None, **kw):
        super().__init__(**kw)
        self.fixe = intention

    def _tirer_intention(self):
        r = self.rng
        s = dict(lat=r.choice([r.uniform(15, 32), r.uniform(32, 100), r.uniform(32, 100)]),
                 pertes=r.uniform(0.3, 4), gigue=r.uniform(5, 30), debit=r.uniform(3.0, 3.9))
        interdits = {k for k in ORDRE if r.random() < 0.2}
        if s['lat'] < LAT_SECOURS:
            interdits.add('deroute')
        if set(ORDRE) <= interdits:
            interdits.discard(r.choice(['limite', 'priorite']))
        return s, interdits

    def reset(self):
        if self.fixe is None:
            self.seuils, self.interdits = self._tirer_intention()
        else:
            self.seuils, self.interdits = dict(self.fixe.seuils), set(self.fixe.interdits)
        init_memoire(self)
        return super().reset()

    def autorisees(self):
        return np.array([ACTIONS[a] not in LEVIER_DE or LEVIER_DE[ACTIONS[a]] not in self.interdits
                         for a in range(len(ACTIONS))])

    def _obs(self):
        return obs_intention(self, super()._obs())

    def step(self, a):
        if not self.autorisees()[a]:
            a = 0
        obs, r, fin, info = super().step(a)
        m = self.histo[-1]
        ok = seuils_ok(m, self.seuils)
        r += (R_OK if ok else R_VIOL) - (R_VIOL if info['violation'] else R_OK)
        info['violation'] = not ok
        maj_memoire(self, ok)
        return self._obs(), r, fin, info


def init_memoire(env):
    env.sans_secours, env.mauvais = SANS_SECOURS_MAX, 0


def maj_memoire(env, ok):
    env.sans_secours = 0 if env.leviers['deroute'] else min(env.sans_secours + 1, SANS_SECOURS_MAX)
    env.mauvais = 0 if ok else min(env.mauvais + 1, MAUVAIS_MAX)


def obs_intention(env, base):
    v = list(base)
    s = env.seuils
    v += [s[k] / NORME[k] for k in ('lat', 'pertes', 'gigue', 'debit')]
    for m in env.histo:
        v += [min(m['lat'], LAT_ABSENTE) / s['lat'] / 3, min(m['pertes'] / s['pertes'], 10) / 3,
              min(m['gigue'] / s['gigue'], 10) / 3, m['debit'] / s['debit']]
    v += [env.sans_secours / SANS_SECOURS_MAX, env.mauvais / MAUVAIS_MAX]
    v += [float(k not in env.interdits) for k in ORDRE]
    return np.array(v, dtype=np.float32)


def q_de(net, obs):
    if net.p['W1'].shape[0] == obs.shape[-1] - MEMOIRE_OBS:
        obs = np.delete(obs, [-5, -4], axis=-1)
    return net.avant(obs)


def autorisees(interdits):
    return np.array([ACTIONS[a] not in LEVIER_DE or LEVIER_DE[ACTIONS[a]] not in interdits for a in range(len(ACTIONS))])


def masque_obs(obs):
    lim, pri, der = obs[..., -3] > 0.5, obs[..., -2] > 0.5, obs[..., -1] > 0.5
    un = np.ones_like(lim)
    return np.stack([un, lim, pri, der, un], -1)


def politique_intention(net):
    def pol(obs, env):
        q = q_de(net, obs[None])[0]
        return int(np.argmax(np.where(masque_obs(obs), q, -1e9)))
    return pol


class RegleIntention:

    def __call__(self, obs, env):
        if env.t == 0 or not hasattr(self, 'mauvais'):
            self.mauvais = self.bon = 0
        if seuils_ok(env.histo[-1], env.seuils):
            self.bon += 1; self.mauvais = 0
        else:
            self.mauvais += 1; self.bon = 0
        lv = env.leviers
        if self.mauvais >= 3 and env.depuis_action >= 3:
            for lev, act in (('priorite', 'priorite_visio'), ('deroute', 'deroute_visio'), ('limite', 'limiter_h2')):
                if not lv[lev] and lev not in env.interdits:
                    return ACTIONS.index(act)
        if self.bon >= 20 and any(lv.values()):
            return ACTIONS.index('reinitialiser')
        return 0


def ancien_agent():
    net = Reseau(Jumeau.DIM_OBS, len(ACTIONS), np.random.default_rng(0))
    net.p = dict(np.load(Path(__file__).resolve().parent.parent / 'phase2' / 'dqn_graine0.npz'))
    return lambda obs, env: int(np.argmax(net.avant(obs[None, :Jumeau.DIM_OBS])[0]))


def evaluer_intention(politique, intention, series, graines=range(5), randomiser=False):
    res = []
    for g in graines:
        env = JumeauIntention(intention=intention, series=series, randomiser=randomiser, graine=1000 + g)
        obs, fin, total, viols, interdit_utilise = env.reset(), False, 0.0, 0, 0
        while not fin:
            a = politique(obs, env)
            interdit_utilise += not env.autorisees()[a]
            obs, r, fin, info = env.step(a)
            total += r; viols += info['violation']
        res.append((total, 100 * viols / env.duree, interdit_utilise))
    return np.array(res).mean(0)


def entrainer(graine, etapes, series, valid):
    rng = np.random.default_rng(graine)
    env = JumeauIntention(series=series, randomiser=True, graine=graine)
    D = JumeauIntention.DIM_OBS
    net, cible = Reseau(D, len(ACTIONS), rng), Reseau(D, len(ACTIONS), rng)
    cible.copie_de(net)
    mem = Memoire(MEMOIRE, D)
    s = env.reset()
    meilleur, meilleurs_poids, courbe = -1e9, None, []
    pol = politique_intention(net)
    for t in range(1, etapes + 1):
        eps = max(EPS_FIN, EPS_DEBUT - (EPS_DEBUT - EPS_FIN) * t / EPS_DUREE)
        if rng.random() < eps:
            a = int(rng.choice(np.flatnonzero(env.autorisees())))
        else:
            a = pol(s, env)
        s2, r, fin, _ = env.step(a)
        mem.ajouter(s, a, r, s2, fin)
        s = env.reset() if fin else s2
        if t >= DEBUT:
            bs, ba, br, bs2, bfin = mem.tirer(BATCH, rng)
            q2 = np.where(masque_obs(bs2), net.avant(bs2), -1e9)
            a_suiv = np.argmax(q2, 1)
            q_suiv = cible.avant(bs2)[np.arange(BATCH), a_suiv]
            net.apprendre(bs, ba, br + GAMMA * (1 - bfin) * q_suiv)
        if t % CIBLE_TOUS == 0:
            cible.copie_de(net)
        if t % VALID_TOUS == 0:
            rv = np.mean([evaluer_intention(pol, it, series, graines=range(500, 502), randomiser=True)[0] for it in valid])
            courbe.append(rv)
            if rv > meilleur:
                meilleur, meilleurs_poids = rv, {k: v.copy() for k, v in net.p.items()}
    net.p = meilleurs_poids
    return net, courbe


def main():
    series = charger()
    intentions = [traduire(p) for p in INTENTIONS_TEST]
    for it in intentions:
        assert it.acceptee, it.resume()
    if sys.argv[1] == 'entrainer':
        g = int(sys.argv[2])
        etapes = int(sys.argv[3]) if len(sys.argv) > 3 else 300_000
        t0 = time.time()
        net, courbe = entrainer(g, etapes, series, intentions)
        np.savez(f'agent_intention_graine{g}.npz', **net.p)
        print(f'graine {g} [{time.time() - t0:.0f} s]  validation : ' + ' '.join(f'{x:.0f}' for x in courbe), flush=True)
        return
    n_graines = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    agents = []
    for g in range(n_graines):
        net = Reseau(JumeauIntention.DIM_OBS, len(ACTIONS), np.random.default_rng(0))
        net.p = dict(np.load(f'agent_intention_graine{g}.npz'))
        agents.append(politique_intention(net))

    print("\n=== Test : 5 épisodes de 10 min par intention (récompense / % violation du contrat de l'intention) ===")
    concurrents = [('ancien agent (phase 2)', [ancien_agent()]), ('règle manuelle', [RegleIntention()]),
                   (f'agent intention ({n_graines} gr.)', agents)]
    print(f'{"intention":<62}' + ''.join(f'{n:>26}' for n, _ in concurrents))
    totaux = {n: [] for n, _ in concurrents}
    for p, it in zip(INTENTIONS_TEST, intentions):
        ligne = f'{p[:60]:<62}'
        for n, pols in concurrents:
            r = np.array([evaluer_intention(pol, it, series) for pol in pols])
            totaux[n].append(r.mean(0))
            interdit = f' ⚠{r[:, 2].mean():.0f}' if r[:, 2].mean() > 0 else ''
            ligne += f'{r[:, 0].mean():>14.0f} / {r[:, 1].mean():5.1f} %{interdit:>4}'
        print(ligne)
    ligne = f'{"MOYENNE":<62}'
    for n, _ in concurrents:
        m = np.array(totaux[n]).mean(0)
        ligne += f'{m[0]:>14.0f} / {m[1]:5.1f} %    '
    print(ligne)
    print('⚠n = nombre de fois où la politique a VOULU un levier interdit (bloqué par le garde-fou)')


if __name__ == '__main__':
    main()
