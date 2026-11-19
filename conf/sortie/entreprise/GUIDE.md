# Guide Packet Tracer — entreprise

Intention : « réseau d'entreprise avec visio, voix, données, invités et serveurs ; 2 commutateurs d'accès ; cœur redondant ; les invités ne doivent pas accéder aux serveurs »

```
Intention : « réseau d'entreprise avec visio, voix, données, invités et serveurs ; 2 commutateurs d'accès ; cœur redondant ; les invités ne doivent pas accéder aux serveurs »
  services  : visio (VLAN 10), voix (VLAN 20), donnees (VLAN 30), invites (VLAN 40), serveurs (VLAN 50) + gestion (VLAN 99)
  accès     : 2 commutateur(s), 20 postes chacun
  cœur      : redondant (2 commutateurs de niveau 3, HSRP)
  sortie    : routeur 2911 vers « Internet » (203.0.113.1)
  filtrage  : invites ✗→ serveurs
  compris   : services : visio, voix, donnees, invites, serveurs; 2 commutateur(s) d'accès; cœur redondant; filtrage : invites ne joint pas serveurs
  → ACCEPTÉE
```

## 1. Poser les équipements

| Nom | Modèle Packet Tracer | Rôle |
|---|---|---|
| CORE1 | 3560-24PS | coeur |
| CORE2 | 3560-24PS | coeur |
| ACC1 | 2960-24TT | acces |
| ACC2 | 2960-24TT | acces |
| EDGE | 2911 | sortie |
| SRV1 | Server-PT | serveur |
| PC-VISIO-1 | PC-PT | pc |
| PC-VOIX-1 | PC-PT | pc |
| PC-DONNEES-1 | PC-PT | pc |
| PC-INVITES-1 | PC-PT | pc |
| PC-VISIO-2 | PC-PT | pc |
| PC-VOIX-2 | PC-PT | pc |
| PC-DONNEES-2 | PC-PT | pc |
| PC-INVITES-2 | PC-PT | pc |

Renommez chaque équipement (onglet Config > Display Name) pour vous y retrouver.

## 2. Câbler

| De | Port | Vers | Port | Câble |
|---|---|---|---|---|
| ACC1 | GigabitEthernet0/1 | CORE1 | FastEthernet0/1 | croisé (cuivre) |
| ACC1 | GigabitEthernet0/2 | CORE2 | FastEthernet0/1 | croisé (cuivre) |
| ACC2 | GigabitEthernet0/1 | CORE1 | FastEthernet0/2 | croisé (cuivre) |
| ACC2 | GigabitEthernet0/2 | CORE2 | FastEthernet0/2 | croisé (cuivre) |
| CORE1 | GigabitEthernet0/1 | CORE2 | GigabitEthernet0/1 | croisé (cuivre) |
| CORE1 | GigabitEthernet0/2 | EDGE | GigabitEthernet0/0 | droit (cuivre) |
| CORE2 | GigabitEthernet0/2 | EDGE | GigabitEthernet0/1 | droit (cuivre) |
| SRV1 | FastEthernet0 | CORE1 | FastEthernet0/24 | droit (cuivre) |
| PC-VISIO-1 | FastEthernet0 | ACC1 | FastEthernet0/1 | droit (cuivre) |
| PC-VOIX-1 | FastEthernet0 | ACC1 | FastEthernet0/6 | droit (cuivre) |
| PC-DONNEES-1 | FastEthernet0 | ACC1 | FastEthernet0/11 | droit (cuivre) |
| PC-INVITES-1 | FastEthernet0 | ACC1 | FastEthernet0/16 | droit (cuivre) |
| PC-VISIO-2 | FastEthernet0 | ACC2 | FastEthernet0/1 | droit (cuivre) |
| PC-VOIX-2 | FastEthernet0 | ACC2 | FastEthernet0/6 | droit (cuivre) |
| PC-DONNEES-2 | FastEthernet0 | ACC2 | FastEthernet0/11 | droit (cuivre) |
| PC-INVITES-2 | FastEthernet0 | ACC2 | FastEthernet0/16 | droit (cuivre) |

## 3. Coller les configurations

Pour chaque commutateur et routeur : cliquez dessus > onglet **CLI** > appuyez sur Entrée si une question s'affiche (répondez **no** à « initial configuration dialog ») > collez tout le contenu du fichier `<NOM>.txt`.

Ordre conseillé : les commutateurs d'accès, puis les cœurs, puis EDGE.
Attendez environ 30 secondes après le dernier collage (le temps que le spanning-tree, HSRP et OSPF se mettent en place), ou cliquez plusieurs fois sur **Fast Forward Time** en bas.

## 4. Régler les PC et le serveur

- Chaque **PC** : Desktop > IP Configuration > **DHCP**. Il doit recevoir une adresse dans son VLAN.
- **SRV1** : Desktop > IP Configuration > Static : IP `10.0.50.10`, masque `255.255.255.0`, passerelle `10.0.50.1`.

## 5. Tester (et noter ce qui ne marche pas)

| # | Test | Où | Résultat attendu |
|---|---|---|---|
| 1 | Adresse reçue par DHCP | PC-VISIO-1 > Desktop > IP Configuration | `10.0.10.x`, passerelle `10.0.10.1` |
| 2 | `ping <adresse de PC-VOIX-2>` (lue dans son IP Configuration ; autre VLAN, autre commutateur) | PC-VISIO-1 > Command Prompt | réponses (le cœur route entre VLAN) |
| 3 | `ping 203.0.113.1` | PC-VISIO-1 > Command Prompt | réponses (sortie par EDGE, appris par OSPF) |
| 4 | `ping 10.0.50.10` | PC-INVITES-1 > Command Prompt | **échec** (« Destination host unreachable » ou délai dépassé) : la liste d'accès bloque |
| 5 | `ping 10.0.50.10` | PC-VISIO-1 > Command Prompt | réponses (seul invites est bloqué) |
| 6 | `show standby brief` | CORE1 et CORE2 | CORE1 **Active**, CORE2 **Standby** sur chaque VLAN |
| 7 | `show ip ospf neighbor` | EDGE | 2 voisin(s) en état **FULL** |
| 8 | `ssh -l admin 10.0.99.11` | PC-DONNEES-1 > Command Prompt | demande le mot de passe `Lab-e28a816f`, puis l'invite ACC1# |
| 9 | Panne du cœur : `ping -t 203.0.113.1` sur PC-VISIO-1, puis éteindre CORE1 (onglet Physical, bouton) | PC-VISIO-1 | quelques pertes, puis les réponses reviennent : CORE2 devient Active |

## Notes

- Mot de passe de **labo** (enable et SSH) : `Lab-e28a816f`. Sur un vrai réseau, il faudrait le changer.
- Le DHCP est sur CORE1 seulement : si CORE1 tombe, les PC déjà configurés continuent de marcher, mais un nouveau PC n'aura pas d'adresse.
- Ces commandes n'ont pas été exécutées par le générateur lui-même : si Packet Tracer refuse une ligne (« % Invalid input »), note le nom de l'équipement et la ligne, c'est exactement ce qu'il me faut pour corriger.

## Résultat du test dans Packet Tracer (16/11/2026, fichier `entreprise.pkt`)

| # | Test | Résultat |
|---|---|---|
| 1 | DHCP | ✅ les 8 PC reçoivent une adresse dans leur VLAN (10.0.10.21/22, 10.0.20.21/22, 10.0.30.21/22, 10.0.40.21/22) |
| 2 | ping entre VLAN (PC0 visio → PC5 voix, autre commutateur) | ✅ |
| 3 | ping 203.0.113.1 (« Internet ») | ✅ |
| 4 | invités → serveur | ✅ bloqué (« Destination host unreachable » renvoyé par CORE1) ; les invités gardent l'accès à Internet |
| 5 | visio → serveur | ✅ |
| 6 | HSRP | ✅ CORE1 Active, CORE2 Standby sur les 6 VLAN |
| 7 | OSPF | ✅ FULL entre CORE1, CORE2 et EDGE |
| 8 | SSH de PC2 vers ACC1 | ✅ |
| 9 | panne de CORE1 | ✅ Internet reste joignable via CORE2 ; ❌ le serveur devient injoignable (il n'est branché que sur CORE1) ; au retour, CORE1 reprend la main (preempt) |

Aucune ligne de configuration refusée par Packet Tracer. Incidents pendant la mise en place, dus au montage et pas aux configurations :
câbles des PC d'abord branchés sur RS 232 au lieu de FastEthernet0, config de CORE2 collée par erreur sur CORE1 (effacé et recollé),
et ACC1 qui a dû être redémarré pour que les VLAN 20 et 40 passent après le redémarrage de CORE1.
