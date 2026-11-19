import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from reentrainement import W, brut, entrainer, entrees, t_c

TAILLES = [2, 4, 8, 14, 20, 27, 34]
TIRAGES = 3
toutes = sorted(brut[brut.split == "train"].session.unique())
val = sorted(brut[brut.split == "val"].session.unique())
print(len(toutes), "sessions d'entraînement disponibles ; validation :", val)


def essai_1b(sessions):
    d = brut.copy()
    tr = d.session.isin(sessions)
    d[entrees] = d[entrees].fillna(d.loc[tr, entrees].max())
    moy, ec = d.loc[tr, entrees].mean(), d.loc[tr, entrees].std() + 1e-9
    d[entrees] = (d[entrees] - moy) / ec

    def fen(liste):
        Xs, ys, inf = [], [], []
        for _, s in d[d.session.isin(liste)].groupby("session"):
            M = s[entrees].to_numpy()
            X = sliding_window_view(M, (W, M.shape[1]))[:, 0].reshape(len(M) - W + 1, -1)
            fin = s.iloc[W - 1:]
            g = fin.viol_futur_10.notna().to_numpy()
            Xs.append(X[g]); ys.append(fin.viol_futur_10.to_numpy()[g])
            inf.append(fin[g][["session", "violation"]])
        return np.vstack(Xs), np.concatenate(ys), pd.concat(inf)

    Xt, yt, _ = fen(sessions)
    Xv, yv, inf = fen(val)
    modele = entrainer(Xt, yt, Xv, yv, 1, 3000, 0.0)
    oui = pd.Series((modele(Xv)[:, 0] > 0.5).astype(int), index=inf.index)
    pred = oui.groupby(inf.session.to_numpy()).transform(lambda x: x.rolling(3, min_periods=3).min().fillna(0)).to_numpy()
    vues = tot = 0
    for _, s in inf.assign(pred=pred).groupby("session"):
        v, pr = s.violation.to_numpy(), s.pred.to_numpy()
        for i in range(10, len(v)):
            if v[i] == 1 and v[i - 1] == 0 and v[i - 10:i].sum() == 0:
                tot += 1; vues += pr[i - 10:i].any()
    avant = (yv == 1) & (inf.violation.to_numpy() == 0)
    return dict(vues=100 * vues / tot, avant=100 * pred[avant].mean(), fausses=100 * pred[yv == 0].mean())


def essai_1a(sessions):
    d = brut.copy()
    for c in ("lat_moy_ms", "lat_med_ms", "lat_max_ms"):
        d[c] = d[c].fillna(1500.0)
    d["classe4"] = d.classe.replace({"degrade": "lien", "panne": "lien"})
    classes = sorted(d.classe4.unique())

    def resumes(liste):
        Xs, ys = [], []
        for _, s in d[d.session.isin(liste)].groupby("session"):
            F = sliding_window_view(s[entrees].to_numpy(), (W, len(entrees)))[:, 0]
            pente = (F * t_c[None, :, None]).sum(axis=1) / (t_c ** 2).sum()
            Xs.append(np.hstack([F.mean(axis=1), F.std(axis=1), pente, F.max(axis=1)]))
            ys.append(s.classe4.map(classes.index).to_numpy()[W - 1:])
        return np.vstack(Xs), np.concatenate(ys)

    Xt, yt = resumes(sessions)
    Xv, yv = resumes(val)
    if len(set(yt)) < len(classes):
        return dict(precision=np.nan, congestion=np.nan)
    moy, ec = Xt.mean(axis=0), Xt.std(axis=0) + 1e-9
    modele = entrainer((Xt - moy) / ec, yt, (Xv - moy) / ec, yv, len(classes), 1500, 1e-3)
    p = modele((Xv - moy) / ec).argmax(axis=1)
    k = classes.index("congestion")
    return dict(precision=100 * (p == yv).mean(), congestion=100 * (p[yv == k] == k).mean())


lignes = []
for n in TAILLES:
    for tirage in range(TIRAGES if n < len(toutes) else 1):
        sessions = list(np.random.default_rng(100 * n + tirage).choice(toutes, n, replace=False))
        r = dict(n=n, tirage=tirage, **essai_1b(sessions), **essai_1a(sessions))
        lignes.append(r)
        print({k: (round(v, 1) if isinstance(v, float) else v) for k, v in r.items()}, flush=True)
res = pd.DataFrame(lignes)
res.to_csv("courbe.csv", index=False)

fig, axes = plt.subplots(1, 2, figsize=(13, 4.5))
for ax, cols, titre in [(axes[0], [("vues", "pannes vues venir (%)"), ("avant", "secondes d'avant-panne détectées (%)"), ("fausses", "fausses alertes (%)")],
                         "1b : « une violation arrive dans 10 s ? »"),
                        (axes[1], [("precision", "précision (%)"), ("congestion", "congestions reconnues (%)")], "1a : « quel problème ? »")]:
    for col, nom in cols:
        g = res.groupby("n")[col]
        ax.plot(g.mean().index, g.mean(), marker="o", label=nom)
        ax.fill_between(g.mean().index, g.min(), g.max(), alpha=0.2)
    ax.set_title(titre); ax.set_xlabel("nombre de sessions d'entraînement (10 min chacune)"); ax.set_xticks(TAILLES)
    ax.set_ylim(0, 100); ax.grid(alpha=0.3); ax.legend()
fig.suptitle("Courbe d'apprentissage, mesurée sur la validation (trait = moyenne de 3 tirages, bande = du pire au meilleur)")
plt.tight_layout()
plt.savefig("courbe_apprentissage.png", dpi=110)
plt.show()
