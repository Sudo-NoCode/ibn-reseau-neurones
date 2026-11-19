import random
import sys
from collections import deque
from pathlib import Path

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'phase3'))
from detection import T_C, W, _entrainer

from env3 import AGENTS, INTENTION_DEFAUT, LIENS, PROBLEMES, Env3
from jumeau3 import GENEURS, SERVICES, chemins_de

ICI = Path(__file__).resolve().parent
POIDS = ICI / 'detecteur3.npz'
ENTREES = ['lat', 'pertes', 'gigue', 'debit', 'sonde0', 'sonde1', 'sonde2', 'sonde3', 'occ0', 'occ1', 'occ2', 'file0', 'file1', 'file2']
ECHELLE = np.array([100, 5, 30, 1, 100, 100, 100, 100, 100, 100, 100, 1000, 1000, 1000], float)
PLAFOND = np.array([10, 4, 2, 2, 10, 10, 10, 10, 1.2, 1.2, 1.2, 1.2, 1.2, 1.2], float)
CLASSES = ['congestion', 'instable', 'lien', 'normal']


def programme_progressif(rng, duree=600, part_progressive=0.85):
    prog, t, reste = [(0, 'normal', [])], rng.randint(30, 90), []
    while t < duree:
        if not reste:
            reste = PROBLEMES[:]; rng.shuffle(reste)
        sit = reste.pop()
        x = lambda: rng.uniform(0.5, 1.5)
        if sit in ('degrade', 'instable'):
            cible = [(rng.choice(LIENS), sit, x())]
        elif sit == 'double_panne':
            cible = [(l, rng.choice(['degrade', 'instable']), x()) for l in rng.sample(LIENS, 2)]
        elif sit == 'congestion_udp':
            cible = [(rng.choice(list(GENEURS)), 'udp', rng.uniform(18, 35))]
        elif sit == 'congestion_tcp':
            cible = [(rng.choice(list(GENEURS)), 'tcp', 4)]
        else:
            cible = [(h, 'udp', rng.uniform(13, 25)) for h in GENEURS]
        progressive = rng.random() < part_progressive and sit != 'congestion_tcp'
        if progressive:
            rampe = rng.randint(10, 40)
            n = 4 if 'congestion' in sit else rampe // 2
            for i in range(1, n):
                f = i / n
                detail = [(c, typ, (6 + (v - 6) * f) if typ == 'udp' else v * f) for c, typ, v in cible]
                prog.append((t + round((i - 1) * rampe / n), f'{sit}|progressive', detail))
            prog.append((t + round((n - 1) * rampe / n), f'{sit}|progressive', cible))
        else:
            prog.append((t, f'{sit}|brusque', cible))
        t += rng.randint(45, 100)
        prog.append((t, 'normal', []))
        t += rng.randint(30, 90)
    return [p for p in prog if p[0] < duree]


def classe_de(service, chemin, detail):
    liens = chemins_de(service)[chemin]
    types = {typ for cible, typ, _ in detail if typ in ('udp', 'tcp') or cible in liens}
    if types & {'udp', 'tcp'}:
        return 'congestion'
    return 'lien' if 'degrade' in types else 'instable' if 'instable' in types else 'normal'


def enregistrer(env, prog, session, politique=None):
    obs = env.reset(); env.prog = prog
    lignes, fin = [], False
    while not fin:
        obs, _, fin, info = env.step({s: politique(obs[s], env, s) if politique else 0 for s in AGENTS})
        _, sit, detail = env.situation(env.t)
        for s in AGENTS:
            m, liens = info['mesure'][s], chemins_de(s)[env.ph.chemin[s]]
            lignes.append(dict(session=session, service=s, t=env.t, lat=round(m['lat'], 2), pertes=round(m['pertes'], 3),
                               gigue=round(m['gigue'], 3), debit=round(m['debit'] / SERVICES[s]['mbps'], 4),
                               **{f'sonde{k}': round(v, 2) for k, v in enumerate(m['sondes'])},
                               **{f'occ{k}': env.liens[l][0] for k, l in enumerate(liens)}, **{f'file{k}': env.liens[l][1] for k, l in enumerate(liens)},
                               violation=int(not info['ok'][s]), situation=sit.split('|')[0],
                               transition=sit.split('|')[1] if '|' in sit else '', classe=classe_de(s, env.ph.chemin[s], detail)))
    return lignes


def sessions_jumeau(n, graine0, agent=None):
    lignes = []
    for i in range(n):
        g = graine0 + i
        env = Env3(randomiser=True, graine=g, intentions=INTENTION_DEFAUT)
        lignes += enregistrer(env, programme_progressif(random.Random(g)), f'j{g}', agent if i % 2 else None)
    return etiqueter(pd.DataFrame(lignes))


def etiqueter(d):
    d = d.sort_values(['session', 'service', 't']).reset_index(drop=True)
    g = d.groupby(['session', 'service']).violation
    futur = sum(g.shift(-k) for k in range(1, 11))
    d['viol_futur_10'] = (futur > 0).astype(float).where(futur.notna())
    return d


def echelle(M):
    return np.minimum(np.nan_to_num(np.asarray(M, float), nan=10.0) / ECHELLE, PLAFOND)


def fenetres(d):
    Xs, ys, inf = [], [], []
    for _, s in d.groupby(['session', 'service'], sort=False):
        M = echelle(s[ENTREES].to_numpy())
        X = sliding_window_view(M, (W, M.shape[1]))[:, 0].reshape(len(M) - W + 1, -1)
        fin = s.iloc[W - 1:]
        g = fin.viol_futur_10.notna().to_numpy()
        Xs.append(X[g]); ys.append(fin.viol_futur_10.to_numpy()[g]); inf.append(fin[g])
    return np.vstack(Xs), np.concatenate(ys), pd.concat(inf)


def resumes(d):
    Xs, ys = [], []
    for _, s in d.groupby(['session', 'service'], sort=False):
        F = sliding_window_view(echelle(s[ENTREES].to_numpy()), (W, len(ENTREES)))[:, 0]
        pente = (F * T_C[None, :, None]).sum(1) / (T_C ** 2).sum()
        Xs.append(np.hstack([F.mean(1), F.std(1), pente, F.max(1)]))
        ys.append(s.classe.map(CLASSES.index).to_numpy()[W - 1:])
    return np.vstack(Xs), np.concatenate(ys)


def _mlp(X, W1, b1, W2, b2):
    return np.tanh(X @ W1 + b1) @ W2 + b2


REEL_TRAIN = [f'r{90000 + i}' for i in range(10)]
REEL_TEST = [f'r{90000 + i}' for i in range(10, 14)]


def entrainer(n_train=90, n_val=14, n_test=20, reel=None, poids=None):
    from ibn3 import AgentEnsemble
    agent = AgentEnsemble()
    poids = Path(poids or POIDS)
    print(f'sessions dans le jumeau (une sur deux avec agent) : {n_train} pour apprendre, {n_val} pour valider, {n_test} pour tester…', flush=True)
    tr, va, te = sessions_jumeau(n_train, 10_000, agent), sessions_jumeau(n_val, 20_000, agent), sessions_jumeau(n_test, 30_000, agent)
    te.to_csv(ICI / 'det3_jumeau_test.csv', index=False)
    if reel:
        r = etiqueter(pd.read_csv(reel))
        r = r[r.session.isin(REEL_TRAIN)]
        copies = [r.assign(session=r.session + f'_{k}') for k in range(3)]
        print(f'+ {r.session.nunique()} vraies sessions (×3) ajoutées pour apprendre', flush=True)
        tr = pd.concat([tr] + copies, ignore_index=True)
    p = {}
    (Xt, yt, _), (Xv, yv, _) = fenetres(tr), fenetres(va)
    print(f'1b : {len(yt)} exemples, {100 * yt.mean():.0f} % de « oui »…', flush=True)
    p['b_W1'], p['b_b1'], p['b_W2'], p['b_b2'] = _entrainer(Xt, yt, Xv, yv, 1, 3000, 0.0)
    (Xt, yt), (Xv, yv) = resumes(tr), resumes(va)
    moy, ec = Xt.mean(0), Xt.std(0) + 1e-3
    print(f'1a : {len(yt)} exemples…', flush=True)
    p['a_W1'], p['a_b1'], p['a_W2'], p['a_b2'] = _entrainer((Xt - moy) / ec, yt, (Xv - moy) / ec, yv, len(CLASSES), 1500, 1e-3)
    p['a_moy'], p['a_ec'] = moy, ec
    np.savez(poids, **p)
    print(f'poids : {poids.name}')
    evaluer(te, 'JUMEAU (sessions jamais vues)', poids)


def lisser(inf, pred, k=3):
    cle = (inf.session + inf.service).to_numpy()
    return pd.Series(pred).groupby(cle).transform(lambda x: x.rolling(k, min_periods=k).min().fillna(0)).to_numpy().astype(int)


def evaluer(d, titre, poids=None):
    p = dict(np.load(poids or POIDS))
    d = etiqueter(d) if 'viol_futur_10' not in d else d
    X, y, inf = fenetres(d)
    oui = (1 / (1 + np.exp(-_mlp(X, p['b_W1'], p['b_b1'], p['b_W2'], p['b_b2'])[:, 0])) > 0.5).astype(int)
    inf = inf.assign(y=y, alerte=lisser(inf, oui), regle=inf.violation.to_numpy())
    print(f'\n================ {titre} : {d.session.nunique()} sessions, {len(d)} lignes ================')
    print('1b — « le contrat sera-t-il perdu dans 10 s ? »')
    print(f'{"":<24}{"précision":>10}{"fausses alertes":>17}{"secondes d\'avant-panne vues":>29}{"pannes vues venir":>19}{"avance":>9}')
    inf['ou'] = np.maximum(inf.alerte, inf.regle)
    for nom, col in (('MLP 10 s + 3 oui', 'alerte'), ('règle « déjà violé »', 'regle'), ('MLP OU déjà violé', 'ou')):
        pr = inf[col].to_numpy()
        avant = (inf.y == 1) & (inf.violation == 0)
        vues, tot, av, par_type = 0, 0, [], {}
        for _, s in inf.groupby(['session', 'service'], sort=False):
            v, a, tr, sit = s.violation.to_numpy(), s[col].to_numpy(), s.transition.to_numpy(), s.situation.to_numpy()
            for i in range(10, len(v)):
                if v[i] == 1 and v[i - 1] == 0 and v[i - 10:i].sum() == 0:
                    tot += 1
                    idx = np.flatnonzero(a[i - 10:i])
                    cle = (sit[i], tr[i] or 'aucune')
                    par_type.setdefault(cle, [0, 0])[1] += 1
                    if len(idx):
                        vues += 1; av.append(10 - idx[0]); par_type[cle][0] += 1
        print(f'{nom:<24}{(pr == inf.y).mean():>10.1%}{pr[inf.y == 0].mean():>17.1%}{pr[avant].mean():>29.1%}{f"{vues}/{tot}":>19}{np.mean(av) if av else 0:>7.1f} s')
        if col == 'alerte':
            detail = par_type
    print('   pannes vues venir par type : ' + ' ; '.join(f'{s} {t} {a}/{b}' for (s, t), (a, b) in sorted(detail.items())))
    Xa, ya = resumes(d)
    pa = _mlp((Xa - p['a_moy']) / p['a_ec'], p['a_W1'], p['a_b1'], p['a_W2'], p['a_b2']).argmax(1)
    print(f'1a — « quel problème ? » : précision {(pa == ya).mean():.1%}')
    mc = pd.crosstab(pd.Series([CLASSES[i] for i in ya], name='vraie'), pd.Series([CLASSES[i] for i in pa], name='prédite'))
    print('  ' + mc.to_string().replace('\n', '\n  '))


class Detecteur3:

    def __init__(self, chemin=POIDS):
        self.p = dict(np.load(chemin))
        self.fen, self.oui = deque(maxlen=W), deque(maxlen=3)

    def ajouter(self, mesures, viole=False):
        p = self.p
        self.fen.append(echelle([mesures[k] for k in ENTREES]))
        if len(self.fen) < W:
            return None
        F = np.array(self.fen)
        pf = float(1 / (1 + np.exp(-_mlp(F.reshape(1, -1), p['b_W1'], p['b_b1'], p['b_W2'], p['b_b2'])[0, 0])))
        self.oui.append(pf > 0.5)
        res = np.hstack([F.mean(0), F.std(0), (F * T_C[:, None]).sum(0) / (T_C ** 2).sum(), F.max(0)])
        z = _mlp(((res - p['a_moy']) / p['a_ec'])[None], p['a_W1'], p['a_b1'], p['a_W2'], p['a_b2'])[0]
        prevision = len(self.oui) == 3 and all(self.oui)
        return dict(p_futur=pf, prevision=prevision, alerte=prevision or viole, classe=CLASSES[int(np.argmax(z))])


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == 'entrainer':
        entrainer(*[int(x) for x in sys.argv[2:5]])
    elif len(sys.argv) > 2 and sys.argv[1] == 'mixte':
        entrainer(reel=sys.argv[2], poids=ICI / 'detecteur3_mixte.npz')
    elif len(sys.argv) > 2 and sys.argv[1] == 'evaluer':
        d = pd.read_csv(sys.argv[2])
        if 'test' in sys.argv:
            d = d[d.session.isin(REEL_TEST)]
        npz = next((x for x in sys.argv[3:] if x.endswith('.npz')), None)
        evaluer(d, f'VRAI RÉSEAU ({sys.argv[2]}{", sessions de test" if "test" in sys.argv else ""}) — {npz or POIDS.name}', npz)
    else:
        print("usage : voir README.md")
