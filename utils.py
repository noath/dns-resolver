import binascii
import ipaddress
import socket
from enum import Enum


class Section(Enum):
    HEADER = 0
    QUESTION = 1
    ANSWER = 2
    AUTHORITY = 3
    ADDITIONAL = 4

class QType(Enum):
    A = 0
    NS = 1
    AAAA = 28

class Site:
    def __init__(self, url="", ip="", ttl=0):
        self.ip = ip
        self.url = url
        self.ttl = ttl

    def __repr__(self):
        return f"{self.url} {self.ip}"

    def __str__(self):
        return f"{self.url} {self.ip}"

def send_udp_message(message, address, port):
    message = message.replace(" ", "").replace("\n", "")
    ip = ipaddress.ip_address(address)
    if ip.version == 6:
        server_address = (address, port)
    elif ip.version == 4:
        server_address = (address, port)

    if socket.has_dualstack_ipv6() and ip.version == 6:
        sock = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    else:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.sendto(binascii.unhexlify(message), server_address)
        data, _ = sock.recvfrom(4096)
    finally:
        sock.close()
    return binascii.hexlify(data).decode("utf-8")

def make_dns_query(url, qtype):
    # HEADER
    message = "AA AA 00 00 00 01 00 00 00 00 00 00 "

    # QNAME
    url_sections = url.split(".")
    for section in url_sections:
        message += f"{hex(len(section))[2:]:0>2} "
        for symb in section:
            message += str(format(ord(symb), "x"))
            message += " "
    message += "00 "

    # QTYPE
    if qtype is QType.A:
        message += "00 01 "
    elif qtype is QType.NS:
        message += "00 02 "
    elif qtype is QType.AAAA:
        message += "00 1c "
    else:
        message += "00 01 "
    # QCLASS
    message += "00 01 "  # IN
    return message

def check_IPv6_support(dns_port):
    try:
        message = make_dns_query("google.com", QType.AAAA)
        _ = send_udp_message(message, "2001:4860:4860::8888", dns_port)
    except Exception as e:
        return False
    return True
