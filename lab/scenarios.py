import random
import subprocess
import sys
import threading
import time
from subprocess import DEVNULL

from topo import GOULOT_DELAI, GOULOT_IF, construire

SCENARIOS = ['normal', 'congestion', 'degrade', 'instable', 'panne']
PROBLEMES = SCENARIOS[1:]
VISIO_MBPS = 4
PORTS = range(5300, 5500)


class Moteur(threading.Thread):
    P_COMBO = 0.2

    def __init__(self, net, graine, bw_mbps, duree_s, duree_scenario=(30, 120),
                 combos=True, pics=True, fond=True, netem_cibles=(('5:1', '10:'),), progressif=False):
        super().__init__(daemon=True)
        self.progressif = progressif
        self.netem_cibles = netem_cibles
        self.rng = random.Random(graine)
        self.rng_fond = random.Random(graine + 1)
        self.bw = bw_mbps
        self.duree = duree_s
        self.duree_scenario = duree_scenario
        self.combos, self.pics, self.fond = combos, pics, fond
        self.h1, self.h2, self.h3, self.h4 = net.get('h1', 'h2', 'h3', 'h4')
        self.stop_evt = threading.Event()
        self.pret = threading.Event()
        self.lock = threading.Lock()
        self.procs = []
        self.flux_fond = []
        self.fil_fond = None
        self._port = 0
        self.base = self.rng.uniform(3, 8)
        self._etat = dict(scenario='normal', secondaire='', pic=0, transition='',
                          netem_delai=0.0, netem_gigue=0.0, netem_pertes=0.0,
                          cong_mbps=0.0, cong_mode='', fond_mbps=0.0, sc_debut=time.time())

    def etat(self):
        with self.lock:
            d = dict(self._etat)
        d['sc_age'] = round(time.time() - d.pop('sc_debut'), 1)
        return d

    def _maj(self, **kw):
        with self.lock:
            self._etat.update(kw)

    def _netem(self, delai, gigue=0.0, pertes=0.0):
        gigue = gigue if gigue >= 0.5 else 0.0
        pertes = pertes if pertes >= 0.01 else 0.0
        reglage = ['netem', 'limit', '1000', 'delay', f'{delai:.1f}ms']
        if gigue:
            reglage += [f'{gigue:.1f}ms', 'distribution', 'normal']
        if pertes:
            reglage += ['loss', f'{pertes:.2f}%']
        for parent, handle in self.netem_cibles:
            subprocess.run(['tc', 'qdisc', 'change', 'dev', GOULOT_IF, 'parent', parent, 'handle', handle] + reglage,
                           check=True, stdout=DEVNULL, stderr=subprocess.PIPE)
        self._maj(netem_delai=round(delai, 1), netem_gigue=round(gigue, 1), netem_pertes=round(pertes, 2))

    def _prochain_port(self):
        with self.lock:
            self._port = (self._port + 1) % len(PORTS)
            return PORTS[self._port]

    def _flux(self, src, dst, mbps, duree, tcp=False, paralleles=1):
        if self.stop_evt.is_set():
            return None
        port = str(self._prochain_port())
        srv = dst.popen(['iperf3', '-s', '-1', '-p', port], stdout=DEVNULL, stderr=DEVNULL)
        time.sleep(0.3)
        args = ['iperf3', '-c', dst.IP(), '-p', port, '-t', str(max(1, round(duree)))]
        args += ['-P', str(paralleles)] if tcp else ['-u', '-b', f'{mbps:.2f}M']
        with self.lock:
            if self.stop_evt.is_set():
                srv.terminate()
                srv.wait()
                return None
            cli = src.popen(args, stdout=DEVNULL, stderr=DEVNULL)
            self.procs = [p for p in self.procs if p.poll() is None] + [srv, cli]
        return cli

    def _congestion_progressive(self, duree, rampe):
        r = self.rng
        n = r.randint(3, 6)
        if r.random() < 0.6:
            total = r.uniform(0.8, 1.5) * self.bw
            mode, part = 'udp_rampe', total / n
            lancer = lambda k: (self._flux(self.h2, self.h4, part, duree - k * rampe / n + 1), part * (k + 1))
        else:
            mode = f'tcp_rampe{n}'
            lancer = lambda k: (self._flux(self.h2, self.h4, 0, duree - k * rampe / n + 1, tcp=True), float(self.bw))
        self._maj(cong_mode=mode)
        return [(k * rampe / n, (lambda k=k: lancer(k))) for k in range(n)]

    def _congestion(self, duree):
        r = self.rng
        if r.random() < 0.6:
            mbps = r.uniform(0.7, 1.5) * self.bw
            proc, mode = self._flux(self.h2, self.h4, mbps, duree), 'udp'
        else:
            n = r.randint(1, 4)
            proc, mode, mbps = self._flux(self.h2, self.h4, 0, duree, tcp=True, paralleles=n), f'tcp{n}', float(self.bw)
        self._maj(cong_mbps=round(mbps, 1), cong_mode=mode)
        return proc

    def _pic(self, base):
        r = self.rng
        duree = r.uniform(2, 5)
        self._maj(pic=1)
        if r.random() < 0.5:
            self._netem(base + r.uniform(10, 40), r.uniform(0, 3))
            self.stop_evt.wait(duree)
            self._netem(base)
        else:
            self._flux(self.h2, self.h4, r.uniform(0.4, 0.7) * self.bw, duree)
            self.stop_evt.wait(duree)
        self._maj(pic=0)

    def _boucle_fond(self):
        paires = [(self.h2, self.h4), (self.h2, self.h3), (self.h1, self.h4),
                  (self.h4, self.h2), (self.h3, self.h1)]
        r = self.rng_fond
        while not self.stop_evt.wait(r.uniform(2, 6)):
            self.flux_fond = [(p, m) for p, m in self.flux_fond if p.poll() is None]
            if len(self.flux_fond) < 3:
                src, dst = r.choice(paires)
                mbps = r.uniform(0.2, 0.1 * self.bw)
                proc = self._flux(src, dst, mbps, r.uniform(3, 15))
                if proc is not None:
                    self.flux_fond.append((proc, mbps))
            self._maj(fond_mbps=round(sum(m for _, m in self.flux_fond), 2))

    def jouer(self, nom, duree):
        r = self.rng
        base = max(1.0, self.base + r.uniform(-1, 1))
        cible = (base, r.uniform(0, 1), 0.0)
        rampe = 0.0
        cong = None
        if nom == 'degrade':
            cible = (r.uniform(30, 150), r.uniform(0, 3), r.uniform(0.5, 8))
            rampe = r.uniform(5, 15) if r.random() < 0.5 else 0.0
        elif nom == 'instable':
            cible = (r.uniform(10, 40), r.uniform(5, 30), r.uniform(0, 0.5))
            rampe = r.uniform(5, 15) if r.random() < 0.5 else 0.0
        elif nom == 'panne':
            cible = (r.uniform(40, 200), r.uniform(2, 10), r.uniform(5, 15))
            rampe = duree
        rampe_cong = 0.0
        if self.progressif:
            if nom in ('degrade', 'instable'):
                rampe = r.uniform(10, 40) if r.random() < 0.85 else 0.0
            rampe_cong = min(r.uniform(10, 40), 0.7 * duree) if r.random() < 0.85 else 0.0
        combo = self.combos and r.random() < self.P_COMBO
        secondaire = ''
        if combo and nom == 'congestion':
            cible = (base + r.uniform(5, 20), r.uniform(5, 20), 0.0)
            secondaire = 'instable'
        elif combo and nom in ('degrade', 'instable'):
            secondaire = 'congestion'

        self._maj(scenario=nom, secondaire=secondaire, pic=0, cong_mbps=0.0, cong_mode='',
                  transition='progressive' if (rampe > 0 or (nom == 'congestion' and rampe_cong > 0)) else 'brusque',
                  sc_debut=time.time())
        self.pret.set()
        paliers, cong_procs = [], []
        if nom == 'congestion' or secondaire == 'congestion':
            if rampe_cong > 0:
                paliers = self._congestion_progressive(duree, rampe_cong)
            else:
                cong = self._congestion(duree)
        pics = []
        if nom == 'normal' and self.pics and duree > 12:
            pics = sorted(r.uniform(2, duree - 6) for _ in range(r.randint(1, 3)))

        debut = time.time()
        dernier = None
        while not self.stop_evt.is_set():
            t = time.time() - debut
            if t >= duree:
                break
            while paliers and t >= paliers[0][0]:
                proc, mbps = paliers.pop(0)[1]()
                cong_procs.append(proc)
                self._maj(cong_mbps=round(mbps, 1))
            if pics and t >= pics[0]:
                pics.pop(0)
                self._pic(base)
                dernier = None
                continue
            frac = 1.0 if rampe <= 0 else min(1.0, t / rampe)
            valeurs = (round(base + (cible[0] - base) * frac, 1),
                       round(cible[1] * frac, 1),
                       round(cible[2] * frac, 2))
            if valeurs != dernier:
                self._netem(*valeurs)
                dernier = valeurs
            self.stop_evt.wait(0.5 if paliers else 1.0)

        for p in [cong] + cong_procs:
            if p is not None and p.poll() is None:
                p.terminate()
        self._maj(cong_mbps=0.0, cong_mode='')

    def run(self):
        t0 = time.time()
        if self.fond:
            self.fil_fond = threading.Thread(target=self._boucle_fond, daemon=True)
            self.fil_fond.start()
        try:
            while not self.stop_evt.is_set():
                sac = PROBLEMES[:]
                self.rng.shuffle(sac)
                for probleme in sac:
                    for nom in ('normal', probleme):
                        reste = self.duree - (time.time() - t0)
                        if reste <= 1 or self.stop_evt.is_set():
                            return
                        self.jouer(nom, min(self.rng.uniform(*self.duree_scenario), reste))
        finally:
            self.nettoyer()

    def arreter(self):
        self.stop_evt.set()

    def nettoyer(self):
        self.stop_evt.set()
        if self.fil_fond is not None and self.fil_fond is not threading.current_thread():
            self.fil_fond.join(10)
        with self.lock:
            procs, self.procs = self.procs, []
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        subprocess.run(['pkill', '-f', 'iperf3 .*-p 5[34]'], stdout=DEVNULL, stderr=DEVNULL)
        for parent, handle in self.netem_cibles:
            subprocess.run(['tc', 'qdisc', 'change', 'dev', GOULOT_IF, 'parent', parent, 'handle', handle,
                            'netem', 'limit', '1000', 'delay', GOULOT_DELAI], stdout=DEVNULL, stderr=DEVNULL)


def _ping(h1):
    sortie = h1.cmd('ping -q -c 30 -i 0.3 10.0.0.3')
    perte = float(sortie.split('% packet loss')[0].split()[-1]) if '% packet loss' in sortie else -1
    rtt = mdev = float('nan')
    if 'rtt' in sortie:
        vals = sortie.split('=')[-1].split()[0].split('/')
        rtt, mdev = float(vals[1]), float(vals[3])
    return rtt, mdev, perte


def tester():
    from mininet.log import setLogLevel
    setLogLevel('warning')
    net = construire()
    net.start()
    try:
        m = Moteur(net, graine=1, bw_mbps=20, duree_s=0, combos=False, pics=False, fond=False)
        h1 = net.get('h1')
        print(f"{'scénario':<12}{'RTT moy':>10}{'gigue':>9}{'pertes':>9}   paramètres tirés")
        for nom in SCENARIOS + ['pic']:
            if nom == 'pic':
                th = threading.Thread(target=m._pic, args=(m.base,))
            else:
                th = threading.Thread(target=m.jouer, args=(nom, 16))
            th.start()
            time.sleep(0.5 if nom == 'pic' else 4)
            rtt, mdev, perte = _ping(h1)
            e = m.etat()
            th.join()
            params = f"delai={e['netem_delai']} gigue={e['netem_gigue']} pertes={e['netem_pertes']}% cong={e['cong_mbps']}{e['cong_mode']}"
            print(f"{nom:<12}{rtt:>8.1f}ms{mdev:>7.1f}ms{perte:>8.1f}%   {params}")
            m.nettoyer()
            m.stop_evt.clear()
            time.sleep(1)
    finally:
        m.nettoyer()
        net.stop()


if __name__ == '__main__':
    if '--test' in sys.argv:
        tester()
    else:
        print("usage : voir README.md")
