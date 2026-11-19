import json

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

DOSSIER = str(__import__("pathlib").Path(__file__).resolve().parent.parent / "data" / "processed")
W = 10
CACHES = 16
TAUX = 0.3
EPOQUES = 1500
L2 = 1e-3
LATENCE_SI_TOUT_PERDU = 1500.0

df = pd.read_csv(DOSSIER + "/dataset.csv").sort_values(["session", "sec"])
entrees = json.load(open(DOSSIER + "/schema.json", encoding="utf-8"))["entrees"]
df = df[df.split.isin(["train", "val"])]
for c in ("lat_moy_ms", "lat_med_ms", "lat_max_ms"):
    df[c] = df[c].fillna(LATENCE_SI_TOUT_PERDU)
SESSIONS = sorted(df.session.unique())
t_centre = np.arange(W) - (W - 1) / 2


def preparer():
    out = {}
    for sid, s in df.groupby("session"):
        M = s[entrees].to_numpy()
        F = sliding_window_view(M, (W, M.shape[1]))[:, 0]
        brut = F.reshape(len(F), -1)
        pente = (F * t_centre[None, :, None]).sum(axis=1) / (t_centre ** 2).sum()
        resume = np.hstack([F.mean(axis=1), F.std(axis=1), pente, F.max(axis=1)])
        out[sid] = dict(brut=brut, resume=resume, classe=s.classe.to_numpy()[W - 1:])
    return out


def softmax(Z):
    E = np.exp(Z - Z.max(axis=1, keepdims=True))
    return E / E.sum(axis=1, keepdims=True)


def perte(P, y):
    return -np.mean(np.log(P[np.arange(len(y)), y] + 1e-9))


def entrainer(Xt, yt, Xe, ye, K):
    rng = np.random.default_rng(0)
    n = Xt.shape[1]
    W1, b1 = rng.normal(0, np.sqrt(1 / n), (n, CACHES)), np.zeros(CACHES)
    W2, b2 = rng.normal(0, np.sqrt(1 / CACHES), (CACHES, K)), np.zeros(K)
    Yt = np.eye(K)[yt]
    avant = lambda X, W1, b1, W2, b2: (lambda A: (A, softmax(A @ W2 + b2)))(np.tanh(X @ W1 + b1))
    meilleure, garde = np.inf, None
    for _ in range(EPOQUES):
        A1, P = avant(Xt, W1, b1, W2, b2)
        D = (P - Yt) / len(yt)
        Dc = (D @ W2.T) * (1 - A1 ** 2)
        W2 = W2 - TAUX * (A1.T @ D + L2 * W2)
        b2 = b2 - TAUX * D.sum(axis=0)
        W1 = W1 - TAUX * (Xt.T @ Dc + L2 * W1)
        b1 = b1 - TAUX * Dc.sum(axis=0)
        pe = perte(avant(Xe, W1, b1, W2, b2)[1], ye)
        if pe < meilleure:
            meilleure, garde = pe, (W1.copy(), b1.copy(), W2.copy(), b2.copy())
    return lambda X: avant(X, *garde)[1].argmax(axis=1)


def validation_croisee(donnees, cle, fusion):
    nom_classe = (lambda c: "lien" if c in ("degrade", "panne") else c) if fusion else (lambda c: c)
    classes = sorted({nom_classe(c) for d in donnees.values() for c in d["classe"]})
    vrai, predit, scores = [], [], []
    for i, test in enumerate(SESSIONS):
        arret = SESSIONS[(i + 1) % len(SESSIONS)]
        appr = [s for s in SESSIONS if s not in (test, arret)]
        X = lambda ss: np.vstack([donnees[s][cle] for s in ss])
        y = lambda ss: np.array([classes.index(nom_classe(c)) for s in ss for c in donnees[s]["classe"]])
        Xa = X(appr)
        moy, ecart = Xa.mean(axis=0), Xa.std(axis=0) + 1e-9
        norm = lambda Z: (Z - moy) / ecart
        modele = entrainer(norm(Xa), y(appr), norm(X([arret])), y([arret]), len(classes))
        p, v = modele(norm(X([test]))), y([test])
        vrai.append(v)
        predit.append(p)
        scores.append((p == v).mean())
    return classes, np.concatenate(vrai), np.concatenate(predit), np.array(scores)


def rapport(titre, classes, v, p, scores):
    print(f"\n===== {titre} =====")
    print(f"précision : {(p == v).mean():.1%}   (par session : {scores.mean():.1%} ± {scores.std():.1%},"
          f" de {scores.min():.0%} à {scores.max():.0%})")
    f1s = []
    lignes = []
    for k, c in enumerate(classes):
        vp = ((p == k) & (v == k)).sum()
        prec, rap = vp / max(1, (p == k).sum()), vp / max(1, (v == k).sum())
        f1 = 2 * prec * rap / max(1e-9, prec + rap)
        f1s.append(f1)
        lignes.append(f"  {c:<11} précision {prec:6.1%}  rappel {rap:6.1%}  F1 {f1:.2f}")
    print("\n".join(lignes))
    print(f"  F1 moyen (macro) : {np.mean(f1s):.2f}")
    return np.mean(f1s)


donnees = preparer()
print(f"{len(SESSIONS)} sessions, {sum(len(d['classe']) for d in donnees.values())} fenêtres")
res = {}
for titre, cle, fusion in [("A  fenêtre brute, 5 classes", "brut", False),
                           ("B  résumés de fenêtre, 5 classes", "resume", False),
                           ("C  résumés de fenêtre, 4 classes", "resume", True)]:
    classes, v, p, scores = validation_croisee(donnees, cle, fusion)
    res[titre] = rapport(titre, classes, v, p, scores)
    if titre.startswith("C"):
        print("\n  matrice de confusion (lignes = vraie, colonnes = prédite) :")
        mc = pd.crosstab(pd.Series([classes[i] for i in v], name="vraie"), pd.Series([classes[i] for i in p], name="prédite"))
        print("  " + mc.to_string().replace("\n", "\n  "))
