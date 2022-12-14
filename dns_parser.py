import logging
import socket
from utils import Section, Site, QType, make_dns_query, send_udp_message


def parse_rr_name(resp, begin):
    i = end = begin
    link = False
    was_new_section = False
    name = ""
    while True:
        byte = resp[i : i + 2]
        i += 2
        if byte == "00":
            break
        if int(byte, 16) >= int("c0", 16):  # offset
            offset = int(resp[i - 2 : i + 2], 16) - int("c000", 16)
            i = 2 * offset
            if not link and was_new_section:
                end += 2
            link = True
            continue
        else:
            was_new_section = True

        url_section_len = int(byte, 16)
        for _ in range(url_section_len):
            byte = resp[i : i + 2]
            symb = bytes.fromhex(byte).decode("utf-8")
            name += symb
            if not link:
                end = i
            i += 2

        name += "."

    if len(name) > 0 and name[-1] == ".":
        name = name[:-1]
    return name, end

def parse_rr(resp, begin, section):
    data = {}
    name, end = parse_rr_name(resp, begin)
    begin = end
    data["NAME"] = name
    data["TYPE"] = int(resp[begin + 4 : begin + 8], 16)
    data["CLASS"] = int(resp[begin + 8 : begin + 12], 16)
    data["TTL"] = int(resp[begin + 12 : begin + 20], 16)
    data["RDLENGTH"] = int(resp[begin + 20 : begin + 24], 16)
    rr_end = begin + 24 + data["RDLENGTH"] * 2

    hex_rddata = resp[begin + 24 : rr_end]
    if section is Section.ANSWER:
        if data["TYPE"] == 1:
            if data["RDLENGTH"] == 4:
                data["RDDATA"] = socket.inet_ntop(
                    socket.AF_INET, bytearray.fromhex(hex_rddata)
                )
            elif data["RDLENGTH"] == 16:
                data["RDDATA"] = socket.inet_ntop(
                    socket.AF_INET6, bytearray.fromhex(hex_rddata)
                )
        elif data["TYPE"] == 2 or data["TYPE"] == 16:
            data["RDDATA"], _ = parse_rr_name(resp, begin + 24)
        elif data["TYPE"] == 28:
            data["RDDATA"] = socket.inet_ntop(
                socket.AF_INET6, bytearray.fromhex(hex_rddata)
            )
    elif section is Section.AUTHORITY:
        data["RDDATA"], _ = parse_rr_name(resp, begin + 24)
    elif section is Section.ADDITIONAL:
        if data["RDLENGTH"] == 4:
            data["RDDATA"] = socket.inet_ntop(
                socket.AF_INET, bytearray.fromhex(hex_rddata)
            )
        elif data["RDLENGTH"] == 16:
            data["RDDATA"] = socket.inet_ntop(
                socket.AF_INET6, bytearray.fromhex(hex_rddata)
            )
        else:
            data["RDDATA"] = hex_rddata
    else:
        logging.error("Invalid section for RDDATA while RR parsing")
        raise Exception("Invalid section for RDDATA while RR parsing")

    return data, rr_end

def parse_dns_response(resp):
    resp = resp.replace(" ", "").replace("\n", "")
    data = {}

    flags = bin(int(resp[4:8], 16))[2:].zfill(16)
    data["HEADER"] = {
        "ID": int(resp[:4], 16),
        "QR": int(flags[0], 2),
        "Opcode": "%0*X" % (1, int(flags[1:5], 2)),
        "AA": int(flags[5], 2),
        "TC": int(flags[6], 2),
        "RD": int(flags[7], 2),
        "RA": int(flags[8], 2),
        "Z": flags[9:12],  # ignoring, must be zeros
        "RCODE": "%0*X" % (1, int(flags[12:16], 2)),
        "QDCOUNT": int(resp[8:12], 16),
        "ANCOUNT": int(resp[12:16], 16),
        "NSCOUNT": int(resp[16:20], 16),
        "ARCOUNT": int(resp[20:24], 16),
    }

    # QUESTION (skip)
    qname_end = resp.find("00", 24, len(resp))
    if qname_end == -1:
        logging.error("Invalid header response.")
        raise Exception("Invalid header response.")
    q_end = 2 + qname_end + 8  # after qtype and qclass both

    # ANSWER
    data["ANSWER"] = []
    rr_end = q_end
    for _ in range(data["HEADER"]["ANCOUNT"]):
        rr, rr_end = parse_rr(resp, rr_end, Section.ANSWER)
        data["ANSWER"].append(rr)

    # AUTHORITY
    data["AUTHORITY"] = []
    for _ in range(data["HEADER"]["NSCOUNT"]):
        rr, rr_end = parse_rr(resp, rr_end, Section.AUTHORITY)
        data["AUTHORITY"].append(rr)

    # ADDITIONAL
    data["ADDITIONAL"] = []
    for _ in range(data["HEADER"]["ARCOUNT"]):
        rr, rr_end = parse_rr(resp, rr_end, Section.ADDITIONAL)
        data["ADDITIONAL"].append(rr)

    return data

def resolve_step(domain, node, root, trace, steps, qtype, dns_port, max_steps):
    trace_repr = ' -> '.join(map(repr, trace))
    logging.info(
        f"Resolving step for domain {domain}, current trace is: {trace_repr}"
    )
    if steps > max_steps:
        return None
    trace.append(node)
        
    message = make_dns_query(domain, qtype=qtype)
    try:
        response = send_udp_message(message, node.ip, dns_port)
    except OSError as e:
        logging.error(f"Error occured while sending udp message: {e}")
        return None

    data = parse_dns_response(response)
    if data["HEADER"]["RCODE"] == '3': # NXDomain
        logging.info(f"Got NXDomain while resolving domain {domain}")
        return None

    if data["ANSWER"] and data["HEADER"]["AA"]:
        return [Site(RR["NAME"], RR["RDDATA"], RR["TTL"]) for RR in data["ANSWER"]]

    additional_info = {}
    for add_rr in data["ADDITIONAL"]:
        correct_IPv4 = add_rr["RDLENGTH"] == 4 and qtype is QType.A
        correct_IPv6 = add_rr["RDLENGTH"] == 16 and qtype is QType.AAAA
        if correct_IPv4 or correct_IPv6:
            additional_info.update({add_rr["NAME"]: add_rr["RDDATA"]})

    for ns_rr in data["AUTHORITY"]:
        ns_name = ns_rr["RDDATA"]
        inner_steps = 0
        if ns_name in additional_info:
            next_node = Site(ns_name, additional_info[ns_name])
            next_nodes = [next_node]
        else:
            next_nodes = resolve_step(ns_name, root, root, trace, steps + 1, qtype, dns_port, max_steps)
            inner_steps = 1
        if next_nodes is not None:
            for next_node in next_nodes:
                if next_node is not None:
                    res_nodes = resolve_step(
                        domain, next_node, root, trace, steps + 1 + inner_steps, qtype, dns_port, max_steps 
                    )
                    if res_nodes is not None:
                        return res_nodes
    return None


{'HEADER': {'ID': 43690, 'QR': 1, 'Opcode': '0', 'AA': 0, 'TC': 0, 'RD': 0, 'RA': 0, 'Z': '000', 'RCODE': '0', 'QDCOUNT': 1, 'ANCOUNT': 0, 'NSCOUNT': 5, 'ARCOUNT': 10}, 'ANSWER': [], 'AUTHORITY': [{'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': 'a.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'b.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'd.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'e.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'f.dns.ripn.net'}], 'ADDITIONAL': [{'NAME': 'a.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.128.6'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.85.252.62'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.190.124.17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.142.17'}, {'NAME': 'f.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.156.17'}, {'NAME': 'a.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:17:0:193:232:128:6'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:16:0:194:85:252:62'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:18:0:194:190:124:17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:15:0:193:232:142:17'}, {'NAME': 'f.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:14:0:193:232:156:17'}]}
{'HEADER': {'ID': 43690, 'QR': 1, 'Opcode': '0', 'AA': 0, 'TC': 0, 'RD': 0, 'RA': 0, 'Z': '000', 'RCODE': '0', 'QDCOUNT': 1, 'ANCOUNT': 0, 'NSCOUNT': 5, 'ARCOUNT': 10}, 'ANSWER': [], 'AUTHORITY': [{'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': 'a.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'b.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'd.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'e.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'f.dns.ripn.net'}], 'ADDITIONAL': [{'NAME': 'a.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.128.6'}, {'NAME': 'a.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:17:0:193:232:128:6'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.85.252.62'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:16:0:194:85:252:62'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.190.124.17'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:18:0:194:190:124:17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.142.17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:15:0:193:232:142:17'}, {'NAME': 'f.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.156.17'}, {'NAME': 'f.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:14:0:193:232:156:17'}]}
{'HEADER': {'ID': 43690, 'QR': 1, 'Opcode': '0', 'AA': 0, 'TC': 0, 'RD': 0, 'RA': 0, 'Z': '000', 'RCODE': '0', 'QDCOUNT': 1, 'ANCOUNT': 0, 'NSCOUNT': 5, 'ARCOUNT': 10}, 'ANSWER': [], 'AUTHORITY': [{'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': 'f.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'd.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'e.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'a.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'b.dns.ripn.net'}], 'ADDITIONAL': [{'NAME': 'f.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.156.17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.142.17'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.190.124.17'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.85.252.62'}, {'NAME': 'a.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.128.6'}, {'NAME': 'f.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:14:0:193:232:156:17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:15:0:193:232:142:17'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:18:0:194:190:124:17'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:16:0:194:85:252:62'}, {'NAME': 'a.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:17:0:193:232:128:6'}]}
{'HEADER': {'ID': 43690, 'QR': 1, 'Opcode': '0', 'AA': 0, 'TC': 0, 'RD': 0, 'RA': 0, 'Z': '000', 'RCODE': '0', 'QDCOUNT': 1, 'ANCOUNT': 0, 'NSCOUNT': 5, 'ARCOUNT': 10}, 'ANSWER': [], 'AUTHORITY': [{'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': 'a.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'b.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'd.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'e.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'f.dns.ripn.net'}], 'ADDITIONAL': [{'NAME': 'a.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.128.6'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.85.252.62'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.190.124.17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.142.17'}, {'NAME': 'f.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.156.17'}, {'NAME': 'a.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:17:0:193:232:128:6'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:16:0:194:85:252:62'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:18:0:194:190:124:17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:15:0:193:232:142:17'}, {'NAME': 'f.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:14:0:193:232:156:17'}]}
{'HEADER': {'ID': 43690, 'QR': 1, 'Opcode': '0', 'AA': 0, 'TC': 0, 'RD': 0, 'RA': 0, 'Z': '000', 'RCODE': '0', 'QDCOUNT': 1, 'ANCOUNT': 0, 'NSCOUNT': 5, 'ARCOUNT': 10}, 'ANSWER': [], 'AUTHORITY': [{'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': 'a.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'b.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'd.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'e.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'f.dns.ripn.net'}], 'ADDITIONAL': [{'NAME': 'a.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.128.6'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.85.252.62'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.190.124.17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.142.17'}, {'NAME': 'f.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.156.17'}, {'NAME': 'a.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:17:0:193:232:128:6'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:16:0:194:85:252:62'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:18:0:194:190:124:17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:15:0:193:232:142:17'}, {'NAME': 'f.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:14:0:193:232:156:17'}]}
{'HEADER': {'ID': 43690, 'QR': 1, 'Opcode': '0', 'AA': 0, 'TC': 0, 'RD': 0, 'RA': 0, 'Z': '000', 'RCODE': '0', 'QDCOUNT': 1, 'ANCOUNT': 0, 'NSCOUNT': 5, 'ARCOUNT': 10}, 'ANSWER': [], 'AUTHORITY': [{'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': 'a.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'e.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'b.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'd.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'f.dns.ripn.net'}], 'ADDITIONAL': [{'NAME': 'f.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.156.17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.142.17'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.190.124.17'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.85.252.62'}, {'NAME': 'a.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.128.6'}, {'NAME': 'f.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:14:0:193:232:156:17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:15:0:193:232:142:17'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:18:0:194:190:124:17'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:16:0:194:85:252:62'}, {'NAME': 'a.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:17:0:193:232:128:6'}]}
{'HEADER': {'ID': 43690, 'QR': 1, 'Opcode': '0', 'AA': 0, 'TC': 0, 'RD': 0, 'RA': 0, 'Z': '000', 'RCODE': '0', 'QDCOUNT': 1, 'ANCOUNT': 0, 'NSCOUNT': 5, 'ARCOUNT': 10}, 'ANSWER': [], 'AUTHORITY': [{'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': 'f.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'b.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'd.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'e.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'a.dns.ripn.net'}], 'ADDITIONAL': [{'NAME': 'f.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.156.17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.142.17'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.190.124.17'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.85.252.62'}, {'NAME': 'a.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.128.6'}, {'NAME': 'f.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:14:0:193:232:156:17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:15:0:193:232:142:17'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:18:0:194:190:124:17'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:16:0:194:85:252:62'}, {'NAME': 'a.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:17:0:193:232:128:6'}]}
{'HEADER': {'ID': 43690, 'QR': 1, 'Opcode': '0', 'AA': 0, 'TC': 0, 'RD': 0, 'RA': 0, 'Z': '000', 'RCODE': '0', 'QDCOUNT': 1, 'ANCOUNT': 0, 'NSCOUNT': 5, 'ARCOUNT': 10}, 'ANSWER': [], 'AUTHORITY': [{'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': 'a.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'b.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'd.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'e.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'f.dns.ripn.net'}], 'ADDITIONAL': [{'NAME': 'a.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.128.6'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.85.252.62'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.190.124.17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.142.17'}, {'NAME': 'f.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.156.17'}, {'NAME': 'a.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:17:0:193:232:128:6'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:16:0:194:85:252:62'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:18:0:194:190:124:17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:15:0:193:232:142:17'}, {'NAME': 'f.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:14:0:193:232:156:17'}]}
{'HEADER': {'ID': 43690, 'QR': 1, 'Opcode': '0', 'AA': 0, 'TC': 0, 'RD': 0, 'RA': 0, 'Z': '000', 'RCODE': '0', 'QDCOUNT': 1, 'ANCOUNT': 0, 'NSCOUNT': 5, 'ARCOUNT': 10}, 'ANSWER': [], 'AUTHORITY': [{'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': 'e.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'd.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'b.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'f.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'a.dns.ripn.net'}], 'ADDITIONAL': [{'NAME': 'f.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.156.17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.142.17'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.190.124.17'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.85.252.62'}, {'NAME': 'a.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.128.6'}, {'NAME': 'f.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:14:0:193:232:156:17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:15:0:193:232:142:17'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:18:0:194:190:124:17'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:16:0:194:85:252:62'}, {'NAME': 'a.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:17:0:193:232:128:6'}]}
{'HEADER': {'ID': 43690, 'QR': 1, 'Opcode': '0', 'AA': 0, 'TC': 0, 'RD': 0, 'RA': 0, 'Z': '000', 'RCODE': '0', 'QDCOUNT': 1, 'ANCOUNT': 0, 'NSCOUNT': 5, 'ARCOUNT': 10}, 'ANSWER': [], 'AUTHORITY': [{'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': 'a.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'b.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'd.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'e.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'f.dns.ripn.net'}], 'ADDITIONAL': [{'NAME': 'a.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.128.6'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.85.252.62'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.190.124.17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.142.17'}, {'NAME': 'f.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.156.17'}, {'NAME': 'a.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:17:0:193:232:128:6'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:16:0:194:85:252:62'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:18:0:194:190:124:17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:15:0:193:232:142:17'}, {'NAME': 'f.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:14:0:193:232:156:17'}]}
{'HEADER': {'ID': 43690, 'QR': 1, 'Opcode': '0', 'AA': 0, 'TC': 0, 'RD': 0, 'RA': 0, 'Z': '000', 'RCODE': '0', 'QDCOUNT': 1, 'ANCOUNT': 0, 'NSCOUNT': 5, 'ARCOUNT': 10}, 'ANSWER': [], 'AUTHORITY': [{'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': 'b.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'd.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'e.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'f.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'a.dns.ripn.net'}], 'ADDITIONAL': [{'NAME': 'f.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.156.17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.142.17'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.190.124.17'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.85.252.62'}, {'NAME': 'a.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.128.6'}, {'NAME': 'f.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:14:0:193:232:156:17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:15:0:193:232:142:17'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:18:0:194:190:124:17'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:16:0:194:85:252:62'}, {'NAME': 'a.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:17:0:193:232:128:6'}]}
{'HEADER': {'ID': 43690, 'QR': 1, 'Opcode': '0', 'AA': 0, 'TC': 0, 'RD': 0, 'RA': 0, 'Z': '000', 'RCODE': '0', 'QDCOUNT': 1, 'ANCOUNT': 0, 'NSCOUNT': 5, 'ARCOUNT': 10}, 'ANSWER': [], 'AUTHORITY': [{'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': 'a.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'b.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'd.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'e.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'f.dns.ripn.net'}], 'ADDITIONAL': [{'NAME': 'a.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.128.6'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.85.252.62'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.190.124.17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.142.17'}, {'NAME': 'f.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.156.17'}, {'NAME': 'a.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:17:0:193:232:128:6'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:16:0:194:85:252:62'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:18:0:194:190:124:17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:15:0:193:232:142:17'}, {'NAME': 'f.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:14:0:193:232:156:17'}]}
{'HEADER': {'ID': 43690, 'QR': 1, 'Opcode': '0', 'AA': 0, 'TC': 0, 'RD': 0, 'RA': 0, 'Z': '000', 'RCODE': '0', 'QDCOUNT': 1, 'ANCOUNT': 0, 'NSCOUNT': 5, 'ARCOUNT': 10}, 'ANSWER': [], 'AUTHORITY': [{'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': 'd.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'b.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'e.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'a.dns.ripn.net'}, {'NAME': 'ru', 'TYPE': 2, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': 'f.dns.ripn.net'}], 'ADDITIONAL': [{'NAME': 'f.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.156.17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.142.17'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.190.124.17'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '194.85.252.62'}, {'NAME': 'a.dns.ripn.net', 'TYPE': 1, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 4, 'RDDATA': '193.232.128.6'}, {'NAME': 'f.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:14:0:193:232:156:17'}, {'NAME': 'e.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:15:0:193:232:142:17'}, {'NAME': 'd.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:18:0:194:190:124:17'}, {'NAME': 'b.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:16:0:194:85:252:62'}, {'NAME': 'a.dns.ripn.net', 'TYPE': 28, 'CLASS': 1, 'TTL': 172800, 'RDLENGTH': 16, 'RDDATA': '2001:678:17:0:193:232:128:6'}]}