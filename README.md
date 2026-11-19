# IBN — un réseau piloté par l'intention, avec des réseaux de neurones

On dit au réseau **ce qu'on veut**, en français, et il se débrouille pour le tenir :

```
« visio excellente ; voix correcte, sans passer par c2 »
   → contrat chiffré par service (latence, pertes, gigue, débit, chemins interdits)
   → coût annoncé AVANT d'accepter (simulé dans un jumeau numérique)
   → surveillance chaque seconde, détection et diagnostic des pannes (réseaux de neurones)
   → réparation automatique : chemin, priorité, bridage (agent d'apprentissage par renforcement)
   → journal et rapport d'incidents
```

Tout est validé sur un **vrai réseau émulé** (Mininet, Open vSwitch, trafic iperf3), pas seulement en simulation.

*English summary: an Intent-Based Networking prototype. A French sentence becomes a per-service contract; neural
detectors forecast and diagnose faults; a shared-weights DQN agent trained in a calibrated digital twin repairs the
network (path, priority, rate limiting). Everything is validated on an emulated network (Mininet). A separate generator
turns a design intent into Cisco IOS configurations, tested in Packet Tracer.*

---

## Résultats

Tous mesurés sur le réseau émulé, sauf mention contraire.

| Brique | Résultat |
|---|---|
| **Jumeau numérique** du réseau à 3 couches (modèle lien par lien, calibré sur 35 essais réels) | même verdict que le réel sur le contrat, seconde par seconde : 92 % (lien abîmé), 98 % (lien saturé), 98 % (cas combinés jamais utilisés pour le régler) |
| **Agent** (DQN à poids partagés, entraîné uniquement dans le jumeau) contre une règle manuelle à sondes, 11 programmes de pannes de 10 min | hors contrat **0,7 %** (visio) et **0,7 %** (voix), contre 2,7 % et 3,4 % pour la règle ; meilleur sur 11 programmes sur 11 |
| **Détecteur** « contrat perdu dans 10 s ? » (MLP), 14 sessions réelles | 95,2 % de précision, 1,5 % de fausses alertes ; voit venir toutes les congestions progressives (18 sur 18), 6,8 s d'avance en moyenne ; ne prévoit pas les pannes brusques (aucun signe avant-coureur) |
| **Diagnostic** « quel problème ? » (MLP, 4 classes), 4 sessions réelles jamais vues | 98,4 % (entraîné jumeau + réel), 92,3 % (jumeau seul) |
| **Boucle IBN complète**, démos de 7 min avec 4 pannes | contrat tenu 98,3 à 99,0 % ; 85 % pour une voix privée de la moitié de ses chemins (coût de l'interdit, annoncé avant) |
| **Générateur de configurations Cisco** (intention de conception → IOS) | réseau d'entreprise de 5 équipements monté dans Packet Tracer : aucune commande refusée, 9 tests sur 9 (DHCP, inter-VLAN, ACL, HSRP, OSPF, SSH, bascule) ; limite trouvée : serveur branché sur un seul cœur |

Les tableaux complets sont dans `phase4/fidelite3.txt`, `phase4/detecteur3_reel.txt`, `phase4/compare_v1_v2.txt`,
`resultats/` et `conf/sortie/entreprise/GUIDE.md`.

## Ce que le projet n'est pas (limites honnêtes)

- Le réseau est **émulé et petit** (8 commutateurs, 2 services) ; les pannes sont celles que le labo provoque.
- Le traducteur d'intentions repère des **mots-clés** : il ne comprend pas le français librement (il dit ce qu'il n'a pas compris).
- Les intentions avec leviers interdits ne sont validées que par des démos, pas par une campagne complète sur le réseau réel.
- Les pannes brusques ne peuvent pas être prévues, seulement réparées vite.

---

## Organisation

| Dossier | Contenu |
|---|---|
| `lab/` | le labo Mininet : topologies (`topo.py`, `topo2.py`, `topo3.py`), pannes (`scenarios.py`), mesures (`mesure.py`, `reseau3.py`), enregistrement et calibration, validation des agents sur le vrai réseau |
| `data/processed/` | jeu de données étiqueté du petit réseau (52 sessions, 31 200 secondes) |
| `phase1/` | les premiers réseaux de neurones, écrits à la main en NumPy : un neurone, un MLP, la fenêtre de 10 s, les deux détecteurs, la courbe d'apprentissage |
| `phase2/` | jumeau numérique et agent DQN du petit réseau |
| `phase3/` | intentions en français, agent conditionné par l'intention, détection en service, boucle IBN (petit réseau) |
| `phase4/` | réseau à 3 couches : jumeau lien par lien, environnement à deux agents, agent à poids partagés, détecteur, intentions par service, boucle IBN |
| `conf/` | conception et configuration automatiques : intention de conception → plan neutre → commandes Cisco IOS + guide Packet Tracer |
| `demo/` | page qui rejoue des démos réelles seconde par seconde (`demo/index.html`) |
| `valid_demo/`, `resultats/` | enregistrements des démos et des validations sur le réseau réel |

## Lancer le projet

Python 3.10 ou plus : `pip install -r requirements.txt`.

**Sans réseau émulé** (le jumeau suffit) :

```bash
cd phase4
python fidelite3.py                     # le jumeau comparé aux 35 essais réels
python env3.py                          # références : ne rien faire, règle manuelle
python agent3.py tester                 # l'agent entraîné contre la règle
python ibn3.py "visio excellente ; voix correcte, sans passer par c2" --oui      # la boucle IBN complète
python agent3.py entrainer 0            # réentraîner une graine (environ 50 min)
python detecteur3.py evaluer det3_reel.csv

cd ../conf
python cisco.py "réseau d'entreprise avec visio, voix, données, invités et serveurs ; 2 commutateurs d'accès ; cœur redondant ; les invités ne doivent pas accéder aux serveurs" mon-reseau
```

La page de démo s'ouvre directement : `demo/index.html`.

**Sur le réseau émulé** (Ubuntu avec Mininet, Open vSwitch et iperf3) : copier dans un même dossier les fichiers de
`lab/`, `phase2/`, `phase3/`, `phase4/` et les poids (`*.npz`, `phase4/v1/*`), puis par exemple :

```bash
sudo python3 topo3.py --test                     # vérifier la topologie à 3 couches
sudo python3 valide3.py 0,1,2 agent,regle        # agent contre règle sur le vrai réseau
sudo python3 ibn3_mininet.py "visio correcte ; voix correcte" --oui --progressif
```

## Méthode

1. **Mesurer** le vrai réseau sous des pannes contrôlées (retard, gigue, pertes, congestions UDP et TCP).
2. **Calibrer** un jumeau numérique sur ces mesures, puis vérifier qu'il donne les mêmes verdicts que le réel, y compris sur des cas jamais utilisés pour le régler.
3. **Entraîner** l'agent et le détecteur dans le jumeau (des milliers de sessions en quelques minutes), avec randomisation du jumeau.
4. **Valider** sur le vrai réseau, en écartant les secondes faussées par les gels de la machine hôte.

## Crédits

Projet personnel de licence (réseaux et télécommunications), développé avec l'assistance de Claude (Anthropic).
