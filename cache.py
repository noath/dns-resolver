import logging
import time
from collections import deque


class Cache:
    def __init__(self, max_size=-1):
        self.max_size = max_size
        self.cache = {}
        self.key_queue = deque()

    def add(self, domain, time, sites):
        if self.max_size == 0:  # no cache case
            return
        if len(self.key_queue) >= self.max_size and self.max_size > 0:
            removed = self.key_queue.popleft()
            self.cache.pop(removed)
            logging.info(
                f"Removing cache info for domain {removed} due to maximum size of cache..."
            )

        logging.info(f"Updating cache info for domain {domain}...")
        self.cache[domain] = {"TIME": time, "SITES": sites}
        self.key_queue.append(domain)

    def get(self, domain):
        if domain in self.cache:
            for site in self.cache[domain]["SITES"]:
                if self.cache[domain]["TIME"] + site.ttl < time.time():
                    logging.info(
                        f"Removing cashed site for domain {domain} due to expiring..."
                    )
                    self.cache.pop(domain)
                    try:
                        self.key_queue.remove(domain)
                    except ValueError as e:
                        logging.error(
                            f"Error occured while removing old cache for domain {domain}: {e}"
                        )
                    return None

            logging.info(f"Using cashed site for domain {domain}...")
            return self.cache[domain]["SITES"]
        else:
            logging.info(f"Domain {domain} did not found in cache.")
        return None

    def update(self):
        for domain in self.cache:
            for site in self.cache[domain]["SITES"]:
                if self.cache[domain]["TIME"] + site.ttl < time.time():
                    logging.info(
                        f"Removing cashed site for domain {domain} due to expiring..."
                    )
                    self.cache.pop(domain)
                    try:
                        self.key_queue.remove(domain)
                    except ValueError as e:
                        logging.error(
                            f"Error occured while removing old cache for domain {domain}: {e}"
                        )
                    return None
