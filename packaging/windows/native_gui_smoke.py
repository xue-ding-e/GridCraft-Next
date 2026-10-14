#!/usr/bin/env python3
"""Real Windows x64 Rust GUI smoke gate. Python stdlib only; never substitutes an app.

Run on a disposable interactive GitHub windows-2022 host, once per delivery mode:
  python packaging/windows/native_gui_smoke.py --gui-exe C:/path/gridcraft-next.exe \
    --cli-exe C:/path/gridcraft-next-cli.exe --expected-version 0.6.1-next.0 \
    --source-sha <40-hex-sha> --mode portable --output-dir C:/new/evidence

All GUI edits use Win32 SendInput. Control calls are allowlisted read-only observations,
renderer screenshots, and explicitly labelled app.open/save/quit setup/lifetime.
No egui synthetic input, engine.execute mutation, graphics override, installation,
security bypass, credential use, or process-name based termination is performed.
An unfocusable/headless desktop FAILS. Evidence is retained for human visual review.
"""
from __future__ import annotations

import argparse
import collections
import contextlib
import ctypes
from ctypes import wintypes as w
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import traceback
import xml.etree.ElementTree as ET
import zipfile
import zlib


class SmokeError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise SmokeError(message)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def runner_guard(env, system, machine, pointer_size):
    expected = {"GITHUB_ACTIONS": "true", "RUNNER_OS": "Windows", "RUNNER_ENVIRONMENT": "github-hosted"}
    for key, value in expected.items():
        require(env.get(key) == value, f"Refusing non-disposable runner: {key} must be {value}")
    require(system == "Windows" and machine.upper() in ("AMD64", "X86_64") and pointer_size == 8,
            "Requires native Windows x64 and 64-bit Python")
    overrides = [k for k in env if k.upper().startswith(("WGPU_", "DXVK_", "MESA_"))]
    require(not overrides, f"Default graphics gate forbids inherited overrides: {overrides}")
    require(env.get("ImageOS") == "win22", "This gate targets the GitHub windows-2022 image (ImageOS=win22)")


def runtime_environment(env):
    root = env.get("SystemRoot")
    require(root, "SystemRoot is required for the isolated application PATH")
    result = dict(env)
    result["PATH"] = str(Path(root) / "System32") + ";" + root
    result["GRIDCRAFT_NO_PREFS"] = "1"
    return result


def pe_identity(path):
    with open(path, "rb") as f:
        head = f.read(64)
        require(len(head) == 64 and head[:2] == b"MZ", f"Not a PE executable: {path}")
        f.seek(struct.unpack_from("<I", head, 60)[0])
        pe = f.read(94)
    require(len(pe) == 94 and pe[:4] == b"PE\0\0", f"Invalid PE header: {path}")
    machine = struct.unpack_from("<H", pe, 4)[0]
    magic = struct.unpack_from("<H", pe, 24)[0]
    subsystem = struct.unpack_from("<H", pe, 24 + 68)[0]
    require(machine == 0x8664 and magic == 0x20B, f"Not an x64 PE32+ executable: {path}")
    return {"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size,
            "machine": machine, "optionalHeaderMagic": magic, "subsystem": subsystem}


def validate_png(path, minimum=(320, 240)):
    """Decode RGB/RGBA PNG filters, CRCs and actual pixels, not just an IHDR signature."""
    raw = Path(path).read_bytes()
    require(len(raw) < 80 * 1024 * 1024 and raw[:8] == b"\x89PNG\r\n\x1a\n", "Invalid/oversized PNG")
    pos, compressed, header, ended = 8, bytearray(), None, False
    while pos < len(raw):
        require(pos + 12 <= len(raw), "Truncated PNG chunk")
        size = struct.unpack_from(">I", raw, pos)[0]
        kind, payload = raw[pos + 4:pos + 8], raw[pos + 8:pos + 8 + size]
        require(pos + size + 12 <= len(raw), "Truncated PNG payload")
        crc = struct.unpack_from(">I", raw, pos + 8 + size)[0]
        require(zlib.crc32(kind + payload) & 0xFFFFFFFF == crc, "PNG CRC mismatch")
        if kind == b"IHDR":
            require(header is None and pos == 8 and size == 13, "Invalid PNG IHDR")
            header = struct.unpack(">IIBBBBB", payload)
        elif kind == b"IDAT":
            compressed.extend(payload)
        elif kind == b"IEND":
            require(size == 0, "Invalid PNG IEND")
            ended = True
            pos += 12
            break
        pos += size + 12
    require(header is not None and ended and pos == len(raw), "Incomplete PNG")
    width, height, depth, color, compression, filtering, interlace = header
    require(minimum[0] <= width <= 8192 and minimum[1] <= height <= 8192 and width * height <= 16_777_216,
            f"Invalid screenshot dimensions: {width}x{height}")
    require(depth == 8 and color in (2, 6) and (compression, filtering, interlace) == (0, 0, 0),
            "Expected noninterlaced 8-bit RGB/RGBA renderer PNG")
    channels = 4 if color == 6 else 3
    stride = width * channels
    expected = height * (stride + 1)
    decoder = zlib.decompressobj()
    pixels = decoder.decompress(bytes(compressed), expected + 1)
    require(decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail and len(pixels) == expected,
            "Invalid PNG decompressed size")
    previous = bytearray(stride)
    histogram = collections.Counter()
    for row in range(height):
        start = row * (stride + 1)
        mode = pixels[start]
        require(mode <= 4, "Invalid PNG row filter")
        current = bytearray(pixels[start + 1:start + stride + 1])
        for i in range(stride):
            left = current[i - channels] if i >= channels else 0
            above = previous[i]
            diagonal = previous[i - channels] if i >= channels else 0
            if mode == 1:
                predictor = left
            elif mode == 2:
                predictor = above
            elif mode == 3:
                predictor = (left + above) // 2
            elif mode == 4:
                p = left + above - diagonal
                distances = (abs(p - left), abs(p - above), abs(p - diagonal))
                predictor = (left, above, diagonal)[distances.index(min(distances))]
            else:
                predictor = 0
            current[i] = (current[i] + predictor) & 255
        for i in range(0, stride, channels):
            # Ignore alpha, so fully opaque black does not appear nonuniform.
            histogram[tuple(current[i:i + 3])] += 1
        previous = current
    non_dominant = 1 - histogram.most_common(1)[0][1] / (width * height)
    luminance = [sum(rgb) / 3 for rgb in histogram]
    require(len(histogram) >= 8 and non_dominant >= 0.01 and max(luminance) - min(luminance) >= 32,
            "Renderer PNG is blank, near-uniform, or insufficiently varied")
    return {"path": str(path), "sha256": sha256(path), "width": width, "height": height,
            "uniqueRgbColors": len(histogram), "nonDominantFraction": non_dominant,
            "humanVisualReviewRequired": True}


NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def xlsx_cells(path):
    with zipfile.ZipFile(path) as archive:
        require(sum(x.file_size for x in archive.infolist()) < 32 * 1024 * 1024, "Oversized smoke XLSX")
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in si.findall(".//s:t", NS)) for si in root]
        styles = ET.fromstring(archive.read("xl/styles.xml"))
        formats = {int(n.get("numFmtId")): n.get("formatCode") for n in styles.findall("s:numFmts/s:numFmt", NS)}
        formats[49] = "@"
        xfs = styles.findall("s:cellXfs/s:xf", NS)
        result = {}
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        for cell in sheet.findall(".//s:sheetData/s:row/s:c", NS):
            kind = cell.get("t", "n")
            value = cell.findtext("s:v", default="", namespaces=NS)
            if kind == "s":
                value = shared[int(value)]
            elif kind == "inlineStr":
                value = "".join(t.text or "" for t in cell.findall("s:is//s:t", NS))
            elif kind == "n" and value:
                value = float(value)
            style = xfs[int(cell.get("s", "0"))]
            result[cell.get("r")] = {"type": kind, "value": value,
                "formula": cell.findtext("s:f", namespaces=NS),
                "format": formats.get(int(style.get("numFmtId", "0")), int(style.get("numFmtId", "0")))}
        return result


def assert_xlsx(path, expected):
    cells = xlsx_cells(path)
    for address, wanted in expected.items():
        actual = cells.get(address)
        require(actual is not None, f"Missing XLSX cell {address}")
        value, kind, formula = wanted
        require(actual["value"] == value, f"XLSX {address}: {actual} != {wanted}")
        if kind == "text":
            require(actual["type"] in ("s", "inlineStr") and actual["format"] == "@",
                    f"XLSX identifier lost its string/Text format: {address}: {actual}")
        else:
            require(actual["type"] == "n", f"XLSX {address} is not numeric: {actual}")
        require(actual["formula"] == (formula[1:] if formula else None), f"XLSX formula mismatch: {address}: {actual}")
    return cells


def cli_result(observations, command):
    # `run` wraps non-null results in {command, result}; do not mistake the
    # transport envelope for a document or filter result.
    matches = [item["result"] for item in observations if isinstance(item, dict)
               and item.get("command") == command and "result" in item]
    require(len(matches) == 1 and isinstance(matches[0], dict), f"Expected one CLI result for {command}: {observations}")
    return matches[0]


class Evidence:
    def __init__(self, directory, args):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=False)
        self.path = self.directory / "evidence.json"
        self.data = {"schema": "gridcraft-native-windows-smoke/1", "status": "running", "startedUtc": utc_now(),
            "mode": args.mode, "sourceSha": args.source_sha, "expectedVersion": args.expected_version,
            "coverage": {"nativeInput": "Win32 SendInput; foreground HWND/PID checked before and after each batch",
                "controlSetup": ["app.open", "app.save", "app.quit"],
                "controlObservation": ["cell.get", "document.inspect", "ui.inspect", "ui.screenshot"],
                "eguiSyntheticInput": False, "manualVisualReview": "required; PNGs retained"}, "steps": []}
        self.write()

    def write(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.path)

    @contextlib.contextmanager
    def step(self, name, layer):
        item = {"name": name, "layer": layer, "status": "running", "startedUtc": utc_now()}
        self.data["steps"].append(item)
        self.write()
        start = time.monotonic()
        try:
            yield item
            item["status"] = "passed"
        except Exception as error:
            item.update(status="failed", error=str(error))
            raise
        finally:
            item["seconds"] = round(time.monotonic() - start, 3)
            self.write()


class Control:
    ALLOWED = {"cell.get", "document.inspect", "ui.inspect", "ui.screenshot", "app.open", "app.save", "app.quit"}

    def __init__(self, port, transcript, process):
        self.port, self.transcript, self.process, self.serial = port, transcript, process, 0

    def call(self, method, params=None, timeout=8):
        require(method in self.ALLOWED, f"Mutation/synthetic control method forbidden by harness: {method}")
        require(self.process.poll() is None, f"GUI exited with {self.process.returncode}")
        self.serial += 1
        request = {"id": self.serial, "method": method, "params": params or {}}
        entry = {"utc": utc_now(), "request": request}
        try:
            with socket.create_connection(("127.0.0.1", self.port), timeout=timeout) as connection:
                connection.settimeout(timeout)
                connection.sendall(json.dumps(request).encode("utf-8") + b"\n")
                with connection.makefile("rb") as reader:
                    line = reader.readline(8 * 1024 * 1024 + 1)
            require(line.endswith(b"\n") and len(line) <= 8 * 1024 * 1024, "Invalid control reply framing")
            reply = json.loads(line)
            entry["response"] = reply
            require(reply.get("id") == request["id"] and reply.get("ok") is True, f"Control failure: {reply}")
            return reply.get("result")
        except Exception as error:
            entry["error"] = str(error)
            raise
        finally:
            with self.transcript.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def wait_until(fn, description, timeout=20):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            value = fn()
            if value:
                return value
        except (SmokeError, OSError) as error:
            last = str(error)
        time.sleep(0.15)
    raise SmokeError(f"Timed out: {description}; last error: {last}")


class Win32:
    """Small instrumentation wrapper. This class is not the application under test."""
    def __init__(self, process, event_log):
        self.process, self.event_log, self.hwnd = process, event_log, None
        self.u = ctypes.WinDLL("user32", use_last_error=True)
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        ulong_ptr = ctypes.c_size_t
        class Mouse(ctypes.Structure):
            _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD),
                        ("dwFlags", w.DWORD), ("time", w.DWORD), ("dwExtraInfo", ulong_ptr)]
        class Keyboard(ctypes.Structure):
            _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD),
                        ("time", w.DWORD), ("dwExtraInfo", ulong_ptr)]
        class Hardware(ctypes.Structure):
            _fields_ = [("uMsg", w.DWORD), ("wParamL", w.WORD), ("wParamH", w.WORD)]
        class Union(ctypes.Union):
            _fields_ = [("mi", Mouse), ("ki", Keyboard), ("hi", Hardware)]
        class Input(ctypes.Structure):
            _anonymous_ = ("u",)
            _fields_ = [("type", w.DWORD), ("u", Union)]
        self.Input, self.Keyboard, self.Mouse = Input, Keyboard, Mouse
        require(ctypes.sizeof(Input) == 40, "Incorrect x64 Win32 INPUT ABI")
        self.EnumProc = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        signatures = {
            "EnumWindows": ([self.EnumProc, w.LPARAM], w.BOOL),
            "GetWindowThreadProcessId": ([w.HWND, ctypes.POINTER(w.DWORD)], w.DWORD),
            "IsWindowVisible": ([w.HWND], w.BOOL), "GetForegroundWindow": ([], w.HWND),
            "GetClientRect": ([w.HWND, ctypes.POINTER(w.RECT)], w.BOOL),
            "ClientToScreen": ([w.HWND, ctypes.POINTER(w.POINT)], w.BOOL),
            "WindowFromPoint": ([w.POINT], w.HWND),
            "SetForegroundWindow": ([w.HWND], w.BOOL), "ShowWindow": ([w.HWND, ctypes.c_int], w.BOOL),
            "SetCursorPos": ([ctypes.c_int, ctypes.c_int], w.BOOL),
            "SendInput": ([w.UINT, ctypes.POINTER(Input), ctypes.c_int], w.UINT),
            "GetAsyncKeyState": ([ctypes.c_int], w.SHORT),
            "SendMessageTimeoutW": ([w.HWND, w.UINT, w.WPARAM, w.LPARAM, w.UINT, w.UINT, ctypes.POINTER(ulong_ptr)], w.LPARAM),
            "OpenClipboard": ([w.HWND], w.BOOL), "CloseClipboard": ([], w.BOOL),
            "GetClipboardData": ([w.UINT], w.HANDLE),
        }
        for name, (argtypes, restype) in signatures.items():
            function = getattr(self.u, name)
            function.argtypes, function.restype = argtypes, restype
        self.k.GlobalLock.argtypes, self.k.GlobalLock.restype = [w.HGLOBAL], ctypes.c_void_p
        self.k.GlobalUnlock.argtypes, self.k.GlobalUnlock.restype = [w.HGLOBAL], w.BOOL
        self.k.GlobalSize.argtypes, self.k.GlobalSize.restype = [w.HGLOBAL], ctypes.c_size_t
        # This affects only the harness's coordinate interpretation, not display settings.
        self.u.SetProcessDpiAwarenessContext.argtypes = [w.HANDLE]
        self.u.SetProcessDpiAwarenessContext.restype = w.BOOL
        self.u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))

    def pid(self, hwnd):
        pid = w.DWORD()
        self.u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def rectangle(self, hwnd):
        rectangle = w.RECT()
        require(self.u.GetClientRect(hwnd, ctypes.byref(rectangle)), "GetClientRect failed")
        return rectangle

    def find_window(self):
        require(self.process.poll() is None, f"GUI exited: {self.process.returncode}")
        matches = []
        @self.EnumProc
        def callback(hwnd, _):
            if self.pid(hwnd) == self.process.pid and self.u.IsWindowVisible(hwnd):
                rect = w.RECT()
                if self.u.GetClientRect(hwnd, ctypes.byref(rect)) and rect.right >= 320 and rect.bottom >= 240:
                    matches.append(hwnd)
            return True
        self.u.EnumWindows(callback, 0)
        if matches:
            self.hwnd = matches[0]
            return self.hwnd
        return None

    def check_foreground(self):
        require(self.process.poll() is None, "GUI process is no longer alive")
        actual = self.u.GetForegroundWindow()
        require(actual == self.hwnd and self.pid(actual) == self.process.pid,
                f"Refusing OS input: foreground HWND/PID {actual}/{self.pid(actual)} != {self.hwnd}/{self.process.pid}")
        return {"hwnd": int(actual), "pid": self.process.pid}

    def focus(self):
        self.u.ShowWindow(self.hwnd, 9)
        self.u.SetForegroundWindow(self.hwnd)
        result = wait_until(self.check_foreground, "interactive desktop foreground ownership", 10)
        out = ctypes.c_size_t()
        require(self.u.SendMessageTimeoutW(self.hwnd, 0, 0, 0, 2, 1000, ctypes.byref(out)), "Window is hung")
        return result

    def listener_owner(self, port):
        # Close the free-port reservation race: never control another process.
        ip = ctypes.WinDLL("iphlpapi", use_last_error=True)
        function = ip.GetExtendedTcpTable
        function.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.DWORD), w.BOOL, w.ULONG, ctypes.c_int, w.ULONG]
        function.restype = w.DWORD
        size = w.DWORD()
        code = function(None, ctypes.byref(size), False, 2, 3, 0)
        require(code in (0, 122) and 4 <= size.value <= 16 * 1024 * 1024, "Cannot inspect TCP listener owners")
        buffer = ctypes.create_string_buffer(size.value)
        require(function(buffer, ctypes.byref(size), False, 2, 3, 0) == 0, "Cannot read TCP listener owners")
        count = struct.unpack_from("<I", buffer.raw)[0]
        require(4 + 24 * count <= size.value, "Malformed TCP owner table")
        for index in range(count):
            state, address, local_port, _, _, owner = struct.unpack_from("<6I", buffer.raw, 4 + 24 * index)
            if state == 2 and socket.ntohs(local_port & 65535) == port:
                require(socket.inet_ntoa(struct.pack("<I", address)) == "127.0.0.1", "Control listener is not loopback-only")
                require(owner == self.process.pid, f"Port {port} belongs to another PID: {owner}")
                return {"address": "127.0.0.1", "port": port, "pid": owner}
        return None

    def send(self, events, description):
        before = self.check_foreground()
        # Do not inject while a physical modifier is down; no global key-state reset.
        for key in (0x10, 0x11, 0x12, 0x5B, 0x5C):
            require(not self.u.GetAsyncKeyState(key) & 0x8000, "A modifier is already held; refusing OS input")
        batch = (self.Input * len(events))(*events)
        count = self.u.SendInput(len(events), batch, ctypes.sizeof(self.Input))
        after = self.check_foreground()
        require(count == len(events), f"SendInput injected {count}/{len(events)} events; error {ctypes.get_last_error()}")
        with self.event_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"utc": utc_now(), "action": description, "events": count,
                                "foregroundBefore": before, "foregroundAfter": after}) + "\n")
        time.sleep(0.12)

    def key_event(self, key=0, scan=0, flags=0):
        event = self.Input(type=1)
        event.ki = self.Keyboard(key, scan, flags, 0, 0)
        return event

    def key(self, key, ctrl=False):
        # Arrow/navigation keys use KEYEVENTF_EXTENDEDKEY, not scan-code injection.
        flags = 1 if key in (0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2E) else 0
        events = [self.key_event(key, flags=flags), self.key_event(key, flags=flags | 2)]
        if ctrl:
            events = [self.key_event(0x11)] + events + [self.key_event(0x11, flags=2)]
        self.send(events, f"key VK={key:#x} ctrl={ctrl}")

    def text(self, text):
        # KEYEVENTF_UNICODE enters UTF-16 through the OS input path and winit.
        units = struct.unpack("<" + "H" * (len(text.encode("utf-16-le")) // 2), text.encode("utf-16-le"))
        for unit in units:
            self.send([self.key_event(scan=unit, flags=4), self.key_event(scan=unit, flags=6)], "unicode typing")

    def click_grid(self, ui):
        self.check_foreground()
        width, height = ui["window"]
        x, y, grid_width, grid_height = ui["grid"]
        require(width > 0 and height > 0 and grid_width > 20 and grid_height > 20, "No usable grid")
        rectangle = self.rectangle(self.hwnd)
        point = w.POINT(round((x + 10) * rectangle.right / width), round((y + 10) * rectangle.bottom / height))
        require(0 <= point.x < rectangle.right and 0 <= point.y < rectangle.bottom, "Grid click outside client")
        require(self.u.ClientToScreen(self.hwnd, ctypes.byref(point)), "ClientToScreen failed")
        require(self.pid(self.u.WindowFromPoint(point)) == self.process.pid, "Another process covers the target grid")
        require(self.u.SetCursorPos(point.x, point.y), "SetCursorPos failed")
        down, up = self.Input(type=0), self.Input(type=0)
        down.mi, up.mi = self.Mouse(0, 0, 0, 2, 0, 0), self.Mouse(0, 0, 0, 4, 0, 0)
        self.send([down, up], f"native grid click screen=({point.x},{point.y})")

    def clipboard_text(self):
        if not self.u.OpenClipboard(self.hwnd):
            return None
        try:
            handle = self.u.GetClipboardData(13)  # CF_UNICODETEXT; only read app-generated synthetic text.
            if not handle:
                return None
            size = self.k.GlobalSize(handle)
            require(0 < size <= 65536, "Unexpected clipboard payload size")
            pointer = self.k.GlobalLock(handle)
            require(pointer, "GlobalLock clipboard failed")
            try:
                return ctypes.string_at(pointer, size).decode("utf-16-le").split("\0", 1)[0]
            finally:
                self.k.GlobalUnlock(handle)
        finally:
            self.u.CloseClipboard()


def run_command(args, directory, label, cwd=None, env=None, timeout=90):
    try:
        output = subprocess.run([str(x) for x in args], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        (directory / f"{label}.stdout.log").write_bytes(error.stdout or b"")
        (directory / f"{label}.stderr.log").write_bytes(error.stderr or b"")
        raise SmokeError(f"{label} timed out after {timeout}s; partial logs retained") from error
    (directory / f"{label}.stdout.log").write_bytes(output.stdout)
    (directory / f"{label}.stderr.log").write_bytes(output.stderr)
    require(output.returncode == 0, f"{label} exited {output.returncode}; see logs")
    return output.stdout.decode("utf-8-sig", errors="replace").strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gui-exe", required=True, type=Path)
    parser.add_argument("--cli-exe", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--mode", required=True, choices=("installed", "portable"))
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args(argv)
    evidence = Evidence(args.output_dir, args)
    directory = evidence.directory
    process = control = native = fixtures = None
    logs = []
    success = False
    try:
        with evidence.step("runner and immutable binary provenance", "preflight") as step:
            runner_guard(os.environ, platform.system(), platform.machine(), struct.calcsize("P"))
            require(10 <= args.timeout <= 180, "--timeout must be 10..180 seconds")
            require(re.fullmatch(r"[0-9a-fA-F]{40}", args.source_sha), "Source SHA must be 40 hex characters")
            require(os.environ.get("GITHUB_SHA", "").lower() == args.source_sha.lower(), "Source SHA must match GITHUB_SHA")
            args.gui_exe, args.cli_exe = args.gui_exe.resolve(strict=True), args.cli_exe.resolve(strict=True)
            gui, cli = pe_identity(args.gui_exe), pe_identity(args.cli_exe)
            require(gui["subsystem"] == 2 and cli["subsystem"] == 3, "Expected GUI subsystem 2 and CLI subsystem 3")
            evidence.data["binaries"] = {"gui": gui, "cli": cli}
            evidence.data["runner"] = {k: os.environ.get(k) for k in ("ImageOS", "ImageVersion", "RUNNER_OS", "RUNNER_ARCH", "RUNNER_ENVIRONMENT", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "GITHUB_SHA")}
            evidence.data["platform"] = platform.platform()
            evidence.data["python"] = sys.version
            # Resolve build metadata tools before isolating ONLY the app's PATH.
            # Rustup/toolchain and PowerShell directories are not app prerequisites.
            tool_paths = {name: shutil.which(name) for name in ("rustc", "cargo", "powershell.exe")}
            require(all(tool_paths.values()), f"Missing runner metadata tool: {tool_paths}")
            tool_paths = {name: str(Path(path).resolve(strict=True)) for name, path in tool_paths.items()}
            evidence.data["metadataToolPaths"] = tool_paths
            runtime_env = runtime_environment(os.environ)
            evidence.data["applicationPath"] = runtime_env["PATH"]
            for name, command in (("rustc", ["rustc", "-Vv"]), ("cargo", ["cargo", "-V"])):
                evidence.data[name] = run_command([tool_paths[name]] + command[1:], directory, name, timeout=20)
            version = run_command([args.cli_exe, "--version"], directory, "cli-version", cwd=directory, env=runtime_env, timeout=20)
            require(version == f"gridcraft-next-cli {args.expected_version}", f"CLI full version mismatch: {version}")
            env = dict(os.environ, GRIDCRAFT_SMOKE_GUI=str(args.gui_exe), GRIDCRAFT_SMOKE_CLI=str(args.cli_exe))
            script = "@($env:GRIDCRAFT_SMOKE_GUI,$env:GRIDCRAFT_SMOKE_CLI) | ForEach-Object { (Get-Item -LiteralPath $_).VersionInfo | Select-Object FileName,FileVersion,ProductVersion,ProductName,CompanyName,OriginalFilename } | ConvertTo-Json -Depth 4"
            metadata = json.loads(run_command([tool_paths["powershell.exe"], "-NoProfile", "-NonInteractive", "-Command", script], directory, "pe-version-info", env=env, timeout=20))
            require(metadata[0]["ProductName"] == "GridCraft-Next", f"Wrong GUI product identity: {metadata[0]}")
            evidence.data["peVersionInfo"] = metadata
            step["cliVersion"] = version
        fixtures = tempfile.TemporaryDirectory(prefix="GridCraft Native Smoke ")
        working = Path(fixtures.name)
        csv = working / "Synthetic Identifiers.csv"
        csv.write_text("identifier,code,decimal,result\n990000000000000001,00000000001,1.25,\n", encoding="utf-8")
        with evidence.step("real CLI eval, conversion, bulk row edit, undo redo and XLSX check", "cli") as step:
            value = run_command([args.cli_exe, "eval", "=SUM(1,2,3)"], directory, "cli-eval", cwd=working, env=runtime_env)
            require(value == "6", f"CLI eval result: {value}")
            converted = working / "converted.xlsx"
            run_command([args.cli_exe, "convert", csv, converted], directory, "cli-convert", cwd=working, env=runtime_env)
            assert_xlsx(converted, {"A2": ("990000000000000001", "text", None), "B2": ("00000000001", "text", None), "C2": (1.25, "number", None)})
            exported = run_command([args.cli_exe, "cat", converted, "--range", "A2:C2", "--csv"], directory, "cli-cat", cwd=working, env=runtime_env)
            require("990000000000000001" in exported and "00000000001" in exported and "1.25" in exported,
                    f"CLI cat lost identifier/decimal spelling: {exported}")
            edited = working / "cli-edited.xlsx"
            commands = [("home.numberFormat", {"range": "A:B", "format": "Text"}),
                ("home.insertRows", {"rows": "3:3"}),
                ("range.setValues", {"range": "A3", "values": [["990000000000000088", "00000000088", 2.5]]}),
                ("cell.set", {"cell": "D3", "input": "=SUM(C2:C3)"}), ("edit.undo", {}), ("edit.redo", {})]
            command = [args.cli_exe, "run", "--in", converted]
            for name, params in commands:
                command += ["--cmd", name + "=" + json.dumps(params, separators=(",", ":"))]
            run_command(command + ["--out", edited], directory, "cli-bulk-row-undo-redo", cwd=working, env=runtime_env)
            step["cells"] = assert_xlsx(edited, {"A3": ("990000000000000088", "text", None), "B3": ("00000000088", "text", None), "C3": (2.5, "number", None), "D3": (3.75, "number", "=SUM(C2:C3)")})
            report = json.loads(run_command([args.cli_exe, "check", edited, "--json"], directory, "cli-check", cwd=working, env=runtime_env))
            require(report["totals"]["opened"] == 1 and report["totals"]["roundTripFailed"] == 0 and report["totals"]["roundTripDiffering"] == 0, "CLI roundtrip check failed")
            step["check"] = report
            filtered = working / "cli-filtered.xlsx"
            filter_commands = [("data.filter", {"range": "A1:D3"}),
                ("data.filterBy", {"column": "C", "values": ["2.5"]}),
                ("selection.set", {"range": "A2:B3", "active": "A3"}), ("document.inspect", {})]
            command = [args.cli_exe, "run", "--in", edited]
            for name, params in filter_commands:
                command += ["--cmd", name + "=" + json.dumps(params, separators=(",", ":"))]
            output = run_command(command + ["--out", filtered], directory, "cli-filter-selection", cwd=working, env=runtime_env)
            observations = [json.loads(line) for line in output.splitlines() if line.strip()]
            require(cli_result(observations, "data.filterBy").get("hiddenRows") == 1, "CLI filter did not hide exactly one row")
            selection = cli_result(observations, "document.inspect")
            require(selection.get("selection") == "A2:B3" and selection.get("activeCell") == "A3",
                    "CLI multi-cell selection/active cell mismatch")
            with zipfile.ZipFile(filtered) as archive:
                sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
                hidden = {row.get("r") for row in sheet.findall("s:sheetData/s:row", NS) if row.get("hidden") in ("1", "true")}
                require(hidden == {"2"}, f"Saved filtered XLSX has wrong hidden rows: {hidden}")
                require(sheet.find("s:autoFilter", NS).get("ref") == "A1:D3", "Saved autofilter range mismatch")
            step["filterSelection"] = observations
        with evidence.step("default graphics real Rust launch, responsive native window and rendered grid", "native-window") as step:
            with socket.socket() as reserve:
                reserve.bind(("127.0.0.1", 0))
                port = reserve.getsockname()[1]
            # Port is a discovery hint, never sufficient evidence. A live same-PID window,
            # control responses, actual rendering and successful native edits are required.
            env = dict(runtime_env, RUST_LOG="warn,gridcraft*=info,wgpu_core=info,egui_wgpu=info")
            logs = [(directory / "gui.stdout.log").open("wb"), (directory / "gui.stderr.log").open("wb")]
            process = subprocess.Popen([str(args.gui_exe), "--control", str(port)], cwd=working,
                env=env, stdin=subprocess.DEVNULL, stdout=logs[0], stderr=logs[1])
            evidence.data["guiPid"] = process.pid
            control = Control(port, directory / "control.jsonl", process)
            native = Win32(process, directory / "native-input.jsonl")
            wait_until(native.find_window, "same-PID visible native window", args.timeout)
            step["listener"] = wait_until(lambda: native.listener_owner(port), "same-PID loopback control listener", args.timeout)
            step["foreground"] = native.focus()
            ui = wait_until(lambda: (lambda x: x if x.get("grid") and x["window"][0] >= 320 else None)(control.call("ui.inspect", timeout=2)), "rendered UI grid", args.timeout)
            step["ui"] = ui
            control.call("ui.screenshot", {"path": str(directory / "startup.png")}, timeout=15)
            step["screenshot"] = validate_png(directory / "startup.png")
        with evidence.step("open synthetic CSV and verify inferred identifiers", "control-setup-and-read") as step:
            control.call("app.open", {"path": str(csv)})
            initial = {"A2": ("990000000000000001", "text", None), "B2": ("00000000001", "text", None), "C2": (1.25, "number", None)}
            def cell(address, value, kind, formula=None):
                def matches():
                    actual = control.call("cell.get", {"cell": address}, timeout=3)
                    return actual if actual.get("value") == value and actual.get("type") == kind and actual.get("formula") == formula else None
                return wait_until(matches, f"cell {address} == {value!r} ({kind}, {formula})", 12)
            step["cells"] = {address: cell(address, *expected) for address, expected in initial.items()}
            native.focus()
            native.click_grid(control.call("ui.inspect"))
        def navigate(address):
            match = re.fullmatch(r"([A-E])([1-3])", address)
            require(match, "Smoke navigation only supports A1:E3")
            native.key(0x1B)
            native.key(0x24, ctrl=True)
            wait_until(lambda: control.call("document.inspect")["activeCell"] == "A1", "native Ctrl+Home selection")
            for _ in range(ord(match[1]) - ord("A")):
                native.key(0x27)
            for _ in range(int(match[2]) - 1):
                native.key(0x28)
            wait_until(lambda: control.call("document.inspect")["activeCell"] == address, f"native navigation to {address}")
        def edit(address, text, value, kind, formula=None):
            navigate(address)
            native.key(0x71)  # F2, confirmed editor before typing.
            wait_until(lambda: (control.call("ui.inspect").get("editor") or {}).get("cell") == address, f"native F2 editor at {address}")
            native.key(ord("A"), ctrl=True)
            native.text(text)
            wait_until(lambda: (control.call("ui.inspect").get("editor") or {}).get("text") == text, "native typed editor text")
            native.key(0x0D)
            return cell(address, value, kind, formula)
        with evidence.step("native identifier updates and decimal undo redo", "native-input-control-read") as step:
            step["A2"] = edit("A2", "990000000000000099", "990000000000000099", "text")
            step["B2"] = edit("B2", "00000000099", "00000000099", "text")
            step["C2"] = edit("C2", "12.5", 12.5, "number")
            native.key(ord("Z"), ctrl=True)
            step["undo"] = cell("C2", 1.25, "number")
            native.key(ord("Y"), ctrl=True)
            step["redo"] = cell("C2", 12.5, "number")
        with evidence.step("native create delete undo redo and OS clipboard copy paste", "native-input-control-read") as step:
            edit("E2", "smoke-delete", "smoke-delete", "text")
            navigate("E2")
            native.key(0x2E)
            cell("E2", None, "empty")
            native.key(ord("Z"), ctrl=True)
            cell("E2", "smoke-delete", "text")
            native.key(ord("Y"), ctrl=True)
            step["deleted"] = cell("E2", None, "empty")
            navigate("A2")
            native.key(ord("C"), ctrl=True)
            step["clipboard"] = wait_until(lambda: (lambda x: x if x and x.rstrip("\r\n") == "990000000000000099" else None)(native.clipboard_text()), "native copy to CF_UNICODETEXT")
            # Navigation sends Escape, clearing the app's internal copy state.
            # Paste the app-generated OS clipboard into an existing Text cell;
            # CSV per-cell formats do not imply a Text schema for blank A3.
            navigate("B2")
            native.key(ord("V"), ctrl=True)
            step["pasted"] = cell("B2", "990000000000000099", "text")
            native.key(ord("Z"), ctrl=True)
            step["pasteUndo"] = cell("B2", "00000000099", "text")
        with evidence.step("native formula and API first save", "native-input-control-save") as step:
            edit("D2", "=SUM(C2:C3)", 12.5, "number", "=SUM(C2:C3)")
            saved = working / "Native Saved Workbook.xlsx"
            control.call("app.save", {"path": str(saved)})
            require(saved.is_file(), "API first save produced no file")
            step["saveMethod"] = "app.save establishes a synthetic path; native Ctrl+S is tested next"
        with evidence.step("native edit and Ctrl+S persist formula dependency", "native-input-control-read") as step:
            edit("C3", "2.5", 2.5, "number")
            cell("D2", 15.0, "number", "=SUM(C2:C3)")
            require(control.call("document.inspect")["dirty"] is True, "Expected unsaved native edit before Ctrl+S")
            native.key(ord("S"), ctrl=True)
            wait_until(lambda: control.call("document.inspect")["dirty"] is False, "native Ctrl+S dirty reset")
            final = {"A2": ("990000000000000099", "text", None), "B2": ("00000000099", "text", None),
                "C2": (12.5, "number", None), "C3": (2.5, "number", None), "D2": (15.0, "number", "=SUM(C2:C3)")}
            step["xlsxCells"] = assert_xlsx(saved, final)
            control.call("ui.screenshot", {"path": str(directory / "edited.png")}, timeout=15)
            step["screenshot"] = validate_png(directory / "edited.png")
        with evidence.step("API reopen persisted workbook, native selection and renderer evidence", "control-reopen-native-read") as step:
            # file.open intentionally activates an already-open document without re-reading
            # disk. Close the saved document through native Ctrl+W before testing reopen.
            saved_title = control.call("document.inspect")["title"]
            native.key(ord("W"), ctrl=True)
            wait_until(lambda: all(d["title"] != saved_title for d in control.call("ui.inspect")["documents"]),
                       "native Ctrl+W removes saved document from memory")
            control.call("app.open", {"path": str(csv)})
            cell("A2", "990000000000000001", "text")
            control.call("app.open", {"path": str(saved)})
            step["cells"] = {address: cell(address, *expected) for address, expected in final.items()}
            native.focus()
            native.click_grid(control.call("ui.inspect"))
            navigate("D2")
            step["document"] = control.call("document.inspect")
            control.call("ui.screenshot", {"path": str(directory / "reopened.png")}, timeout=15)
            step["screenshot"] = validate_png(directory / "reopened.png")
            # Keep synthetic saved workbook as diagnostic evidence, not as an upstream sample.
            (directory / "synthetic-native-saved.xlsx").write_bytes(saved.read_bytes())
        with evidence.step("API quit and clean actual process exit", "control-lifetime") as step:
            control.call("app.quit")
            code = process.wait(timeout=20)
            step["exitCode"] = code
            require(code == 0, f"GUI did not exit cleanly: {code}")
        success = True
    except Exception as error:
        evidence.data["error"] = str(error)
        evidence.data["traceback"] = traceback.format_exc()
        if control and process and process.poll() is None:
            for method, filename in (("ui.inspect", "failure-ui.json"), ("document.inspect", "failure-document.json")):
                try:
                    (directory / filename).write_text(json.dumps(control.call(method, timeout=3), indent=2), encoding="utf-8")
                except Exception as diagnostic_error:
                    evidence.data.setdefault("diagnosticErrors", []).append(str(diagnostic_error))
            try:
                control.call("ui.screenshot", {"path": str(directory / "failure.png")}, timeout=12)
                evidence.data["failureScreenshot"] = validate_png(directory / "failure.png")
            except Exception as diagnostic_error:
                evidence.data.setdefault("diagnosticErrors", []).append(str(diagnostic_error))
    finally:
        if process and process.poll() is None:
            try:
                if control:
                    control.call("app.quit", timeout=3)
                process.wait(timeout=8)
            except Exception:
                # Exact Popen handle only. Never stop processes by name or touch unrelated PIDs.
                evidence.data["cleanupForcedTermination"] = True
                success = False
                try:
                    process.kill()
                    process.wait(timeout=10)
                except Exception as error:
                    evidence.data["cleanupProcessError"] = str(error)
        for stream in logs:
            stream.close()
        gui_log = directory / "gui.stderr.log"
        if gui_log.exists():
            evidence.data["graphicsLogLines"] = [line for line in gui_log.read_text(encoding="utf-8", errors="replace").splitlines()
                if "graphics:" in line or "adapter" in line.lower()]
        if fixtures:
            try:
                fixtures.cleanup()
            except OSError as error:
                evidence.data["cleanupError"] = str(error)
                success = False
        evidence.data["status"] = "passed" if success else "failed"
        evidence.data["finishedUtc"] = utc_now()
        evidence.data["artifacts"] = {p.name: {"sha256": sha256(p), "bytes": p.stat().st_size}
            for p in directory.iterdir() if p.is_file() and p.name != "evidence.json"}
        evidence.write()
    print(json.dumps({"status": evidence.data["status"], "evidence": str(evidence.path), "error": evidence.data.get("error")}))
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
