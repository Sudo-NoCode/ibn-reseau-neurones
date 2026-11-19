from collections import Counter, deque
from pathlib import Path

import numpy as np

from agent_intention import ACTIONS, ORDRE, JumeauIntention, Reseau, autorisees, masque_obs, q_de, traduire

ICI = Path(__file__).resolve().parent
NOM_ACTION = {'limiter_h2': 'brider h2 (les autres utilisateurs)', 'priorite_visio': 'priorité à la visio',
              'deroute_visio': 'visio déroutée sur le lien de secours', 'reinitialiser': 'retour à la normale'}
NOM_LEVIER = {'limite': 'bridage de h2', 'priorite': 'priorité visio', 'deroute': 'déroute sur le secours'}
SEUIL_CONFIRMATION = 15.0


class AgentIntention:

    def __init__(self, dossier=None, graines=range(5)):
        dossier = Path(dossier or ICI / 'v3')
        self.nets = []
        for g in graines:
            f = dossier / f'agent_intention_graine{g}.npz'
            if f.exists():
                n = Reseau(JumeauIntention.DIM_OBS, len(ACTIONS), np.random.default_rng(0))
                n.p = dict(np.load(f))
                self.nets.append(n)
        assert self.nets, f'aucun poids dans {dossier}'

    def __call__(self, obs, env=None):
        q = np.mean([q_de(n, obs[None])[0] for n in self.nets], 0)
        return int(np.argmax(np.where(masque_obs(obs), q, -1e9)))


def estimer_cout(intention, agent, series, episodes=3):
    viols, secours = [], []
    for g in range(episodes):
        env = JumeauIntention(intention=intention, series=series, randomiser=False, graine=2000 + g)
        obs, fin, v, d = env.reset(), False, 0, 0
        while not fin:
            obs, _, fin, info = env.step(agent(obs, env))
            v += info['violation']; d += info['leviers']['deroute']
        viols.append(100 * v / env.duree); secours.append(100 * d / env.duree)
    return float(np.mean(viols)), float(np.mean(secours))


def accepter(texte, agent, series, confirmer=None):
    it = traduire(texte)
    print(it.resume())
    if not it.acceptee:
        return None, 'refusée : ' + ' ; '.join(it.refus)
    cout, secours = estimer_cout(it, agent, series)
    print(f'  coût prévu (jumeau, 3 × 10 min de pannes jamais vues) : {cout:.1f} % du temps hors contrat, '
          f'secours utilisé {secours:.0f} % du temps')
    if cout > SEUIL_CONFIRMATION:
        raison = ''
        if 'deroute' in it.interdits:
            raison = ' — sans le secours, une panne du lien principal ne peut pas être réparée'
        print(f'  ⚠ cette intention coûte cher{raison}.')
        if confirmer is not None and not confirmer('  Confirmer quand même ? [o/n] '):
            return None, "annulée par l'utilisateur"
    return it, cout


class Journal:
    OUVERTURE, FERMETURE = 3, 10

    def __init__(self, intention, verite=None):
        self.it, self.verite = intention, verite
        self.incidents, self.courant = [], None
        self.mauvais = 0
        self.recents = deque(maxlen=5)
        self.derniere_action = None
        self.n = self.ok = self.secours = self.changements = 0
        self.lignes = []

    def _dire(self, t, msg):
        ligne = f'[{t:4d} s] {msg}'
        self.lignes.append(ligne)
        print(ligne, flush=True)

    def seconde(self, t, ok, det, avant, apres, vraie_cause=None):
        self.n += 1; self.ok += ok; self.secours += apres['deroute']
        self.mauvais = 0 if ok else self.mauvais + 1
        alerte = bool(det and det['alerte'])
        classe = det['classe'] if det and det['classe'] not in ('normal', 'inconnu') else None
        inc = self.courant

        if inc is None and (alerte or self.mauvais >= self.OUVERTURE):
            inc = self.courant = dict(debut=t, diag=Counter(c for c in self.recents if c), actions=[], hors_contrat=0,
                                      retabli=None, causes=Counter(), alerte=alerte, bons=0, annonce=False)
            if self.derniere_action and t - self.derniere_action[0] <= 5:
                inc['actions'].append(self.derniere_action)
            cause = 'alerte du détecteur, contrat encore tenu' if alerte and ok else \
                    ('alerte du détecteur' if alerte else f'{self.mauvais} s hors contrat')
            self._dire(t, f'⚠ INCIDENT ouvert ({cause}).')
        self.recents.append(classe)
        if inc is not None:
            inc['hors_contrat'] += not ok
            inc['bons'] = inc['bons'] + 1 if ok and not alerte else 0
            if vraie_cause:
                inc['causes'][vraie_cause] += 1
            if classe and t - inc['debut'] < 15:
                inc['diag'][classe] += 1
            if not inc['annonce'] and sum(inc['diag'].values()) >= 3:
                c, k = inc['diag'].most_common(1)[0]
                inc['annonce'] = True
                self._dire(t, f'  diagnostic du détecteur : {c} ({k}/{sum(inc["diag"].values())} lectures)')
            if inc['retabli'] is None and inc['hors_contrat'] and inc['bons'] >= 5:
                inc['retabli'] = t - 4
                self._dire(t, f'  ✓ contrat rétabli, {t - 4 - inc["debut"]} s après l\'ouverture.')
            if inc['bons'] >= self.FERMETURE:
                inc['fin'] = t - self.FERMETURE
                self.incidents.append(inc); self.courant = None
                bilan = f'dont {inc["hors_contrat"]} s hors contrat' if inc['hors_contrat'] else 'contrat jamais perdu'
                self._dire(t, f'✓ incident clos ({inc["fin"] - inc["debut"] + 1} s, {bilan}).')

        chg = [k for k in ORDRE if avant[k] != apres[k]]
        if chg:
            self.changements += len(chg)
            if not any(apres.values()):
                msg = 'retour à la normale (tous les leviers coupés)'
            else:
                msg = ', '.join(('+ ' if apres[k] else '− ') + NOM_LEVIER[k] for k in chg)
            self._dire(t, f'  → action : {msg}' + ('' if self.courant is not None else '  (préventive : aucun incident en cours)'))
            self.derniere_action = (t, msg)
            if self.courant is not None:
                self.courant['actions'].append((t, msg))

    def rapport(self, cout_prevu=None, bloques=0):
        it = self.it
        s = it.seuils
        if self.courant is not None:
            self.courant['fin'] = self.n
            self.incidents.append(self.courant); self.courant = None
        L = ['=' * 78, 'RAPPORT IBN', '=' * 78,
             f'Intention : « {it.texte} »',
             f'Contrat   : latence ≤ {s["lat"]:g} ms, pertes ≤ {s["pertes"]:g} %, gigue ≤ {s["gigue"]:g} ms, '
             f'débit ≥ {s["debit"]:g} Mb/s ; interdits : {", ".join(sorted(it.interdits)) or "aucun"}',
             f'Durée     : {self.n} s',
             f'Contrat tenu : {100 * self.ok / self.n:.1f} % du temps'
             + (f'   (prévu par le jumeau : {100 - cout_prevu:.1f} %)' if cout_prevu is not None else ''),
             f'Leviers   : secours utilisé {100 * self.secours / self.n:.0f} % du temps, {self.changements} changements ; '
             f'leviers interdits utilisés : 0 (tentatives bloquées par le garde-fou : {bloques})',
             '', f'Incidents : {len(self.incidents)}']
        for i, inc in enumerate(self.incidents, 1):
            diag = inc['diag'].most_common(1)[0][0] if inc['diag'] else '—'
            vraie = inc['causes'].most_common(1)[0][0] if inc['causes'] else None
            rep = f'{inc["retabli"] - inc["debut"]} s' if inc['retabli'] is not None else 'NON rétabli'
            if not inc['hors_contrat']:
                rep = '0 s : contrat jamais perdu (panne vue et traitée à temps)'
            elif not inc['actions']:
                rep += ' sans action (la panne a cessé ; aucun levier autorisé ne la réparait)'
            L.append(f'  #{i}  {inc["debut"]:4d}–{inc["fin"]:4d} s   diagnostic : {diag:<11}'
                     + (f' (vraie cause, connue du labo seulement : {vraie})' if vraie else ''))
            L.append(f'       rétabli en {rep}, {inc["hors_contrat"]} s hors contrat ; actions : '
                     + ('; '.join(f'{a} (à {t} s)' for t, a in inc['actions']) or 'aucune'))
        return '\n'.join(L)
