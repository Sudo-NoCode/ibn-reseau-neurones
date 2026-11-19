import csv
import math
import re
import statistics
import subprocess
import sys
import threading
import time
from subprocess import DEVNULL, PIPE, STDOUT

from scenarios import VISIO_MBPS, Moteur
from topo import GOULOT_IF, construire

PING_INTERVALLE = 0.2
GRACE_S = 3
VISIO_PORT = '5201'

COLONNES = [
    'session', 'graine', 'bw_mbps', 'sec', 't',
    'lat_moy_ms', 'lat_med_ms', 'lat_max_ms', 'lat_pertes_pct',
    'visio_debit_mbps', 'visio_gigue_ms', 'visio_pertes_pct',
    'lien_occup_pct', 'file_paquets', 'file_jetes',
    'scenario', 'secondaire', 'pic', 'transition', 'sc_age',
    'netem_delai', 'netem_gigue', 'netem_pertes', 'cong_mbps', 'cong_mode', 'fond_mbps',
]

RE_PING = re.compile(r'^\[([\d.]+)\].*icmp_seq=(\d+).*time=([\d.]+) ms')
RE_IPERF = re.compile(r'\[\s*\d+\]\s+([\d.]+)-([\d.]+)\s+sec\s+[\d.]+\s+\w?Bytes\s+([\d.]+)\s+(\w?)bits/sec'
                      r'\s+([\d.]+)\s+ms\s+(-?\d+)/(\d+)\s+\(')


class Ping(threading.Thread):

    def __init__(self, h1, dst='10.0.0.3', options=()):
        super().__init__(daemon=True)
        self.proc = h1.popen(['stdbuf', '-oL', 'ping', '-D', '-n', '-i', str(PING_INTERVALLE), *options, dst],
                             stdout=PIPE, stderr=DEVNULL, universal_newlines=True)
        self.rtt = {}
        self.ancre = None
        self.lock = threading.Lock()

    def run(self):
        for ligne in self.proc.stdout:
            m = RE_PING.match(ligne)
            if m:
                ts, seq, rtt = float(m.group(1)), int(m.group(2)), float(m.group(3))
                with self.lock:
                    self.rtt[seq] = rtt
                    self.ancre = (seq, ts - rtt / 1000)

    def fenetre(self, debut, fin):
        with self.lock:
            if self.ancre is None:
                return None
            seq_a, envoi_a = self.ancre
            s_lo = math.ceil(seq_a + (debut - envoi_a) / PING_INTERVALLE - 1e-6)
            s_hi = math.ceil(seq_a + (fin - envoi_a) / PING_INTERVALLE - 1e-6)
            seqs = [s for s in range(s_lo, s_hi) if s >= 1]
            recus = [self.rtt.pop(s) for s in seqs if s in self.rtt]
            for s in [s for s in self.rtt if s < s_lo - 50]:
                del self.rtt[s]
        if not seqs:
            return None
        return recus, len(seqs)

    def arreter(self):
        self.proc.terminate()


class Visio(threading.Thread):

    def __init__(self, h1, h3, duree_max, dst='10.0.0.3', port=VISIO_PORT, mbps=VISIO_MBPS):
        super().__init__(daemon=True)
        self.h1, self.h3, self.duree_max = h1, h3, duree_max
        self.dst, self.port, self.mbps = dst, str(port), mbps
        self.par_seconde = {}
        self.lock = threading.Lock()
        self.stop_evt = threading.Event()
        self.relances = 0
        self.srv = self.h3.popen(['iperf3', '-s', '-p', self.port, '-i', '1', '--forceflush'],
                                 stdout=PIPE, stderr=STDOUT, universal_newlines=True)
        self.debut_test = None
        self.mur0, self.mono0 = time.time(), time.monotonic()
        self.lecteur = threading.Thread(target=self._lire, daemon=True)
        self.lecteur.start()

    def _lire(self):
        for ligne in self.srv.stdout:
            m = RE_IPERF.search(ligne)
            if not m or 'receiver' in ligne or 'sender' in ligne:
                if 'Accepted connection' in ligne:
                    self.debut_test = None
                continue
            a, b = float(m.group(1)), float(m.group(2))
            if b - a > 1.5:
                continue
            mur, mono = time.time(), time.monotonic()
            if self.debut_test is None or abs((mur - self.mur0) - (mono - self.mono0)) > 1.0:
                self.debut_test = mur - b
                self.mur0, self.mono0 = mur, mono
            fin_abs = self.debut_test + round(b)
            unite = {'K': 1e-3, 'M': 1.0, 'G': 1e3, '': 1e-6}[m.group(4)]
            perdus, total = max(0, int(m.group(6))), int(m.group(7))
            with self.lock:
                self.par_seconde[round(fin_abs)] = (
                    float(m.group(3)) * unite, float(m.group(5)),
                    100.0 * perdus / total if total else 100.0)

    def run(self):
        time.sleep(0.5)
        while not self.stop_evt.is_set():
            cli = self.h1.popen(['iperf3', '-c', self.dst, '-p', self.port, '-u', '-b', f'{self.mbps}M',
                                 '-t', str(self.duree_max), '-l', '1200'], stdout=DEVNULL, stderr=DEVNULL)
            while cli.poll() is None and not self.stop_evt.wait(0.5):
                pass
            if cli.poll() is None:
                cli.terminate()
                cli.wait()
            elif not self.stop_evt.is_set():
                self.relances += 1
                self.stop_evt.wait(1.0)

    def seconde(self, fin_abs):
        with self.lock:
            return self.par_seconde.pop(round(fin_abs), None)

    def arreter(self):
        self.stop_evt.set()
        self.join(5)
        self.srv.terminate()
        self.srv.wait()


class Lien:

    def __init__(self, bw_mbps, interface=GOULOT_IF):
        self.bw = bw_mbps
        self.interface = interface
        self.prec = None

    def _lire(self):
        with open(f'/sys/class/net/{self.interface}/statistics/tx_bytes') as f:
            tx = int(f.read())
        stats = subprocess.run(['tc', '-s', 'qdisc', 'show', 'dev', self.interface],
                               capture_output=True, text=True).stdout
        blocs = [b for b in stats.split('qdisc ')[1:] if b.startswith('netem')]
        jetes = sum(int(re.search(r'dropped (\d+)', b).group(1)) for b in blocs)
        file = sum(int(re.search(r'backlog \S+ (\d+)p', b).group(1)) for b in blocs)
        return time.time(), tx, jetes, file

    def echantillon(self):
        t, tx, jetes, file = self._lire()
        res = None
        if self.prec:
            t0, tx0, jetes0, _ = self.prec
            mbps = (tx - tx0) * 8 / 1e6 / (t - t0)
            res = (round(100 * mbps / self.bw, 1), file, jetes - jetes0)
        self.prec = (t, tx, jetes, file)
        return res


class SautHorloge(Exception):
    pass


def _r(x, n=2):
    return '' if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, n)


def collecter(net, moteur, duree_s, chemin_csv, session, graine, bw_mbps, journal=None):
    h1, h3 = net.get('h1', 'h3')
    ping = Ping(h1)
    ping.start()
    visio = Visio(h1, h3, duree_s + 120)
    visio.start()
    lien = Lien(bw_mbps)
    moteur.start()
    moteur.pret.wait(10)
    time.sleep(2)

    t0 = math.floor(time.time()) + 1
    mur0, mono0 = time.time(), time.monotonic()
    attente = {}
    lignes = 0
    lien.echantillon()
    try:
        with open(chemin_csv, 'w', newline='') as f:
            w = csv.DictWriter(f, fieldnames=COLONNES)
            w.writeheader()
            lignes = _boucle(w, f, t0, mur0, mono0, duree_s, attente, lien, moteur, ping, visio,
                             session, graine, bw_mbps, journal)
    finally:
        moteur.arreter()
        moteur.join(15)
        visio.arreter()
        ping.arreter()
    return lignes, visio.relances


def _boucle(w, f, t0, mur0, mono0, duree_s, attente, lien, moteur, ping, visio, session, graine, bw_mbps, journal):
    lignes = 0
    k = 0
    while True:
        derive = (time.time() - mur0) - (time.monotonic() - mono0)
        if abs(derive) > 1.0:
            raise SautHorloge(f'horloge décalée de {derive:+.1f} s à la seconde {k}')
        etat_milieu = None
        if k < duree_s:
            pause = t0 + k + 0.5 - time.time()
            if pause > 0:
                time.sleep(pause)
            etat_milieu = moteur.etat()
        pause = t0 + k + 1 - time.time()
        if pause > 0:
            time.sleep(pause)
        if k < duree_s:
            attente[k] = (lien.echantillon(), etat_milieu)
        for j in sorted(x for x in attente if t0 + x + 1 + GRACE_S <= time.time()):
            l_ech, etat = attente.pop(j)
            debut = t0 + j
            p = ping.fenetre(debut, debut + 1)
            v = visio.seconde(debut + 1)
            ligne = dict(session=session, graine=graine, bw_mbps=bw_mbps, sec=j, t=debut)
            if p:
                recus, envoyes = p
                ligne.update(lat_moy_ms=_r(statistics.fmean(recus)) if recus else '',
                             lat_med_ms=_r(statistics.median(recus)) if recus else '',
                             lat_max_ms=_r(max(recus)) if recus else '',
                             lat_pertes_pct=_r(100 * (envoyes - len(recus)) / envoyes, 1))
            if v:
                ligne.update(visio_debit_mbps=_r(v[0], 3), visio_gigue_ms=_r(v[1], 3), visio_pertes_pct=_r(v[2], 2))
            if l_ech:
                ligne.update(lien_occup_pct=l_ech[0], file_paquets=l_ech[1], file_jetes=l_ech[2])
            ligne.update({c: etat[c] for c in COLONNES if c in etat})
            w.writerow(ligne)
            lignes += 1
        f.flush()
        if journal and k % 30 == 0 and k < duree_s:
            journal(f'{k}/{duree_s} s  scénario={moteur.etat()["scenario"]}')
        k += 1
        if k >= duree_s and not attente:
            break
    return lignes


def controle(chemin):
    import pandas as pd
    df = pd.read_csv(chemin)
    print(f'\nlignes : {len(df)}   secondes manquantes : {df.sec.max() + 1 - len(df)}')
    print('valeurs vides par colonne de mesure (%) :')
    mesures = ['lat_med_ms', 'lat_pertes_pct', 'visio_debit_mbps', 'visio_gigue_ms', 'visio_pertes_pct', 'lien_occup_pct']
    print((df[mesures].isna().mean() * 100).round(1).to_string())
    print('\nmédianes par scénario :')
    print(df.groupby('scenario')[mesures + ['file_paquets']].median().round(2).to_string())

    print('\nalignement : hors congestion, latence mesurée - (réglage netem + 5 ms), décalée de k secondes')
    calme = df[(df.cong_mbps == 0) & (df.secondaire.fillna('') != 'congestion')]
    for lag in (-1, 0, 1):
        ecart = (df.lat_med_ms.shift(-lag) - (df.netem_delai + 5)).loc[calme.index].abs()
        print(f'  k={lag:+d} s : écart médian {ecart.median():5.2f} ms   écart > 10 ms sur {100 * (ecart > 10).mean():4.1f} % des secondes')
    return df


if __name__ == '__main__':
    if '--test' in sys.argv:
        from mininet.log import setLogLevel
        setLogLevel('warning')
        duree = int(sys.argv[2]) if len(sys.argv) > 2 else 150
        net = construire()
        net.start()
        try:
            m = Moteur(net, graine=3, bw_mbps=20, duree_s=duree + 30, duree_scenario=(15, 30))
            n, relances = collecter(net, m, duree, '/tmp/test_mesure.csv', 'test', 3, 20, journal=print)
            print(f'\n{n} lignes écrites, flux visio relancé {relances} fois')
        finally:
            subprocess.run(['pkill', '-x', 'iperf3'], stdout=DEVNULL, stderr=DEVNULL)
            net.stop()
        controle('/tmp/test_mesure.csv')
    else:
        print("usage : voir README.md")
