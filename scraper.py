from __future__ import annotations

import socket
import threading
import time
from datetime import datetime

from config import (
    BACKGROUND_MIN_INTERVAL_SECONDS,
    LATE_COMPLETION_LOG_COOLDOWN_SECONDS,
    SCRAPE_TRACKING_FILE,
    WATCHDOG_TIMEOUT_SECONDS,
)
from updater import ContentUpdater
from utils import load_json, save_json


class AutonomousScraper:
    def __init__(self, updater: ContentUpdater) -> None:
        self.updater = updater
        self.running = False
        self.thread: threading.Thread | None = None
        self.tracking = load_json(SCRAPE_TRACKING_FILE, default={})
        self.internet_available = False
        self.user_active = threading.Event()
        self._late_completion_log_cooldown_seconds = float(LATE_COMPLETION_LOG_COOLDOWN_SECONDS)
        self._last_late_completion_log = 0.0
        self._log_lock = threading.Lock()

    def set_user_active(self, active: bool) -> None:
        if active:
            self.user_active.set()
        else:
            self.user_active.clear()

    def _check_internet(self) -> bool:
        try:
            socket.gethostbyname("en.wikipedia.org")
            with socket.create_connection(("1.1.1.1", 53), timeout=2):
                return True
        except OSError:
            return False

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._scrape_loop, daemon=True)
        self.thread.start()
        print("Background scraper started.")

    def stop(self) -> None:
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2)

    def _run_watchdog_update(self) -> int:
        result = {"count": 0}
        finished = threading.Event()

        def do_update() -> None:
            try:
                result["count"] = self.updater._fetch_web_content()
            except Exception as exc:
                print(f"Background update failed: {exc}")
                result["count"] = 0
            finally:
                finished.set()

        worker = threading.Thread(target=do_update, daemon=True)
        worker.start()
        worker.join(timeout=WATCHDOG_TIMEOUT_SECONDS)
        if worker.is_alive():
            print(
                f"Background update watchdog timeout reached after {WATCHDOG_TIMEOUT_SECONDS}s; update still running in background."
            )

            def log_late_completion() -> None:
                finished.wait()
                now = time.time()
                with self._log_lock:
                    if now - self._last_late_completion_log >= self._late_completion_log_cooldown_seconds:
                        print(f"Background update finished after timeout; added {result['count']} items.")
                        self._last_late_completion_log = now

            threading.Thread(target=log_late_completion, daemon=True).start()
            return 0
        return result["count"]

    def _scrape_loop(self) -> None:
        backoff_seconds = 20
        while self.running:
            if self.user_active.is_set():
                time.sleep(4)
                continue

            has_internet = self._check_internet()
            if not has_internet:
                if self.internet_available:
                    self.internet_available = False
                    print(f"[{datetime.now().isoformat(timespec='seconds')}] Switched to offline mode.")
                time.sleep(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2, 300)
                continue

            if not self.internet_available:
                print(f"[{datetime.now().isoformat(timespec='seconds')}] Internet connection restored.")
                self.internet_available = True

            backoff_seconds = 20
            current_time = time.time()
            last_scrape = float(self.tracking.get("last_background_scrape", 0))
            if current_time - last_scrape >= BACKGROUND_MIN_INTERVAL_SECONDS:
                print(f"[{datetime.now().isoformat(timespec='seconds')}] Running background scrape.")
                count = self._run_watchdog_update()
                if count > 0:
                    print(f"Background scrape added {count} items.")
                    self.tracking["last_background_scrape"] = current_time
                    save_json(SCRAPE_TRACKING_FILE, self.tracking)

            for _ in range(60):
                if not self.running:
                    break
                time.sleep(2)
