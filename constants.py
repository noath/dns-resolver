from utils import Site


APP_PORT = 5000
DNS_PORT = 53
MAX_STEPS = 16
ROOT_SERVERS = (
    Site("a.root-servers.net", "198.41.0.4"),
    Site("b.root-servers.net", "199.9.14.201"),
    Site("c.root-servers.net", "192.33.4.12"),
    Site("d.root-servers.net", "199.7.91.13"),
    Site("e.root-servers.net", "192.203.230.10"),
    Site("f.root-servers.net", "192.5.5.241"),
    Site("g.root-servers.net", "192.112.36.4"),
    Site("h.root-servers.net", "198.97.190.53"),
    Site("i.root-servers.net", "192.36.148.17"),
    Site("j.root-servers.net", "192.58.128.30"),
    Site("k.root-servers.net", "193.0.14.129"),
    Site("l.root-servers.net", "199.7.83.42"),
    Site("m.root-servers.net", "202.12.27.33"),
)
LOG_PATH = 'resolve.log'
