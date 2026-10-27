import unittest
import contextlib
import io
import json
import socket
import struct
import time
from pathlib import Path
from urllib.request import Request, urlopen
from tempfile import TemporaryDirectory

import cv2
import numpy as np

from detect_camera import (
    face_looks_toward_camera,
    has_moved,
    is_background_candidate,
    is_background_person,
    is_camera_owner,
    is_trigger_signal,
    has_required_people,
    advance_face_presence,
    save_trigger_frame,
    title_matches_window,
    window_match_fragments,
)
from head_debug import DEFAULT_CONFIG, HeadDebugServer, normalize_config
from head_pose import (
    classify_head_direction,
    classify_screen_mode,
    resolve_screen_mode,
)


class FakeFaceDetector:
    def __init__(self, faces):
        self.faces = faces

    def detectMultiScale(self, *_args, **_kwargs):
        return self.faces


class DetectionLogicTests(unittest.TestCase):
    def test_background_zone_ignores_lower_self_region(self):
        self.assertTrue(is_background_person((100, 50, 200, 250), 640, 480, 0.72))
        self.assertFalse(is_background_person((100, 250, 200, 470), 640, 480, 0.72))

    def test_camera_owner_is_centered_large_and_touches_bottom(self):
        self.assertTrue(is_camera_owner((188, 236, 454, 479), 640, 480))
        self.assertFalse(is_camera_owner((10, 50, 100, 300), 640, 480))

    def test_passing_person_near_left_edge_is_not_camera_owner(self):
        self.assertFalse(is_camera_owner((317, 336, 478, 685), 1280, 720))

    def test_camera_owner_is_excluded_even_when_box_center_is_in_zone(self):
        owner_box = (200, 200, 440, 450)
        self.assertTrue(is_background_person(owner_box, 640, 480, 0.72))
        self.assertTrue(is_camera_owner(owner_box, 640, 480))
        self.assertFalse(is_background_candidate(owner_box, 640, 480, 0.72))

    def test_track_movement_requires_three_observations(self):
        history = []
        self.assertFalse(has_moved(history, (10, 10), 10))
        self.assertFalse(has_moved(history, (12, 10), 10))
        self.assertTrue(has_moved(history, (25, 10), 10))

    def test_stationary_track_does_not_count_as_passing(self):
        history = []
        for _ in range(10):
            self.assertFalse(has_moved(history, (100, 100), 18))

    def test_frontal_face_result_controls_look_toward_signal(self):
        crop = np.zeros((48, 48, 3), dtype=np.uint8)
        self.assertTrue(face_looks_toward_camera(FakeFaceDetector([(1, 1, 24, 24)]), crop))
        self.assertFalse(face_looks_toward_camera(FakeFaceDetector(()), crop))

    def test_look_signal_is_disabled_by_default(self):
        self.assertFalse(is_trigger_signal(False, True, False, False))
        self.assertTrue(is_trigger_signal(False, True, False, True))
        self.assertFalse(is_trigger_signal(False, True, True, True))

    def test_people_trigger_uses_minimum_person_count(self):
        self.assertFalse(has_required_people(0))
        self.assertFalse(has_required_people(1))
        self.assertTrue(has_required_people(2))
        self.assertTrue(has_required_people(3, minimum=3))

    def test_absence_and_return_require_stable_timeouts(self):
        state, missing, present, transition = advance_face_presence(
            "present", None, None, False, 10.0, 5.0, 1.0
        )
        self.assertEqual((state, transition), ("present", None))
        state, missing, present, transition = advance_face_presence(
            state, missing, present, False, 15.1, 5.0, 1.0
        )
        self.assertEqual((state, transition), ("absent", "absent"))

        state, missing, present, transition = advance_face_presence(
            state, missing, present, True, 20.0, 5.0, 1.0
        )
        self.assertEqual((state, transition), ("absent", None))
        state, missing, present, transition = advance_face_presence(
            state, missing, present, True, 21.1, 5.0, 1.0
        )
        self.assertEqual((state, transition), ("present", "present"))

    def test_head_direction_thresholds_and_inversion(self):
        self.assertEqual(classify_head_direction(-20, -12, 12), "left")
        self.assertEqual(classify_head_direction(20, -12, 12), "right")
        self.assertEqual(classify_head_direction(0, -12, 12), "center")
        self.assertEqual(classify_head_direction(-20, -12, 12, invert=True), "right")
        self.assertEqual(classify_head_direction(None, -12, 12), "unknown")

    def test_screen_mode_uses_three_calibrated_ranges(self):
        config = {
            "both_center": 0,
            "both_range": 5,
            "left_center": 25,
            "left_range": 8,
            "right_center": -30,
            "right_range": 8,
        }
        self.assertEqual(classify_screen_mode(0, config), "both")
        self.assertEqual(classify_screen_mode(30, config), "left")
        self.assertEqual(classify_screen_mode(-35, config), "right")
        self.assertEqual(classify_screen_mode(12, config), "none")
        self.assertEqual(classify_screen_mode(None, config), "none")

    def test_overlapping_screen_ranges_choose_nearest_center(self):
        config = {
            "both_center": 0,
            "both_range": 20,
            "left_center": 15,
            "left_range": 20,
            "right_center": -30,
            "right_range": 5,
        }
        self.assertEqual(classify_screen_mode(12, config), "left")

    def test_test_mode_overrides_yaw_and_auto_uses_calibration(self):
        config = {
            "both_center": 0,
            "both_range": 5,
            "left_center": 25,
            "left_range": 8,
            "right_center": -30,
            "right_range": 8,
            "test_mode": "left",
        }
        self.assertEqual(resolve_screen_mode(-30, config), "left")
        config["test_mode"] = "auto"
        self.assertEqual(resolve_screen_mode(-30, config), "right")
        config["test_mode"] = "none"
        self.assertEqual(resolve_screen_mode(0, config), "none")

    def test_calibration_config_clamps_invalid_values(self):
        config = normalize_config(
            {
                "both_center": 180,
                "left_range": 0,
                "right_center": "nan",
                "test_mode": "invalid",
            }
        )
        self.assertEqual(config["both_center"], 90)
        self.assertEqual(config["left_range"], 0.5)
        self.assertEqual(config["right_center"], DEFAULT_CONFIG["right_center"])
        self.assertEqual(config["test_mode"], "auto")

    def test_debug_http_config_reads_and_persists_updates(self):
        with TemporaryDirectory() as directory:
            config_path = Path(directory) / "calibration.json"
            server = HeadDebugServer(port=0, config_path=config_path)
            try:
                with urlopen(server.url, timeout=3) as response:
                    page = response.read().decode("utf-8")
                    self.assertEqual(response.status, 200)
                    self.assertIn("屏幕中心点和范围", page)
                with urlopen(server.url + "config", timeout=3) as response:
                    initial = json.loads(response.read())
                self.assertEqual(initial["test_mode"], "auto")

                update = dict(initial, both_center=3.5, test_mode="right")
                request = Request(
                    server.url + "config",
                    data=json.dumps(update).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(request, timeout=3) as response:
                    saved = json.loads(response.read())
                self.assertEqual(saved["both_center"], 3.5)
                self.assertEqual(saved["test_mode"], "right")
                self.assertEqual(json.loads(config_path.read_text())["test_mode"], "right")
            finally:
                server.close()

    def test_debug_server_silences_browser_disconnect_errors(self):
        with TemporaryDirectory() as directory:
            server = HeadDebugServer(port=0, config_path=Path(directory) / "config.json")
            errors = io.StringIO()
            client = socket.create_connection(
                ("127.0.0.1", server.server.server_port), timeout=3
            )
            client.sendall(
                f"GET /events HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".encode("ascii")
            )
            client.recv(1024)
            client.setsockopt(
                socket.SOL_SOCKET,
                socket.SO_LINGER,
                struct.pack("hh", 1, 0),
            )
            client.close()
            try:
                with contextlib.redirect_stderr(errors):
                    server.publish({"frame_number": 1})
                    time.sleep(0.2)
                self.assertNotIn("Exception occurred during processing", errors.getvalue())
            finally:
                server.close()

    def test_trigger_frame_is_saved_as_jpeg(self):
        with TemporaryDirectory() as directory:
            path = save_trigger_frame(np.zeros((12, 16, 3), dtype=np.uint8), directory)
            self.assertTrue(path.is_file())
            self.assertEqual(path.suffix, ".jpg")
            self.assertIsNotNone(cv2.imread(str(path)))

    def test_window_title_falls_back_to_stable_suffix(self):
        selected = "扩展: vscode-wechat - XinTongMeetingAgent__CLONED - Visual Studio Code"
        current = "main.py - OtherProject - Visual Studio Code"
        self.assertEqual(window_match_fragments(selected)[-1], "Visual Studio Code")
        self.assertTrue(title_matches_window(current, selected))


if __name__ == "__main__":
    unittest.main()
