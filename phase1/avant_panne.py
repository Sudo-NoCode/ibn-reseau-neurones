import matplotlib.pyplot as plt
import pandas as pd

DOSSIER = str(__import__("pathlib").Path(__file__).resolve().parent.parent / "data" / "processed")
df = pd.read_csv(DOSSIER + "/dataset.csv")
CAS = [("s01", 486, "s01 : congestion BRUSQUE"), ("p02", 378, "p02 : congestion progressive"),
       ("p02", 101, "p02 : dégradation progressive"), ("p02", 529, "p02 : panne progressive")]
MESURES = [("lat_med_ms", "latence (ms)", 60, True), ("visio_pertes_pct", "pertes visio (%)", 2, False),
           ("lien_occup_pct", "occupation du lien (%)", None, False)]

fig, axes = plt.subplots(len(MESURES), len(CAS), figsize=(15, 8), sharex=True)
for j, (session, debut, titre) in enumerate(CAS):
    s = df[(df.session == session) & (df.sec >= debut - 30) & (df.sec <= debut + 6)]
    for i, (col, nom, seuil, log) in enumerate(MESURES):
        ax = axes[i, j]
        ax.plot(s.sec - debut, s[col], color="tab:blue", marker="o", markersize=3)
        ax.axvline(0, color="red", linewidth=1)
        ax.axvspan(-10, 0, color="gold", alpha=0.25)
        if seuil is not None:
            ax.axhline(seuil, linestyle="--", color="black", linewidth=0.8)
        if log:
            ax.set_yscale("log")
        if i == 0:
            ax.set_title(titre, fontsize=11)
        if j == 0:
            ax.set_ylabel(nom)
        if i == len(MESURES) - 1:
            ax.set_xlabel("secondes avant / après le début de la violation")
fig.suptitle("Zone jaune = les 10 s avant la violation (ce que 1b doit prévoir) ; trait rouge = début ; pointillé = seuil du contrat")
plt.tight_layout()
plt.savefig("avant_panne.png", dpi=110)
plt.show()
