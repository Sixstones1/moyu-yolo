import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import cv2
import numpy as np

from detect_camera import (
    face_looks_toward_camera,
    has_moved,
    is_background_candidate,
    is_background_person,
    is_camera_owner,
    save_trigger_frame,
    title_matches_window,
    window_match_fragments,
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
