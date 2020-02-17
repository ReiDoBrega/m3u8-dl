from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, Future
from multiprocessing import Manager, current_process
from .common.constants import IP, PORT, HEADER_SIZE
from typing import List, Dict, Optional
from .weblib.fetch import fetch_data
from .common.base import Client
from threading import Lock
from random import shuffle
from time import time
import requests
import pickle
import sys
import os


def download_process(links, total_links, session, headers, http2, max_retries,
                     convert, file_link_maps, path_prefix) -> None:

    print(f"Starting Download process {current_process().name}")
    start_time = time()
    try:
        download_manager = DownloadProcess(links, total_links, session, headers, http2, max_retries, convert)
        start_processes(download_manager, file_link_maps, path_prefix)
        try:
            client = Client(IP, PORT)
            client.send_data(f"{'STOP_QUEUE':<{HEADER_SIZE}}{download_manager.get_total_downloaded_links_count()}")
        except:
            pass

    except (KeyboardInterrupt, Exception):
        sys.exit()

    print(f"Download took {time() - start_time}")
    print(f"Stopped Download process {current_process().name}")


class DownloadProcess:
    def __init__(self, links: List[str], total_links: int, session: requests.Session,
                 headers: Dict[str, str], http2: bool = False, max_retries: int = 5, convert: bool = True):
        self.__session: requests.Session = session
        self.__headers: Dict[str, str] = headers
        self.__total_links: int = total_links
        self.__links: List[str] = links
        self.max_retries: int = max_retries
        self.http2: bool = http2
        self.convert = convert
        self.__process_num = 8
        self.__thread_num = 4
        self.__sent = 0
        self.done_retries = 0
        self.error_links = []

    def get_thread_num(self, num: int = 4) -> int:
        if num < self.__thread_num:
            return num
        return self.__thread_num

    def get_process_num(self) -> int:
        return self.__process_num

    def get_download_links(self) -> List[str]:
        return self.__links

    def get_total_links(self) -> int:
        return self.__total_links

    def get_session(self) -> requests.Session:
        return self.__session

    def get_headers(self) -> Dict[str, str]:
        return self.__headers

    def get_total_downloaded_links_count(self) -> int:
        return self.__sent

    def set_total_downloaded_links_count(self, val: int = 1) -> None:
        self.__sent += val


def start_processes(download_manager: DownloadProcess, file_link_maps: Dict[str, str], path_prefix: str) -> None:
    process_num: int = download_manager.get_process_num()
    with ProcessPoolExecutor(max_workers=process_num) as process_pool_executor:
        try:
            process_pool_executor_handler(process_pool_executor, download_manager, file_link_maps, path_prefix)
        except (KeyboardInterrupt, Exception):
            sys.exit()


def process_pool_executor_handler(executor: ProcessPoolExecutor, manager: DownloadProcess,
                                  file_maps: Dict[str, str], directory: str) -> None:
    if manager.done_retries == manager.max_retries:
        return

    print(f"Starting download {manager.get_total_links() - manager.get_total_downloaded_links_count()} left")

    lock = Manager().Lock()

    def update_hook(future: Future):
        temp = future.result()
        if temp:
            with lock:
                manager.error_links.extend(temp)

    if manager.error_links:
        shuffle(manager.error_links)
        download_links = manager.error_links.copy()
        manager.error_links = []
    else:
        download_links = manager.get_download_links().copy()
        shuffle(download_links)

    start = 0
    for _ in range(len(download_links)):
        end = start + manager.get_thread_num()
        if end > len(download_links):
            end = len(download_links)
        executor.submit(start_threads, download_links[start:end], file_maps,
                        manager.get_session(), manager.get_headers(),
                        directory, manager.http2).add_done_callback(update_hook)
        start = end
        if end >= len(download_links):
            break

    manager.set_total_downloaded_links_count(manager.get_total_links() - len(manager.error_links))

    if manager.error_links:
        print(f"{manager.get_total_links()} was expected but "
              f"{manager.get_total_downloaded_links_count()} was downloaded.")
        manager.done_retries += 1
        return process_pool_executor_handler(executor, manager, file_maps, directory)


def start_threads(links: List[str], maps: Dict[str, str], session: requests.Session,
                  headers: Dict[str, str], file_path_prefix: str, http2: bool) -> List[Optional[str]]:

    lock = Lock()

    def update_hook(future: Future):
        temp = future.result()
        if temp:
            with lock:
                failed_links.append(temp)

    failed_links = []

    thread_num: int = 4 if len(links) > 4 else len(links)

    sent_links = {}

    with ThreadPoolExecutor(max_workers=thread_num) as executor:
        for link in links:
            temp_path = os.path.join(file_path_prefix, maps[link])
            sent_links[link] = temp_path
            executor.submit(download_thread, temp_path, link, session, headers, http2)\
                    .add_done_callback(update_hook)

    for link in failed_links:
        del sent_links[link]

    send_data = pickle.dumps(list(sent_links.values()))
    msg = f"{'POST_FILENAME_QUEUE':<{HEADER_SIZE}}"
    client = Client(IP, PORT)
    client.send_data(msg)
    client.send_data(send_data, "bytes")

    return failed_links


def download_thread(file_path: str, link: str, session: requests.Session,
                    headers: Dict[str, str], http2: bool) -> Optional[str]:

    if os.path.exists(file_path):
        return None

    return fetch_data(link, headers, session, 120, file_path, http2)
