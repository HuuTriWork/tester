import sys
import os
import json
import subprocess
import time
import threading
import random
from typing import Optional, Tuple

import cv2
import numpy as np

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTableWidget, QTableWidgetItem, QCheckBox, QPushButton, QTextEdit,
    QHeaderView, QLabel, QTabWidget, QFrame, QLineEdit, QDialog, QMessageBox
)
from PyQt5.QtCore import Qt, pyqtSignal, QSize
from PyQt5.QtGui import QIcon, QPixmap, QImage

ADB_PATH = "adb\\adb.exe"
DATA_DIR = "data"
CACHE_DIR = "cache"
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)
DATA_JSON = "data.json"

THRESHOLDS = {
    "disconnect.png": 0.75,
    "confirm.png": 0.75,
    "home.png": 0.85,
    "map.png": 0.85,
    "scout.png": 0.85,
    "explore.png": 0.85,
    "selected.png": 0.85,
    "notselected.png": 0.88,
    "exit.png": 0.85,
    "send.png": 0.85,
    "other.png": 0.85,
    "caves.png": 0.85,
    "go.png": 0.85,
    "investigate.png": 0.85,
    "sleep.png": 0.70,
    "back.png": 0.70,
    "camp.png": 0.70,
    "captcha1.png": 0.75,
    "captcha2.png": 0.75,
    "captcha3.png": 0.75,
}

RANDOM_DELAY_RANGE = (1.5, 2.5)
RANDOM_OFFSET = 5
FIXED_DELAY = 1.5
LONG_RECONNECT_WAIT = 30.0

connected_devices = set()
halted_devices = set()

PORT_CANDIDATES = [5555, 5557, 5559, 5561, 5563, 5565, 5567, 5569, 5571, 5573]

def adb_devices_raw():
    try:
        result = subprocess.run([ADB_PATH, "devices"], capture_output=True, text=True)
        lines = result.stdout.strip().splitlines()[1:]
        parsed = []
        for line in lines:
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 2:
                parsed.append((parts[0], parts[1]))
            else:
                parsed.append((parts[0], ""))
        return parsed
    except Exception:
        return []

def try_connect_ports():
    for p in PORT_CANDIDATES:
        ip_port = f"127.0.0.1:{p}"
        try:
            subprocess.run([ADB_PATH, "connect", ip_port], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1.2)
        except Exception:
            pass

def get_ipport_devices():
    parsed = adb_devices_raw()
    devices = []
    for dev_id, _ in parsed:
        if dev_id.startswith("127.0.0.1:") or dev_id.startswith("localhost:"):
            devices.append(dev_id)
    return devices

def ensure_connected(dev):
    if dev in connected_devices:
        return
    try:
        subprocess.run([ADB_PATH, "connect", dev], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1.2)
        connected_devices.add(dev)
    except Exception:
        pass

def adb_tap(dev: str, x: int, y: int):
    subprocess.run([ADB_PATH, "-s", dev, "shell", "input", "tap", str(int(x)), str(int(y))],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def adb_tap_randomized(dev: str, x: int, y: int, offset: int = RANDOM_OFFSET):
    rand_x = x + random.randint(-offset, offset)
    rand_y = y + random.randint(-offset, offset)
    subprocess.run([ADB_PATH, "-s", dev, "shell", "input", "tap", str(int(rand_x)), str(int(rand_y))],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def adb_screencap_img(dev: str) -> Optional[np.ndarray]:
    try:
        proc = subprocess.run([ADB_PATH, "-s", dev, "exec-out", "screencap", "-p"],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=8)
        data = proc.stdout
        if not data:
            return None
        img_array = np.frombuffer(data, np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
        if img is not None:
            cv2.imwrite(os.path.join(CACHE_DIR, f"{dev.replace(':','_')}.png"), img)
        return img
    except Exception:
        return None

def load_template(name: str) -> Optional[np.ndarray]:
    path = os.path.join(DATA_DIR, name)
    if not os.path.exists(path):
        return None
    return cv2.imread(path, cv2.IMREAD_COLOR)

def match_template(screen: np.ndarray, templ: np.ndarray) -> Tuple[float, Tuple[int, int]]:
    res = cv2.matchTemplate(screen, templ, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    h, w = templ.shape[:2]
    center = (max_loc[0] + w // 2, max_loc[1] + h // 2)
    return max_val, center

def find_on_screen(screen: np.ndarray, template_name: str) -> Optional[Tuple[int, int]]:
    templ = load_template(template_name)
    if templ is None or screen is None:
        return None
    thr = THRESHOLDS.get(template_name, 0.85)
    score, center = match_template(screen, templ)
    if score >= thr:
        return center
    return None

def wait_for_template(dev: str, template_name: str, timeout: float = 12.0, interval: float = 0.8, stop_event: threading.Event = None) -> Optional[Tuple[int, int]]:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if stop_event and stop_event.is_set():
            return None
        screen = adb_screencap_img(dev)
        if screen is None:
            if stop_event and stop_event.wait(interval):
                return None
            time.sleep(0.05)
            continue
        pos = find_on_screen(screen, template_name)
        if pos:
            return pos
        if stop_event and stop_event.wait(interval):
            return None
    return None

def wait_for_any_template(dev: str, names: list, timeout: float = 12.0, interval: float = 0.8, stop_event: threading.Event = None) -> Optional[Tuple[str, Tuple[int, int]]]:
    t0 = time.time()
    while time.time() - t0 < timeout:
        if stop_event and stop_event.is_set():
            return None
        screen = adb_screencap_img(dev)
        if screen is None:
            if stop_event and stop_event.wait(interval):
                return None
            time.sleep(0.05)
            continue
        for name in names:
            pos = find_on_screen(screen, name)
            if pos:
                return name, pos
        if stop_event and stop_event.wait(interval):
            return None
    return None

def wait_or_stop(stop_event: threading.Event, seconds: float) -> bool:
    if stop_event:
        return stop_event.wait(seconds)
    else:
        time.sleep(seconds)
        return False

def get_delay(anti_ban_enabled: bool, delay_range: Tuple[float, float] = RANDOM_DELAY_RANGE) -> float:
    if anti_ban_enabled:
        return random.uniform(delay_range[0], delay_range[1])
    return FIXED_DELAY

def perform_tap(dev:str, x: int, y: int, anti_ban_enabled: bool):
    if anti_ban_enabled:
        adb_tap_randomized(dev, x, y)
    else:
        adb_tap(dev, x, y)

def load_coords() -> dict:
    if not os.path.exists(DATA_JSON):
        return {}
    try:
        with open(DATA_JSON, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_coords(data: dict):
    try:
        with open(DATA_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False

def click_center(dev: str, log_fn, stop_event: threading.Event, anti_ban: bool):
    img = adb_screencap_img(dev)
    if img is None:
        return
    h, w = img.shape[:2]
    cx, cy = w // 2, h // 2
    log_fn(f"[{dev}] Clicking screen center ({cx},{cy}) to skip.")
    perform_tap(dev, cx, cy, anti_ban)
    if wait_or_stop(stop_event, get_delay(anti_ban)):
        return

def reset_to_home(dev: str, log_fn, stop_event: threading.Event, anti_ban: bool):
    screen = adb_screencap_img(dev)
    if screen is None:
        log_fn(f"[{dev}] Reset: Could not get screenshot.")
        return
    home_pos = find_on_screen(screen, "home.png")
    map_pos = find_on_screen(screen, "map.png")
    if home_pos:
        log_fn(f"[{dev}] Home icon found. Entering city.")
        perform_tap(dev, *home_pos, anti_ban)
        if wait_or_stop(stop_event, get_delay(anti_ban)):
            return
        return
    if map_pos:
        log_fn(f"[{dev}] Map icon found. Exiting to map then going home.")
        perform_tap(dev, *map_pos, anti_ban)
        if wait_or_stop(stop_event, get_delay(anti_ban)):
            return
        hp = wait_for_template(dev, "home.png", timeout=6.0, stop_event=stop_event)
        if hp:
            perform_tap(dev, *hp, anti_ban)
            wait_or_stop(stop_event, get_delay(anti_ban))
        else:
            log_fn(f"[{dev}] Could not find home icon after exiting to map.")

def go_to_coord_and_scout(dev: str, log_fn, stop_event: threading.Event, anti_ban: bool):
    coords = load_coords()
    info = coords.get(dev)
    if not info or "x" not in info or "y" not in info:
        log_fn(f"[{dev}] Coordinates not set. Please use Setup.")
        return False
    x, y = int(info["x"]), int(info["y"])
    log_fn(f"[{dev}] Clicking coordinates: ({x},{y})")
    perform_tap(dev, x, y, anti_ban)
    if wait_or_stop(stop_event, get_delay(anti_ban)):
        return False
    sp = wait_for_template(dev, "scout.png", timeout=6.0, stop_event=stop_event)
    if sp:
        perform_tap(dev, *sp, anti_ban)
        if wait_or_stop(stop_event, get_delay(anti_ban)):
            return False
        log_fn(f"[{dev}] Scout button clicked.")
        return True
    log_fn(f"[{dev}] Scout button not found.")
    return False

def ensure_selected(dev: str, log_fn, stop_event: threading.Event, anti_ban: bool, retries: int = 3):
    for i in range(retries):
        if stop_event.is_set():
            return False
        wait_or_stop(stop_event, get_delay(anti_ban, (0.3, 0.7)))
        screen = adb_screencap_img(dev)
        if screen is None:
            if i < retries - 1:
                log_fn(f"[{dev}] Select: Can't get screenshot, retrying...")
                wait_or_stop(stop_event, 1.0)
                continue
            else:
                log_fn(f"[{dev}] Select: Failed to get screenshot.")
                return False
        if find_on_screen(screen, "selected.png"):
            log_fn(f"[{dev}] Troops are selected.")
            return True
        pos_not = find_on_screen(screen, "notselected.png")
        if pos_not:
            log_fn(f"[{dev}] Not selected. Clicking to select (Attempt {i + 1}).")
            perform_tap(dev, *pos_not, anti_ban)
            wait_or_stop(stop_event, get_delay(anti_ban, (1.0, 1.5)))
        else:
            log_fn(f"[{dev}] Cannot find selected/notselected status (Attempt {i + 1}).")
            if i < retries - 1:
                wait_or_stop(stop_event, 1.0)
    log_fn(f"[{dev}] FAILED to confirm troop selection after {retries} attempts.")
    return False

def do_reconnect_if_needed(dev: str, log_fn, stop_event: threading.Event, anti_ban: bool) -> bool:
    screen = adb_screencap_img(dev)
    if screen is None:
        return False
    if find_on_screen(screen, "disconnect.png"):
        log_fn(f"[{dev}] Disconnect detected.")
        pos_confirm = wait_for_template(dev, "confirm.png", timeout=8.0, stop_event=stop_event)
        if pos_confirm:
            perform_tap(dev, *pos_confirm, anti_ban)
            log_fn(f"[{dev}] Confirm button clicked. Waiting {int(LONG_RECONNECT_WAIT)}s to reconnect.")
            if stop_event and stop_event.wait(LONG_RECONNECT_WAIT):
                return True
            return True
        else:
            log_fn(f"[{dev}] Confirm button not found after disconnect.")
    return False

def do_captcha_check(dev: str, log_fn, stop_event: threading.Event) -> bool:
    names = ["captcha1.png", "captcha2.png", "captcha3.png"]
    res = wait_for_any_template(dev, names, timeout=0.5, interval=0.5, stop_event=stop_event)
    if res:
        return True
    return False

def try_exit(dev: str, log_fn, stop_event: threading.Event, anti_ban: bool, timeout: float = 5.0):
    pos_exit = wait_for_template(dev, "exit.png", timeout=timeout, stop_event=stop_event)
    if pos_exit:
        log_fn(f"[{dev}] Exit.")
        perform_tap(dev, *pos_exit, anti_ban)
        wait_or_stop(stop_event, get_delay(anti_ban))
        return True
    return False

def logic_explore_fog(dev: str, log_fn, only_this_mode: bool, other_modes_selected: bool, stop_event: threading.Event, anti_ban: bool, captcha_enabled: bool, captcha_notify):
    reset_to_home(dev, log_fn, stop_event, anti_ban)
    if stop_event.is_set():
        return False
    if captcha_enabled and do_captcha_check(dev, log_fn, stop_event):
        captcha_notify(dev)
        return True
    if not go_to_coord_and_scout(dev, log_fn, stop_event, anti_ban):
        return False
    if captcha_enabled and do_captcha_check(dev, log_fn, stop_event):
        captcha_notify(dev)
        return True

    if only_this_mode:
        log_fn(f"[{dev}] Only Explore Fog selected waiting until Explore.")
        while not stop_event.is_set():
            if captcha_enabled and do_captcha_check(dev, log_fn, stop_event):
                captcha_notify(dev)
                return True
            screen = adb_screencap_img(dev)
            if screen is None:
                if stop_event and stop_event.wait(0.8):
                    return False
                continue
            pos_explore = find_on_screen(screen, "explore.png")
            if pos_explore:
                log_fn(f"[{dev}] Explore appears, will press.")
                perform_tap(dev, *pos_explore, anti_ban)
                if wait_or_stop(stop_event, get_delay(anti_ban)):
                    return False
                break
            if stop_event and stop_event.wait(0.8):
                return False
    else:
        pos_explore = wait_for_template(dev, "explore.png", timeout=8.0, stop_event=stop_event)
        if not pos_explore:
            if only_this_mode:
                log_fn(f"[{dev}] Explore button not found, waiting longer...")
                pos_explore = wait_for_template(dev, "explore.png", timeout=20.0, stop_event=stop_event)
                if not pos_explore:
                    log_fn(f"[{dev}] Explore button still not found.")
                    return False
            else:
                if other_modes_selected:
                    log_fn(f"[{dev}] Explore not found. Trying to exit current panel before switching mode.")
                    try_exit(dev, log_fn, stop_event, anti_ban, timeout=4.0)
                    click_center(dev, log_fn, stop_event, anti_ban)
                    return False
                else:
                    return False
        else:
            perform_tap(dev, *pos_explore, anti_ban)
            if wait_or_stop(stop_event, get_delay(anti_ban)):
                return False

    if captcha_enabled and do_captcha_check(dev, log_fn, stop_event):
        captcha_notify(dev)
        return True

    if not ensure_selected(dev, log_fn, stop_event, anti_ban):
        log_fn(f"[{dev}] Fog: Could not select troops.")
        return False
    if stop_event.is_set():
        return False

    pos_explore2 = wait_for_template(dev, "explore.png", timeout=6.0, stop_event=stop_event)
    if pos_explore2:
        perform_tap(dev, *pos_explore2, anti_ban)
        if wait_or_stop(stop_event, get_delay(anti_ban)):
            return False
        if captcha_enabled and do_captcha_check(dev, log_fn, stop_event):
            captcha_notify(dev)
            return True
        pos_send = wait_for_template(dev, "send.png", timeout=6.0, stop_event=stop_event)
        if pos_send:
            perform_tap(dev, *pos_send, anti_ban)
            wait_or_stop(stop_event, get_delay(anti_ban))
            log_fn(f"[{dev}] Fog: Sent troops.")
            return True
    log_fn(f"[{dev}] Fog: Failed to complete.")
    return False

def logic_explore_other(dev: str, log_fn, only_this_mode: bool, other_modes_selected: bool, stop_event: threading.Event, anti_ban: bool, captcha_enabled: bool, captcha_notify):
    reset_to_home(dev, log_fn, stop_event, anti_ban)
    if stop_event.is_set():
        return False
    if captcha_enabled and do_captcha_check(dev, log_fn, stop_event):
        captcha_notify(dev)
        return True
    if not go_to_coord_and_scout(dev, log_fn, stop_event, anti_ban):
        return False
    if captcha_enabled and do_captcha_check(dev, log_fn, stop_event):
        captcha_notify(dev)
        return True
    pos_other = wait_for_template(dev, "other.png", timeout=8.0, stop_event=stop_event)
    if not pos_other:
        log_fn(f"[{dev}] 'Other' button not found.")
        if other_modes_selected:
            try_exit(dev, log_fn, stop_event, anti_ban, timeout=4.0)
        return False
    perform_tap(dev, *pos_other, anti_ban)
    if wait_or_stop(stop_event, get_delay(anti_ban)):
        return False
    if captcha_enabled and do_captcha_check(dev, log_fn, stop_event):
        captcha_notify(dev)
        return True
    pos_go = wait_for_template(dev, "go.png", timeout=8.0, stop_event=stop_event)
    if not pos_go:
        if only_this_mode:
            log_fn(f"[{dev}] 'Go' button not found, waiting longer...")
            pos_go = wait_for_template(dev, "go.png", timeout=20.0, stop_event=stop_event)
            if not pos_go:
                log_fn(f"[{dev}] 'Go' button still not found.")
                return False
        else:
            if other_modes_selected:
                log_fn(f"[{dev}] 'Go' not found. Exiting current panel and skipping to next mode.")
                try_exit(dev, log_fn, stop_event, anti_ban, timeout=4.0)
                click_center(dev, log_fn, stop_event, anti_ban)
                return False
            else:
                return False
    perform_tap(dev, *pos_go, anti_ban)
    random_wait = get_delay(anti_ban, (18.0, 22.0))
    log_fn(f"[{dev}] Other: 'Go' clicked, waiting {random_wait:.1f}s.")
    if wait_or_stop(stop_event, random_wait):
        return False
    return True

def logic_explore_caves(dev: str, log_fn, only_this_mode: bool, other_modes_selected: bool, stop_event: threading.Event, anti_ban: bool, captcha_enabled: bool, captcha_notify):
    reset_to_home(dev, log_fn, stop_event, anti_ban)
    if stop_event.is_set():
        return False
    if captcha_enabled and do_captcha_check(dev, log_fn, stop_event):
        captcha_notify(dev)
        return True
    if not go_to_coord_and_scout(dev, log_fn, stop_event, anti_ban):
        return False
    if captcha_enabled and do_captcha_check(dev, log_fn, stop_event):
        captcha_notify(dev)
        return True
    pos_caves = wait_for_template(dev, "caves.png", timeout=8.0, stop_event=stop_event)
    if not pos_caves:
        log_fn(f"[{dev}] 'Caves' button not found.")
        if other_modes_selected:
            try_exit(dev, log_fn, stop_event, anti_ban, timeout=4.0)
        return False
    perform_tap(dev, *pos_caves, anti_ban)
    if wait_or_stop(stop_event, get_delay(anti_ban)):
        return False
    if captcha_enabled and do_captcha_check(dev, log_fn, stop_event):
        captcha_notify(dev)
        return True
    pos_go = wait_for_template(dev, "go.png", timeout=8.0, stop_event=stop_event)
    if not pos_go:
        if only_this_mode:
            log_fn(f"[{dev}] Caves: 'Go' button not found, waiting longer...")
            pos_go = wait_for_template(dev, "go.png", timeout=20.0, stop_event=stop_event)
            if not pos_go:
                log_fn(f"[{dev}] Caves: 'Go' button still not found.")
                return False
        else:
            if other_modes_selected:
                log_fn(f"[{dev}] Caves: 'Go' not found. Exiting current panel and skipping to next mode.")
                try_exit(dev, log_fn, stop_event, anti_ban, timeout=4.0)
                click_center(dev, log_fn, stop_event, anti_ban)
                return False
            else:
                return False
    perform_tap(dev, *pos_go, anti_ban)
    if wait_or_stop(stop_event, get_delay(anti_ban)):
        return False
    if captcha_enabled and do_captcha_check(dev, log_fn, stop_event):
        captcha_notify(dev)
        return True
    if not ensure_selected(dev, log_fn, stop_event, anti_ban):
        log_fn(f"[{dev}] Caves: Could not select troops.")
        return False
    if stop_event.is_set():
        return False
    pos_invest = wait_for_template(dev, "investigate.png", timeout=8.0, stop_event=stop_event)
    if not pos_invest:
        log_fn(f"[{dev}] 'Investigate' button not found.")
        if other_modes_selected:
            try_exit(dev, log_fn, stop_event, anti_ban, timeout=4.0)
        return False
    perform_tap(dev, *pos_invest, anti_ban)
    if wait_or_stop(stop_event, get_delay(anti_ban)):
        return False
    res = wait_for_any_template(dev, ["sleep.png", "back.png", "camp.png"], timeout=12.0, stop_event=stop_event)
    if not res:
        log_fn(f"[{dev}] Did not find sleep/back/camp buttons.")
        if other_modes_selected:
            try_exit(dev, log_fn, stop_event, anti_ban, timeout=4.0)
        return False
    name, pos = res
    perform_tap(dev, *pos, anti_ban)
    if wait_or_stop(stop_event, get_delay(anti_ban)):
        return False
    if captcha_enabled and do_captcha_check(dev, log_fn, stop_event):
        captcha_notify(dev)
        return True
    pos_send = wait_for_template(dev, "send.png", timeout=8.0, stop_event=stop_event)
    if pos_send:
        perform_tap(dev, *pos_send, anti_ban)
        if wait_or_stop(stop_event, get_delay(anti_ban)):
            return False
        log_fn(f"[{dev}] Caves: Sent troops.")
        return True
    log_fn(f"[{dev}] Caves: 'Send' button not found.")
    return False

class ClickableLabel(QLabel):
    clicked = pyqtSignal(int, int)
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(int(event.pos().x()), int(event.pos().y()))
        super().mousePressEvent(event)

class SetupDialog(QDialog):
    def __init__(self, dev: str, native_img: np.ndarray, ref_w: int, ref_h: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Setup - {dev}")
        self.dev = dev
        self.native_img = native_img
        self.native_h, self.native_w = native_img.shape[:2]
        self.ref_w = ref_w
        self.ref_h = ref_h
        vbox = QVBoxLayout()
        self.lbl = ClickableLabel()
        self.lbl.setAlignment(Qt.AlignCenter)
        vbox.addWidget(self.lbl)
        rgb = cv2.cvtColor(native_img, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb.shape
        bytes_per_line = ch * w
        qimg = QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
        pm = QPixmap.fromImage(qimg)
        pm = pm.scaled(QSize(ref_w, ref_h), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.lbl.setPixmap(pm)
        self.display_pixmap = pm
        self.display_w = pm.width()
        self.display_h = pm.height()
        self.lbl.clicked.connect(self.on_click)
        self.setLayout(vbox)
        self.setMinimumSize(self.display_w + 40, self.display_h + 60)

    def on_click(self, dx: int, dy: int):
        label_w = self.lbl.width()
        label_h = self.lbl.height()
        off_x = (label_w - self.display_w) // 2
        off_y = (label_h - self.display_h) // 2
        rel_x = dx - off_x
        rel_y = dy - off_y
        if rel_x < 0 or rel_y < 0 or rel_x > self.display_w or rel_y > self.display_h:
            return
        scale_x = self.native_w / float(self.display_w)
        scale_y = self.native_h / float(self.display_h)
        nx = int(rel_x * scale_x)
        ny = int(rel_y * scale_y)
        data = load_coords()
        data[self.dev] = {"x": nx, "y": ny, "w": self.native_w, "h": self.native_h, "ref_w": self.ref_w, "ref_h": self.ref_h}
        if save_coords(data):
            self.accept()

class MainWindow(QMainWindow):
    sig_log = pyqtSignal(str)
    sig_popup = pyqtSignal(str)
    sig_status = pyqtSignal(str, str)
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Update 29/8/2025")
        self.setWindowIcon(QIcon("logo.png"))
        self.resize(280, 560)
        self.stop_event = threading.Event()
        self.workers = {}
        main_layout = QVBoxLayout()
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(6, 6, 6, 6)
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["#", "Device (ip:port)", "Status"])
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setStyleSheet("QTableWidget{border:1px solid gray;} QHeaderView::section{border:1px solid gray;background-color:#f0f0f0;} QTableWidget::item{border:1px solid gray;}")
        frame_emulator = QFrame()
        vbox_emulator = QVBoxLayout()
        vbox_emulator.setContentsMargins(0, 0, 0, 0)
        vbox_emulator.addWidget(self.table)
        frame_emulator.setLayout(vbox_emulator)
        main_layout.addWidget(frame_emulator)
        frame_logs = QFrame()
        vbox_logs = QVBoxLayout()
        vbox_logs.setContentsMargins(0, 0, 0, 0)
        lbl_logs = QLabel("Activity Log")
        lbl_logs.setStyleSheet("background-color:#f0f0f0;font-weight:bold;padding:4px;border:1px solid gray;")
        vbox_logs.addWidget(lbl_logs)
        self.logs = QTextEdit()
        self.logs.setReadOnly(True)
        self.logs.setStyleSheet("border:1px solid gray;border-top:none;")
        vbox_logs.addWidget(self.logs)
        frame_logs.setLayout(vbox_logs)
        main_layout.addWidget(frame_logs)
        tabs = QTabWidget()
        tabs.setStyleSheet("QTabWidget::pane{border:1px solid gray;} QTabBar::tab{padding:6px;}")
        tab_control = QWidget()
        layout_control = QVBoxLayout()
        hbox_global = QHBoxLayout()
        self.btn_open_game_global = QPushButton("Open Game (GB)")
        self.btn_close_game_global = QPushButton("Close Game (GB)")
        hbox_global.addWidget(self.btn_open_game_global)
        hbox_global.addWidget(self.btn_close_game_global)
        layout_control.addLayout(hbox_global)
        hbox_vn = QHBoxLayout()
        self.btn_open_game_vn = QPushButton("Open Game (VN)")
        self.btn_close_game_vn = QPushButton("Close Game (VN)")
        hbox_vn.addWidget(self.btn_open_game_vn)
        hbox_vn.addWidget(self.btn_close_game_vn)
        layout_control.addLayout(hbox_vn)
        tab_control.setLayout(layout_control)
        tabs.addTab(tab_control, "Game")
        tab_fog = QWidget()
        layout_fog = QVBoxLayout()
        hbox_setup = QHBoxLayout()
        self.btn_setup = QPushButton("Setup")
        self.input_w = QLineEdit("960")
        self.input_h = QLineEdit("540")
        self.input_w.setFixedWidth(60)
        self.input_h.setFixedWidth(60)
        lbl_w = QLabel("W:")
        lbl_h = QLabel("H:")
        hbox_setup.addWidget(self.btn_setup)
        hbox_setup.addWidget(lbl_w)
        hbox_setup.addWidget(self.input_w)
        hbox_setup.addWidget(lbl_h)
        hbox_setup.addWidget(self.input_h)
        hbox_setup.addStretch()
        layout_fog.addLayout(hbox_setup)
        self.chk_explore_fog = QCheckBox("Explore Fog")
        self.chk_explore_caves = QCheckBox("Explore Caves")
        self.chk_explore_other = QCheckBox("Explore Other")
        self.chk_auto_reconnect = QCheckBox("Auto Reconnect")
        self.chk_anti_ban = QCheckBox("Enable Anti-ban")
        self.chk_captcha = QCheckBox("Captcha Alert")
        self.chk_anti_ban.setChecked(True)
        self.chk_captcha.setChecked(True)
        layout_fog.addWidget(self.chk_explore_fog)
        layout_fog.addWidget(self.chk_explore_caves)
        layout_fog.addWidget(self.chk_explore_other)
        layout_fog.addWidget(self.chk_auto_reconnect)
        layout_fog.addWidget(self.chk_anti_ban)
        layout_fog.addWidget(self.chk_captcha)
        hbox_buttons = QHBoxLayout()
        self.btn_start_fog = QPushButton("▶ Start")
        self.btn_stop_fog = QPushButton("⏹ Stop")
        hbox_buttons.addWidget(self.btn_start_fog)
        hbox_buttons.addWidget(self.btn_stop_fog)
        layout_fog.addLayout(hbox_buttons)
        tab_fog.setLayout(layout_fog)
        tabs.addTab(tab_fog, "Fog")
        frame_tabs = QFrame()
        vbox_tabs = QVBoxLayout()
        vbox_tabs.setContentsMargins(0, 0, 0, 0)
        vbox_tabs.addWidget(tabs)
        frame_tabs.setLayout(vbox_tabs)
        main_layout.addWidget(frame_tabs)
        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)
        self.btn_open_game_global.clicked.connect(lambda: self.launch_close_game("open", "com.lilithgame.roc.gp"))
        self.btn_close_game_global.clicked.connect(lambda: self.launch_close_game("close", "com.lilithgame.roc.gp"))
        self.btn_open_game_vn.clicked.connect(lambda: self.launch_close_game("open", "com.rok.gp.vn"))
        self.btn_close_game_vn.clicked.connect(lambda: self.launch_close_game("close", "com.rok.gp.vn"))
        self.btn_refresh = QPushButton("🔄", self.table)
        self.btn_refresh.setFixedSize(28, 20)
        self.btn_refresh.clicked.connect(self.scan_and_connect)
        header = self.table.horizontalHeader()
        self.update_button_position()
        header.sectionResized.connect(self.update_button_position)
        header.geometriesChanged.connect(self.update_button_position)
        self.btn_start_fog.clicked.connect(self.start_fog_logic)
        self.btn_stop_fog.clicked.connect(self.stop_fog_logic)
        self.btn_setup.clicked.connect(self.run_setup_for_selected)
        self.sig_log.connect(self.on_log)
        self.sig_popup.connect(self.on_captcha_popup)
        self.sig_status.connect(self.on_set_status)

    def on_log(self, msg: str):
        now = time.strftime("%H:%M:%S")
        self.logs.append(f"{now} - {msg}")

    def on_captcha_popup(self, dev: str):
        halted_devices.add(dev)
        self.set_device_status(dev, "🛑 Captcha")
        QMessageBox.warning(self, "Captcha Detected", f"Device {dev} has triggered CAPTCHA.\nAll tasks on this device have been stopped.")

    def on_set_status(self, dev: str, status: str):
        self.set_device_status(dev, status)

    def log(self, msg: str):
        self.sig_log.emit(msg)

    def update_button_position(self):
        header = self.table.horizontalHeader()
        x = header.sectionPosition(0) + (header.sectionSize(0) - self.btn_refresh.width()) // 2
        y = (header.height() - self.btn_refresh.height()) // 2
        self.btn_refresh.move(x, y)
        self.btn_refresh.raise_()

    def get_selected_devices(self):
        devices = []
        for row in range(self.table.rowCount()):
            widget = self.table.cellWidget(row, 0)
            if widget:
                chk = widget.layout().itemAt(0).widget()
                if chk and chk.isChecked():
                    dev_item = self.table.item(row, 1)
                    if dev_item:
                        devices.append(dev_item.text())
        return devices

    def set_device_status(self, dev: str, text: str):
        for row in range(self.table.rowCount()):
            item_dev = self.table.item(row, 1)
            if item_dev and item_dev.text() == dev:
                item_status = QTableWidgetItem(text)
                item_status.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, 2, item_status)
                break

    def launch_close_game(self, action, pkg):
        selected = self.get_selected_devices()
        if not selected:
            self.log("No devices selected!")
            return
        for dev in selected:
            try:
                if dev in halted_devices:
                    self.log(f"[{dev}] Blocked due to CAPTCHA.")
                    continue
                if action == "open":
                    subprocess.run([ADB_PATH, "-s", dev, "shell", "monkey",
                                    "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self.log(f"Opened game ({pkg}) on {dev}")
                else:
                    subprocess.run([ADB_PATH, "-s", dev, "shell", "am", "force-stop", pkg],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self.log(f"Closed game ({pkg}) on {dev}")
            except Exception as e:
                self.log(f"Error on {dev}: {e}")

    def scan_and_connect(self):
        try:
            previously_selected = set(self.get_selected_devices())
            try_connect_ports()
            time.sleep(0.2)
            devices = get_ipport_devices()
            for dev in devices:
                ensure_connected(dev)
            parsed = dict(adb_devices_raw())
            self.table.setRowCount(len(devices))
            for row, dev in enumerate(devices):
                chk = QCheckBox()
                if dev in previously_selected:
                    chk.setChecked(True)
                layout = QHBoxLayout()
                layout.addWidget(chk)
                layout.setAlignment(Qt.AlignCenter)
                layout.setContentsMargins(0, 0, 0, 0)
                w = QWidget()
                w.setLayout(layout)
                self.table.setCellWidget(row, 0, w)
                item_dev = QTableWidgetItem(dev)
                item_dev.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, 1, item_dev)
                state = parsed.get(dev, "")
                ok = (state == "device")
                status_text = "🟢 Connected" if ok else "🔴 Error"
                item_status = QTableWidgetItem(status_text)
                item_status.setTextAlignment(Qt.AlignCenter)
                self.table.setItem(row, 2, item_status)
                if ok:
                    connected_devices.add(dev)
            self.log("Device list updated.")
        except Exception as e:
            self.log(f"Error scanning devices: {e}")

    def run_setup_for_selected(self):
        selected = self.get_selected_devices()
        if not selected:
            self.log("No device selected for Setup!")
            return
        try:
            ref_w = max(200, int(self.input_w.text().strip()))
            ref_h = max(200, int(self.input_h.text().strip()))
        except Exception:
            ref_w, ref_h = 960, 540
            self.input_w.setText("960")
            self.input_h.setText("540")
        for dev in selected:
            if dev in halted_devices:
                self.log(f"[{dev}] Blocked due to CAPTCHA.")
                continue
            img = adb_screencap_img(dev)
            if img is None:
                self.log(f"[{dev}] Could not get a screenshot.")
                continue
            dlg = SetupDialog(dev, img, ref_w, ref_h, self)
            if dlg.exec_() == QDialog.Accepted:
                self.log(f"[{dev}] Coordinates saved.")

    def start_fog_logic(self):
        selected = self.get_selected_devices()
        if not selected:
            self.log("No devices selected for Fog task!")
            return
        if not (self.chk_explore_fog.isChecked() or self.chk_explore_caves.isChecked() or self.chk_explore_other.isChecked()):
            self.log("No mode selected (Explore Fog/Caves/Other).")
            return
        self.stop_event.clear()
        for dev in selected:
            if dev in halted_devices:
                self.log(f"[{dev}] Blocked due to CAPTCHA.")
                continue
            if dev in self.workers and self.workers[dev].is_alive():
                continue
            anti_ban_on = self.chk_anti_ban.isChecked()
            reconnect_on = self.chk_auto_reconnect.isChecked()
            captcha_on = self.chk_captcha.isChecked()
            modes_on = {
                "fog": self.chk_explore_fog.isChecked(),
                "caves": self.chk_explore_caves.isChecked(),
                "other": self.chk_explore_other.isChecked()
            }
            t = threading.Thread(target=self.run_worker, args=(dev, anti_ban_on, reconnect_on, captcha_on, modes_on), daemon=True)
            t.start()
            self.workers[dev] = t
        self.log("Fog task started.")

    def stop_fog_logic(self):
        self.stop_event.set()
        self.log("Fog task stopped.")

    def run_worker(self, dev: str, anti_ban: bool, reconnect: bool, captcha_enabled: bool, modes: dict):
        def lg(msg): self.log(msg)
        def captcha_notify(d):
            self.sig_log.emit(f"[{d}] CAPTCHA detected. Stopping device tasks.")
            self.sig_popup.emit(d)
        lg(f"[{dev}] Worker started. Anti-ban: {'ON' if anti_ban else 'OFF'}")
        while not self.stop_event.is_set():
            if dev in halted_devices:
                break
            try:
                if captcha_enabled and do_captcha_check(dev, lg, self.stop_event):
                    captcha_notify(dev)
                    break
                if reconnect:
                    if do_reconnect_if_needed(dev, lg, self.stop_event, anti_ban):
                        if self.stop_event.is_set() or dev in halted_devices:
                            break
                mode_list = [
                    ("fog", modes["fog"]),
                    ("caves", modes["caves"]),
                    ("other", modes["other"]),
                ]
                selected_modes = [m for m, on in mode_list if on]
                if not selected_modes:
                    if self.stop_event.wait(1.0):
                        break
                    continue
                only_this_mode = lambda name: (selected_modes == [name])
                other_selected = lambda name: (len(selected_modes) > 1)
                done = False
                for name, is_on in mode_list:
                    if self.stop_event.is_set() or dev in halted_devices:
                        break
                    if not is_on:
                        continue
                    lg(f"[{dev}] Running mode: Explore {name.capitalize()}")
                    if name == "fog":
                        done = logic_explore_fog(dev, lg, only_this_mode("fog"), other_selected("fog"), self.stop_event, anti_ban, captcha_enabled, captcha_notify)
                    elif name == "caves":
                        done = logic_explore_caves(dev, lg, only_this_mode("caves"), other_selected("caves"), self.stop_event, anti_ban, captcha_enabled, captcha_notify)
                    elif name == "other":
                        done = logic_explore_other(dev, lg, only_this_mode("other"), other_selected("other"), self.stop_event, anti_ban, captcha_enabled, captcha_notify)
                    if dev in halted_devices:
                        break
                    if done or self.stop_event.is_set():
                        break
                if dev in halted_devices:
                    break
                if self.stop_event.wait(0.5):
                    break
            except Exception as e:
                lg(f"[{dev}] Worker error: {e}")
                if self.stop_event.wait(1.0):
                    break

if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec_())
