"""Portable unit tests for harness safeguards/parsers. These are NOT Windows GUI evidence."""
import argparse
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest import mock
import zipfile
import zlib

SOURCE = Path(__file__).resolve().parents[1] / "native_gui_smoke.py"
spec = importlib.util.spec_from_file_location("native_gui_smoke", SOURCE)
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


def chunk(kind, value):
    return struct.pack(">I", len(value)) + kind + value + struct.pack(">I", zlib.crc32(kind + value) & 0xFFFFFFFF)


def png(mode=0, uniform=False, channels=4):
    width, height = 16, 12
    rows, previous = bytearray(), bytearray(width * channels)
    for y in range(height):
        row = bytearray()
        for x in range(width):
            row.extend((0, 0, 0) if uniform else (x * 15, y * 20, (x + y) * 8))
            if channels == 4:
                row.append(255)
        filtered = bytearray()
        for i, value in enumerate(row):
            a = row[i - channels] if i >= channels else 0
            b = previous[i]
            c = previous[i - channels] if i >= channels else 0
            p = a + b - c
            distances = (abs(p - a), abs(p - b), abs(p - c))
            predictor = (0, a, b, (a + b) // 2, (a, b, c)[distances.index(min(distances))])[mode]
            filtered.append((value - predictor) & 255)
        rows.extend(bytes([mode]) + filtered)
        previous = row
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6 if channels == 4 else 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def book(path, identifier="990000000000000099", format_id="49", formula="SUM(C2:C3)"):
    ns = smoke.NS["s"]
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/styles.xml", f'<styleSheet xmlns="{ns}"><cellXfs><xf numFmtId="0"/><xf numFmtId="{format_id}"/></cellXfs></styleSheet>')
        archive.writestr("xl/sharedStrings.xml", f'<sst xmlns="{ns}"><si><t>{identifier}</t></si></sst>')
        archive.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{ns}"><sheetData><row r="2"><c r="A2" t="s" s="1"><v>0</v></c><c r="B2" t="inlineStr" s="1"><is><t>00000000099</t></is></c><c r="C2"><v>12.5</v></c><c r="D2"><f>{formula}</f><v>15</v></c></row></sheetData></worksheet>')


class SmokeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_png_decodes_all_filters_and_rgb_rgba(self):
        hashes = []
        for channels in (3, 4):
            for mode in range(5):
                path = self.directory / f"{mode}-{channels}.png"
                path.write_bytes(png(mode, channels=channels))
                result = smoke.validate_png(path, minimum=(1, 1))
                self.assertEqual((result["width"], result["height"]), (16, 12))
                self.assertTrue(result["humanVisualReviewRequired"])
                hashes.append(result["uniqueRgbColors"])
        self.assertEqual(len(set(hashes)), 1)

    def test_png_rejects_uniform_frame(self):
        path = self.directory / "black.png"
        path.write_bytes(png(uniform=True))
        with self.assertRaisesRegex(smoke.SmokeError, "blank"):
            smoke.validate_png(path, minimum=(1, 1))

    def test_png_rejects_corrupt_crc(self):
        path = self.directory / "corrupt.png"
        raw = bytearray(png())
        raw[29] ^= 1
        path.write_bytes(raw)
        with self.assertRaisesRegex(smoke.SmokeError, "CRC"):
            smoke.validate_png(path, minimum=(1, 1))

    def test_png_rejects_truncation_and_small_dimensions(self):
        path = self.directory / "invalid.png"
        path.write_bytes(png()[:-3])
        with self.assertRaises(smoke.SmokeError):
            smoke.validate_png(path, minimum=(1, 1))
        path.write_bytes(png())
        with self.assertRaisesRegex(smoke.SmokeError, "dimensions"):
            smoke.validate_png(path)

    def test_png_rejects_decompression_bomb(self):
        path = self.directory / "bomb.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(b"\0" * 100000)) + chunk(b"IEND", b""))
        with self.assertRaisesRegex(smoke.SmokeError, "size"):
            smoke.validate_png(path, minimum=(1, 1))

    def test_xlsx_checks_text_type_format_formula_numeric_precision(self):
        path = self.directory / "book.xlsx"
        book(path)
        result = smoke.assert_xlsx(path, {"A2": ("990000000000000099", "text", None), "B2": ("00000000099", "text", None), "C2": (12.5, "number", None), "D2": (15, "number", "=SUM(C2:C3)")})
        self.assertEqual(result["A2"]["format"], "@")

    def test_xlsx_rejects_digit_loss(self):
        path = self.directory / "book.xlsx"
        book(path, identifier="990000000000000000")
        with self.assertRaises(smoke.SmokeError):
            smoke.assert_xlsx(path, {"A2": ("990000000000000099", "text", None)})

    def test_xlsx_rejects_lost_text_format(self):
        path = self.directory / "book.xlsx"
        book(path, format_id="0")
        with self.assertRaisesRegex(smoke.SmokeError, "Text format"):
            smoke.assert_xlsx(path, {"A2": ("990000000000000099", "text", None)})

    def test_xlsx_rejects_changed_formula(self):
        path = self.directory / "book.xlsx"
        book(path, formula="15")
        with self.assertRaisesRegex(smoke.SmokeError, "formula mismatch"):
            smoke.assert_xlsx(path, {"D2": (15, "number", "=SUM(C2:C3)")})

    def test_pe_x64_and_subsystem(self):
        path = self.directory / "app.exe"
        data = bytearray(64 + 94)
        data[:2] = b"MZ"
        struct.pack_into("<I", data, 60, 64)
        data[64:68] = b"PE\0\0"
        struct.pack_into("<H", data, 68, 0x8664)
        struct.pack_into("<H", data, 88, 0x20B)
        struct.pack_into("<H", data, 156, 2)
        path.write_bytes(data)
        self.assertEqual(smoke.pe_identity(path)["subsystem"], 2)
        struct.pack_into("<H", data, 68, 0x14C)
        path.write_bytes(data)
        with self.assertRaisesRegex(smoke.SmokeError, "x64"):
            smoke.pe_identity(path)

    def test_runner_guard_accepts_only_hosted_x64_win22_without_overrides(self):
        env = {"GITHUB_ACTIONS": "true", "RUNNER_OS": "Windows", "RUNNER_ENVIRONMENT": "github-hosted", "ImageOS": "win22"}
        smoke.runner_guard(env, "Windows", "AMD64", 8)
        for updates in ({"RUNNER_ENVIRONMENT": "self-hosted"}, {"ImageOS": "win25"}, {"WGPU_BACKEND": "dx12"}, {"MESA_LOADER_DRIVER_OVERRIDE": "software"}):
            with self.assertRaises(smoke.SmokeError):
                smoke.runner_guard(dict(env, **updates), "Windows", "AMD64", 8)
        with self.assertRaises(smoke.SmokeError):
            smoke.runner_guard(env, "Linux", "x86_64", 8)

    def test_application_path_excludes_toolchain_and_preserves_parent(self):
        parent = {"SystemRoot": "C:/Windows", "PATH": "C:/toolchain;C:/build", "UNCHANGED": "value"}
        runtime = smoke.runtime_environment(parent)
        self.assertEqual(runtime["PATH"], str(Path("C:/Windows") / "System32") + ";C:/Windows")
        self.assertEqual(parent["PATH"], "C:/toolchain;C:/build")
        self.assertEqual(runtime["GRIDCRAFT_NO_PREFS"], "1")
        self.assertEqual(runtime["UNCHANGED"], "value")
        with self.assertRaises(smoke.SmokeError):
            smoke.runtime_environment({})

    def test_control_allowlist_forbids_mutation_and_synthetic_ui(self):
        process = mock.Mock()
        process.poll.return_value = None
        control = smoke.Control(1, self.directory / "control.jsonl", process)
        with mock.patch.object(smoke.socket, "create_connection") as connection:
            for method in ("engine.execute", "cell.set", "ui.text", "ui.key", "ui.cell.click", "ui.edit.begin", "ui.click"):
                with self.assertRaisesRegex(smoke.SmokeError, "forbidden"):
                    control.call(method)
            connection.assert_not_called()

    def test_cli_results_unwrap_only_the_expected_command(self):
        observations = [{"command": "data.filterBy", "result": {"hiddenRows": 1}},
                        {"command": "document.inspect", "result": {"selection": "A2:B3", "activeCell": "A3"}}]
        self.assertEqual(smoke.cli_result(observations, "data.filterBy"), {"hiddenRows": 1})
        self.assertEqual(smoke.cli_result(observations, "document.inspect")["activeCell"], "A3")
        with self.assertRaises(smoke.SmokeError):
            smoke.cli_result([{"hiddenRows": 1}], "data.filterBy")
        with self.assertRaises(smoke.SmokeError):
            smoke.cli_result(observations + observations, "data.filterBy")

    def test_control_response_requires_matching_id_and_ok(self):
        process = mock.Mock()
        process.poll.return_value = None
        control = smoke.Control(1234, self.directory / "control.jsonl", process)
        with mock.patch.object(smoke.socket, "create_connection") as connect:
            reader = connect.return_value.__enter__.return_value.makefile.return_value.__enter__.return_value
            reader.readline.return_value = b'{"id":123,"ok":true,"result":{}}\n'
            with self.assertRaisesRegex(smoke.SmokeError, "Control failure"):
                control.call("ui.inspect")
        self.assertIn('"error"', (self.directory / "control.jsonl").read_text())

    def test_os_input_refuses_foreground_mismatch_before_sendinput(self):
        native = smoke.Win32.__new__(smoke.Win32)
        native.process = mock.Mock(pid=123)
        native.process.poll.return_value = None
        native.hwnd = 100
        native.u = mock.Mock()
        native.u.GetForegroundWindow.return_value = 200
        native.pid = lambda _: 999
        with self.assertRaisesRegex(smoke.SmokeError, "Refusing OS input"):
            native.send([], "do not send")
        native.u.SendInput.assert_not_called()

    def test_evidence_records_failed_step_and_never_reuses_directory(self):
        args = argparse.Namespace(mode="portable", source_sha="a" * 40, expected_version="0.6.1-next.0")
        directory = self.directory / "evidence"
        evidence = smoke.Evidence(directory, args)
        with self.assertRaisesRegex(smoke.SmokeError, "expected"):
            with evidence.step("fails", "test"):
                raise smoke.SmokeError("expected")
        saved = json.loads(evidence.path.read_text())
        self.assertEqual(saved["steps"][0]["status"], "failed")
        with self.assertRaises(FileExistsError):
            smoke.Evidence(directory, args)

    def test_native_command_timeout_preserves_partial_logs(self):
        with mock.patch.object(smoke.subprocess, "run", side_effect=subprocess.TimeoutExpired(["fake"], 1, output=b"partial", stderr=b"failure")):
            with self.assertRaisesRegex(smoke.SmokeError, "timed out"):
                smoke.run_command(["fake"], self.directory, "timeout", timeout=1)
        self.assertEqual((self.directory / "timeout.stdout.log").read_bytes(), b"partial")
        self.assertEqual((self.directory / "timeout.stderr.log").read_bytes(), b"failure")

    def test_failed_preflight_returns_red_with_evidence(self):
        directory = self.directory / "failed"
        with mock.patch.dict(smoke.os.environ, {}, clear=True):
            code = smoke.main(["--gui-exe", "missing.exe", "--cli-exe", "missing-cli.exe", "--output-dir", str(directory), "--expected-version", "0.6.1-next.0", "--source-sha", "a" * 40, "--mode", "portable"])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads((directory / "evidence.json").read_text())["status"], "failed")

    def test_no_egui_event_mutation_in_main(self):
        source = SOURCE.read_text()
        for method in ("ui.text", "ui.key", "ui.cell.click", "engine.execute", "ui.edit.begin"):
            self.assertNotIn(f'control.call("{method}"', source)
        self.assertNotIn("taskkill", source)
        self.assertNotIn("Unblock-File", source)


if __name__ == "__main__":
    unittest.main()
