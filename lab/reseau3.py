import statistics
import subprocess
import sys
import time
from subprocess import DEVNULL

from mesure import Lien, Ping, Visio
from topo3 import (COMMUTATEURS, HOTES, LIEN_DELAI, LIEN_MBPS, LIENS, Chemins, _flux, chemins, construire, demarrer, ip,
                   sh)

SERVICES = {'visio': dict(src='h1', dst='h5', mbps=4, port=5201),
            'voix': dict(src='h3', dst='h7', mbps=1, port=5202)}
GENEURS = {'h2': 'h6', 'h4': 'h8'}
PRIORITAIRE_MBPS = 6
LIMITE_KBPS = 5000
DECALAGE = 0.6
LAT_ABSENTE = 1000.0


def chemins_de(service):
    s = SERVICES[service]
    return chemins(HOTES[s['src']], HOTES[s['dst']])


def liens_de(service):
    return sorted({(p[i], p[i + 1]) for p in chemins_de(service) for i in range(len(p) - 1)})


LIENS_MONTANTS = sorted({l for s in SERVICES for l in liens_de(s)})


def poser_files(net):
    for (a, b), d in net.intf.items():
        if a in COMMUTATEURS and b in COMMUTATEURS:
            bw = net.bw
            sh(f'tc qdisc del dev {d} root', verifier=False)
            sh(f'tc qdisc add dev {d} root handle 5: htb default 20')
            sh(f'tc class add dev {d} parent 5: classid 5:1 htb rate {bw}mbit')
            sh(f'tc class add dev {d} parent 5:1 classid 5:10 htb rate {PRIORITAIRE_MBPS}mbit ceil {bw}mbit prio 0')
            sh(f'tc class add dev {d} parent 5:1 classid 5:20 htb rate {bw - PRIORITAIRE_MBPS}mbit ceil {bw}mbit prio 1')
            for parent, handle in (('5:10', '10:'), ('5:20', '20:')):
                sh(f'tc qdisc add dev {d} parent {parent} handle {handle} netem limit 1000 delay {LIEN_DELAI}')


def etat_lien(net, a, b, delai_ms=1.5, gigue_ms=0.0, pertes_pct=0.0):
    reglage = f'netem limit 1000 delay {delai_ms:.1f}ms'
    if gigue_ms >= 0.5:
        reglage += f' {gigue_ms:.1f}ms distribution normal'
    if pertes_pct >= 0.01:
        reglage += f' loss {pertes_pct:.2f}%'
    for parent, handle in (('5:10', '10:'), ('5:20', '20:')):
        sh(f'tc qdisc change dev {net.intf[a, b]} parent {parent} handle {handle} {reglage}')


class Leviers:
    def __init__(self, net):
        self.net, self.ch = net, Chemins(net)
        self.chemin_de = {s: 0 for s in SERVICES}
        self.prio = {s: False for s in SERVICES}
        self.limite = {h: False for h in GENEURS}

    def chemin(self, service, k):
        s = SERVICES[service]
        if k == 0:
            self.ch.retirer(s['src'], s['dst'])
        else:
            self.ch.poser(s['src'], s['dst'], chemins_de(service)[k])
        self.chemin_de[service] = k

    def priorite(self, service, on):
        if on == self.prio[service]:
            return
        s, n = SERVICES[service], 1 + list(SERVICES).index(service)
        for a, b in liens_de(service):
            d = self.net.intf[a, b]
            if on:
                sh(f'tc filter add dev {d} parent 5: protocol ip prio {n} u32 '
                   f'match ip src {ip(s["src"])}/32 match ip dst {ip(s["dst"])}/32 flowid 5:10')
            else:
                sh(f'tc filter del dev {d} parent 5: prio {n}')
        self.prio[service] = on

    def limiter(self, hote, on):
        if on != self.limite[hote]:
            r, b = (LIMITE_KBPS, LIMITE_KBPS // 10) if on else (0, 0)
            d = self.net.intf[HOTES[hote], hote]
            sh(f'ovs-vsctl set interface {d} ingress_policing_rate={r} ingress_policing_burst={b}')
            self.limite[hote] = on

    def reinitialiser(self):
        for s in SERVICES:
            self.chemin(s, 0); self.priorite(s, False)
        for h in GENEURS:
            self.limiter(h, False)


class Sondes:

    def __init__(self, net, service):
        s = SERVICES[service]
        src, dst = s['src'], s['dst']
        self.pings = []
        for k, p in enumerate(chemins_de(service)):
            for (a, b), route in (((src, dst), p), ((dst, src), list(reversed(p)))):
                for i, sw in enumerate(route):
                    suivant = b if i == len(route) - 1 else route[i + 1]
                    _flux(sw, f'priority=300,icmp,nw_src={ip(a)},nw_dst={ip(b)},ip_dscp={k + 1},actions=output:{net.port[sw, suivant]}')
            self.pings.append(Ping(net.get(src), ip(dst), options=('-Q', str((k + 1) << 2))))
        for pg in self.pings:
            pg.start()

    def seconde(self, T):
        out = []
        for pg in self.pings:
            f = pg.fenetre(T - 1, T)
            out.append(statistics.median(f[0]) if f and f[0] else LAT_ABSENTE)
        return out

    def arreter(self):
        for pg in self.pings:
            pg.arreter()


class Service:

    def __init__(self, net, nom, duree_max=3600):
        s = SERVICES[nom]
        src, dst = net.get(s['src']), net.get(s['dst'])
        self.nom = nom
        self.ping = Ping(src, ip(s['dst']))
        self.flux = Visio(src, dst, duree_max, dst=ip(s['dst']), port=s['port'], mbps=s['mbps'])
        self.sondes = Sondes(net, nom)
        self.ping.start(); self.flux.start()

    def seconde(self, T):
        p, v = self.ping.fenetre(T - 1, T), self.flux.seconde(T)
        recus = p[0] if p else []
        return dict(lat=statistics.median(recus) if recus else LAT_ABSENTE,
                    debit=v[0] if v else 0.0, gigue=v[1] if v else 0.0, pertes=v[2] if v else 100.0,
                    sondes=self.sondes.seconde(T))

    def arreter(self):
        self.flux.arreter(); self.ping.arreter(); self.sondes.arreter()


class Reseau3:

    def __init__(self):
        self.net = construire()
        demarrer(self.net)
        poser_files(self.net)
        self.leviers = Leviers(self.net)
        self.services = {s: Service(self.net, s) for s in SERVICES}
        self.liens = {l: Lien(LIEN_MBPS, self.net.intf[l]) for l in LIENS_MONTANTS}
        self.procs = []
        time.sleep(4)
        self.T = round(time.time())
        for l in self.liens.values():
            l.echantillon()

    def tick(self):
        self.T += 1
        retard = time.time() - (self.T + DECALAGE)
        if retard > 5:
            raise RuntimeError(f"saut d'horloge de {retard:.0f} s (mise en veille du PC ?) : session abandonnée")
        time.sleep(max(0, -retard))
        m = {s: sv.seconde(self.T) for s, sv in self.services.items()}
        m['liens'] = {l: ln.echantillon() for l, ln in self.liens.items()}
        return m

    def gener(self, hote, mbps, duree, tcp=0):
        dst = GENEURS[hote]
        self.n_gene = getattr(self, 'n_gene', 0) + 1
        port = 5300 + self.n_gene % 400
        self.procs.append(self.net.get(dst).popen(['iperf3', '-s', '-1', '-p', str(port)], stdout=DEVNULL, stderr=DEVNULL))
        time.sleep(0.3)
        debit = ['-P', str(tcp)] if tcp else ['-u', '-b', f'{mbps}M', '-l', '1200']
        self.procs.append(self.net.get(hote).popen(['iperf3', '-c', ip(dst), '-p', str(port), '-t', str(duree)] + debit,
                                                   stdout=DEVNULL, stderr=DEVNULL))

    def calmer(self):
        procs, self.procs = self.procs, []
        for p in procs:
            if p.poll() is None:
                p.terminate()
        for p in procs:
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()

    def arreter(self):
        self.calmer()
        for sv in self.services.values():
            sv.arreter()
        subprocess.run(['pkill', '-x', 'iperf3'], stdout=DEVNULL, stderr=DEVNULL)
        self.net.stop()


def tester():
    from mininet.log import setLogLevel
    setLogLevel('warning')
    r = Reseau3()
    lv = r.leviers
    etapes = [
        (8, 'calme', lambda: None),
        (12, 'h2 envoie 25 Mb/s (route normale : a1→d1→c1→a3)', lambda: r.gener('h2', 25, 60)),
        (10, '+ priorité visio', lambda: lv.priorite('visio', True)),
        (10, '+ voix passée par d2→c2', lambda: lv.chemin('voix', 3)),
        (10, 'h2 bridé, priorité ôtée, voix remise sur la route normale',
         lambda: (lv.limiter('h2', True), lv.priorite('visio', False), lv.chemin('voix', 0))),
        (10, 'lien d1→c1 abîmé (+80 ms, 3 % de pertes)', lambda: etat_lien(r.net, 'd1', 'c1', 81.5, 0, 3)),
        (10, 'visio passée par d2→c2 (la voix reste sur d1→c1)', lambda: lv.chemin('visio', 3)),
    ]
    try:
        print(f'{"étape":<58}{"visio : lat / pertes / débit":>30}{"voix : lat / pertes / débit":>30}   sondes visio (4 chemins)   occupation a1→d1, d1→c1, d2→c2')
        for duree, titre, action in etapes:
            action()
            mes = [r.tick() for _ in range(duree)][3:]
            ligne = f'{titre:<58}'
            for s in SERVICES:
                med = lambda k: statistics.median(m[s][k] for m in mes)
                ligne += f'{med("lat"):>12.1f} ms {med("pertes"):>5.1f} % {med("debit"):>5.2f} Mb'
            sondes = [statistics.median(m['visio']['sondes'][k] for m in mes) for k in range(4)]
            occ = [statistics.median(m['liens'][l][0] for m in mes if m['liens'][l]) for l in (('a1', 'd1'), ('d1', 'c1'), ('d2', 'c2'))]
            print(ligne + '   ' + ' '.join(f'{x:6.1f}' for x in sondes) + '      ' + ' '.join(f'{x:5.0f} %' for x in occ), flush=True)
    finally:
        r.arreter()


if __name__ == '__main__':
    if '--test' in sys.argv:
        tester()
