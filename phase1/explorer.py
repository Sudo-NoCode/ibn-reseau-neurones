import json
import sys

import matplotlib.pyplot as plt
import pandas as pd

DOSSIER = str(__import__("pathlib").Path(__file__).resolve().parent.parent / "data" / "processed")

df = pd.read_csv(DOSSIER + "/dataset.csv")
schema = json.load(open(DOSSIER + "/schema.json", encoding="utf-8"))
entrees = schema["entrees"]

print("1. Forme du tableau :", df.shape)

pd.set_option("display.width", 200)
print("\n2. Premières lignes :")
print(df[entrees + ["classe", "violation"]].head())

train = df[df["split"] == "train"]
print("\n3. Classes dans train :")
print(train["classe"].value_counts())

SESSION = sys.argv[1] if len(sys.argv) > 1 else "s01"
s01 = df[df["session"] == SESSION]
plt.figure(figsize=(12, 4))
plt.plot(s01["sec"], s01["lat_med_ms"], color="gray", linewidth=0.8)
for classe, couleur in [("congestion", "red"), ("degrade", "orange"), ("instable", "purple"), ("panne", "brown")]:
    morceau = s01[s01["classe"] == classe]
    plt.scatter(morceau["sec"], morceau["lat_med_ms"], s=6, color=couleur, label=classe)
plt.axhline(60, linestyle="--", color="black", label="seuil de l'intention (60 ms)")
plt.yscale("log")
plt.xlabel("temps (s)")
plt.ylabel("latence médiane (ms, échelle log)")
plt.title(f"Session {SESSION} : latence et scénarios")
plt.legend()
plt.tight_layout()
plt.savefig(f"{SESSION}_latence.png", dpi=120)
plt.show()
