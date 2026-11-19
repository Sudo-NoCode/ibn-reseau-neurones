import json
import sys

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

DOSSIER = str(__import__("pathlib").Path(__file__).resolve().parent.parent / "data" / "processed")
W, CACHES, TAUX = 10, 16, 0.3
TRAIN = tuple((sys.argv[1] if __name__ == "__main__" and len(sys.argv) > 1 else "s,p").split(","))
JEUX = {"test brusque (s17-20)": lambda d: (d.split == "test") & d.session.str.startswith("s"),
        "test progressif (p24-28)": lambda d: (d.split == "test") & d.session.str.startswith("p"),
        "généralisation 10 Mbps": lambda d: d.session.str.startswith("g10"),
        "généralisation 50 Mbps": lambda d: d.session.str.startswith("g50")}

brut = pd.read_csv(DOSSIER + "/dataset.csv").sort_values(["session", "sec"])
brut.loc[brut.split.isin(["train", "val"]) & ~brut.session.str.startswith(TRAIN), "split"] = "ecarte"
print("entraînement sur :", sorted(brut[brut.split == "train"].session.unique()))
entrees = json.load(open(DOSSIER + "/schema.json", encoding="utf-8"))["entrees"]
t_c = np.arange(W) - (W - 1) / 2


def sigmoide(z):
    return 1 / (1 + np.exp(-z))


def softmax(Z):
    E = np.exp(Z - Z.max(axis=1, keepdims=True))
    return E / E.sum(axis=1, keepdims=True)


def entrainer(Xt, yt, Xe, ye, K, epoques, l2):
    rng = np.random.default_rng(0)
    n = Xt.shape[1]
    W1, b1 = rng.normal(0, np.sqrt(1 / n), (n, CACHES)), np.zeros(CACHES)
    W2, b2 = rng.normal(0, np.sqrt(1 / CACHES), (CACHES, K)), np.zeros(K)
    Y = yt[:, None].astype(float) if K == 1 else np.eye(K)[yt]
    sortie = (lambda Z: sigmoide(Z)) if K == 1 else softmax

    def avant(X, W1, b1, W2, b2):
        A = np.tanh(X @ W1 + b1)
        return A, sortie(A @ W2 + b2)

    def perte(P, y):
        if K == 1:
            p = P[:, 0]
            return -np.mean(y * np.log(p + 1e-9) + (1 - y) * np.log(1 - p + 1e-9))
        return -np.mean(np.log(P[np.arange(len(y)), y] + 1e-9))

    meilleure, garde = np.inf, None
    for _ in range(epoques):
        A1, P = avant(Xt, W1, b1, W2, b2)
        D = (P - Y) / len(yt)
        Dc = (D @ W2.T) * (1 - A1 ** 2)
        W2, b2 = W2 - TAUX * (A1.T @ D + l2 * W2), b2 - TAUX * D.sum(axis=0)
        W1, b1 = W1 - TAUX * (Xt.T @ Dc + l2 * W1), b1 - TAUX * Dc.sum(axis=0)
        pe = perte(avant(Xe, W1, b1, W2, b2)[1], ye)
        if pe < meilleure:
            meilleure, garde = pe, (W1.copy(), b1.copy(), W2.copy(), b2.copy())
    return lambda X: avant(X, *garde)[1]


def phase_1b():
    d = brut.copy()
    tr = d.split == "train"
    d[entrees] = d[entrees].fillna(d.loc[tr, entrees].max())
    moy, ec = d.loc[tr, entrees].mean(), d.loc[tr, entrees].std() + 1e-9
    d[entrees] = (d[entrees] - moy) / ec

    def fen(masque):
        Xs, ys, inf = [], [], []
        for _, s in d[masque(d)].groupby("session"):
            M = s[entrees].to_numpy()
            X = sliding_window_view(M, (W, M.shape[1]))[:, 0].reshape(len(M) - W + 1, -1)
            fin = s.iloc[W - 1:]
            g = fin.viol_futur_10.notna().to_numpy()
            Xs.append(X[g]); ys.append(fin.viol_futur_10.to_numpy()[g])
            inf.append(fin[g][["session", "sec", "violation", "ref_persistance"]])
        return np.vstack(Xs), np.concatenate(ys), pd.concat(inf)

    Xt, yt, _ = fen(lambda x: x.split == "train")
    Xv, yv, _ = fen(lambda x: x.split == "val")
    modele = entrainer(Xt, yt, Xv, yv, 1, 3000, 0.0)

    def lisser(inf, pred, k=3):
        s = pd.Series(pred, index=inf.index)
        return s.groupby(inf.session.to_numpy()).transform(lambda x: x.rolling(k, min_periods=k).min().fillna(0)).to_numpy().astype(int)

    def anticip(inf, pred):
        vus, av, tot = 0, [], 0
        for _, s in inf.assign(pred=pred).groupby("session"):
            v, pr = s.violation.to_numpy(), s.pred.to_numpy()
            for i in range(10, len(v)):
                if v[i] == 1 and v[i - 1] == 0 and v[i - 10:i].sum() == 0:
                    tot += 1
                    a = np.flatnonzero(pr[i - 10:i])
                    if len(a):
                        vus += 1; av.append(10 - a[0])
        return vus, tot, (np.mean(av) if av else 0.0)

    print("\n================ PHASE 1b : « une violation arrive dans 10 s ? » ================")
    print(f"{"jeu":<28}{'modèle':<24}{'précision':>10}{'fausses alertes':>17}{'pannes vues venir':>19}{'avance':>9}")
    for nom, masque in JEUX.items():
        X, y, inf = fen(masque)
        brut_pred = (modele(X)[:, 0] > 0.5).astype(int)
        for lib, pr in [("MLP 10 s + 3 oui", lisser(inf, brut_pred)), ("règle persistance", inf.ref_persistance.to_numpy())]:
            vus, tot, av = anticip(inf, pr)
            print(f"{nom:<28}{lib:<24}{(pr == y).mean():>10.1%}{pr[y == 0].mean():>17.1%}{f'{vus}/{tot}':>19}{av:>7.1f} s")


def phase_1a():
    d = brut.copy()
    for c in ("lat_moy_ms", "lat_med_ms", "lat_max_ms"):
        d[c] = d[c].fillna(1500.0)
    d["classe4"] = d.classe.replace({"degrade": "lien", "panne": "lien"})
    classes = sorted(d.classe4.unique())

    def resumes(masque):
        Xs, ys = [], []
        for _, s in d[masque(d)].groupby("session"):
            F = sliding_window_view(s[entrees].to_numpy(), (W, len(entrees)))[:, 0]
            pente = (F * t_c[None, :, None]).sum(axis=1) / (t_c ** 2).sum()
            Xs.append(np.hstack([F.mean(axis=1), F.std(axis=1), pente, F.max(axis=1)]))
            ys.append(s.classe4.map(classes.index).to_numpy()[W - 1:])
        return np.vstack(Xs), np.concatenate(ys)

    Xt, yt = resumes(lambda x: x.split == "train")
    Xv, yv = resumes(lambda x: x.split == "val")
    moy, ec = Xt.mean(axis=0), Xt.std(axis=0) + 1e-9
    modele = entrainer((Xt - moy) / ec, yt, (Xv - moy) / ec, yv, len(classes), 1500, 1e-3)

    print("\n================ PHASE 1a : « quel problème ? » (4 classes) ================")
    for nom, masque in JEUX.items():
        X, y = resumes(masque)
        p = modele((X - moy) / ec).argmax(axis=1)
        f1s = []
        for k in range(len(classes)):
            vp = ((p == k) & (y == k)).sum()
            pr, rp = vp / max(1, (p == k).sum()), vp / max(1, (y == k).sum())
            f1s.append(2 * pr * rp / max(1e-9, pr + rp))
        print(f"\n{nom} : précision {(p == y).mean():.1%}   F1 macro {np.mean(f1s):.2f}   "
              + "   ".join(f"{c} {f:.2f}" for c, f in zip(classes, f1s)))
        mc = pd.crosstab(pd.Series([classes[i] for i in y], name="vraie"), pd.Series([classes[i] for i in p], name="prédite"))
        print("  " + mc.to_string().replace("\n", "\n  "))


if __name__ == "__main__":
    phase_1b()
    phase_1a()
