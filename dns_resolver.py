import logging
import sys
import time
from cache import Cache
from constants import APP_ADDRESS, APP_PORT, DNS_PORT, MAX_STEPS, LOG_PATH, ROOT_SERVERS
from distutils.util import strtobool
from dns_parser import resolve_step
from flask import Flask, request
from gevent.pywsgi import WSGIServer
from utils import QType, check_IPv6_support


logging.basicConfig(level=logging.INFO, filename=LOG_PATH, filemode="w")
app = Flask(__name__)


@app.route("/")
def index():
    return "<h1>Recursive DNS-resolver</h1><br/>Try to use query /get-records?domain=&trace=<br/>Detailes can be found on <a href='https://github.com/noath/dns-resolver'>github repository</a>.<br/><br/>Author: <a href='https://t.me/noath'>@Noath (telegram)</a>"


@app.route("/update-cache", methods=['GET'])
def update_cache():
    try:
        cache = app.config["cache"]
        cache.update()
        return "Cache updated"
    except Exception as e:
        logging.error(f"Error occured while updating cache: {e}")
        return "Failed to update cache"


@app.route("/get-records", methods=['GET'])
def get_records():
    ipv_string = "Using IPv4 " + (
        "and IPv6 both.<br/><br/>" if app.config["ipv6_support"] else "only.<br/><br/>"
    )
    cache = app.config["cache"]
    domain = request.args.get("domain").strip('.')
    if len(domain) == 0:
        logging.error("Empty domain")
        raise ValueError("Empty domain")
    trace_flag = request.args.get("trace", "false")
    try:
        trace_flag = strtobool(trace_flag)
    except Exception as e:
        logging.error(f"Error occured while converting trace argument to bool: {e}")
        trace_flag = False
    if not trace_flag:
        cached = cache.get(domain)
        if cached is not None:
            return (
                ipv_string
                + "Using cached record:<br/>"
                + "<br/>".join(map(repr, cached))
            )

    logging.info(f"Starting resolving for domain {domain}...")
    resolve_start_time = time.time()
    for root in ROOT_SERVERS:
        trace = []

        res_nodes_ipv4 = resolve_step(domain, root, root, trace, 0, QType.A, DNS_PORT, MAX_STEPS, ROOT_SERVERS)
        res_nodes_ipv4 = res_nodes_ipv4 if res_nodes_ipv4 is not None else []

        if app.config["ipv6_support"]:
            try:
                res_nodes_ipv6 = resolve_step(domain, root, root, trace, 0, QType.AAAA, DNS_PORT, MAX_STEPS, ROOT_SERVERS)
                res_nodes_ipv6 = res_nodes_ipv6 if res_nodes_ipv6 is not None else []
            except Exception as e:
                logging.error(
                    f"Error occured while resolving IPv6. Probably your net has no IPv6 support. Exception: {e}"
                )
                res_nodes_ipv6 = []
        else:
            res_nodes_ipv6 = []

        res_nodes = res_nodes_ipv4 + res_nodes_ipv6
        if res_nodes:
            logging.info(f"Resolving finished for domain {domain}...")
            logging.info(f"Updating cache for domain {domain}...")
            cache.add(domain, resolve_start_time, res_nodes)
            break

    if trace_flag:
        return (
            ipv_string
            + "Trace:<br/>"
            + "<br/>".join(map(repr, trace))
            + "<br/><br/>Answers:<br/>"
            + "<br/>".join(map(repr, res_nodes))
        )
    return ipv_string + "<br/>".join(map(repr, res_nodes)) if res_nodes else 'There are not A or AAAA records for this domain.'


if __name__ == "__main__":
    try:
        max_cache_size = int(sys.argv[1]) if len(sys.argv) > 1 else -1
    except Exception as e:
        logging.error(f"Error occured while parsing argv: {e}")
        max_cache_size = -1
    app.config["cache"] = Cache(max_size=max_cache_size)
    app.config["ipv6_support"] = check_IPv6_support(DNS_PORT)
    # app.run(debug=False, port=APP_PORT)
    http_server = WSGIServer((APP_ADDRESS, APP_PORT), app)
    print(f'Serving on {APP_ADDRESS}:{APP_PORT}...')
    http_server.serve_forever()
