from dataclasses import dataclass
import shutil
import tempfile
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class HeadPose:
    yaw: float
    pitch: float
    roll: float


def classify_head_direction(yaw, left_threshold, right_threshold, invert=False):
    if yaw is None:
        return "unknown"
    if invert:
        yaw = -yaw
    if yaw <= left_threshold:
        return "left"
    if yaw >= right_threshold:
        return "right"
    return "center"


def classify_screen_mode(yaw, config):
    if yaw is None:
        return "none"

    matches = []
    for name in ("both", "left", "right"):
        center = float(config[f"{name}_center"])
        radius = max(0.1, float(config[f"{name}_range"]))
        if center - radius <= yaw <= center + radius:
            matches.append((abs(yaw - center), name))
    return min(matches)[1] if matches else "none"


def resolve_screen_mode(yaw, config):
    test_mode = config.get("test_mode", "auto")
    if test_mode in ("both", "left", "right", "none"):
        return test_mode
    return classify_screen_mode(yaw, config)


class HeadPoseEstimator:
    _LANDMARK_IDS = (1, 152, 33, 263, 61, 291)
    _MODEL_POINTS = np.array(
        [
            (0.0, 0.0, 0.0),
            (0.0, -330.0, -65.0),
            (-225.0, 170.0, -135.0),
            (225.0, 170.0, -135.0),
            (-150.0, -150.0, -125.0),
            (150.0, -150.0, -125.0),
        ],
        dtype=np.float64,
    )

    def __init__(
        self,
        model_path=None,
        min_detection_confidence=0.5,
        min_tracking_confidence=0.5,
    ):
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision

        source = Path(model_path or Path(__file__).parent / "weights" / "face_landmarker.task")
        if not source.is_file():
            raise FileNotFoundError(
                f"Missing face landmark model: {source}. "
                "Download face_landmarker.task into weights."
            )
        if not str(source).isascii():
            model_copy = Path(tempfile.gettempdir()) / "moyu_yolo_face_landmarker.task"
            if not model_copy.is_file() or model_copy.stat().st_size != source.stat().st_size:
                shutil.copyfile(source, model_copy)
            source = model_copy
        options = vision.FaceLandmarkerOptions(
            base_options=python.BaseOptions(model_asset_path=str(source)),
            running_mode=vision.RunningMode.VIDEO,
            num_faces=1,
            output_facial_transformation_matrixes=True,
            min_face_detection_confidence=min_detection_confidence,
            min_face_presence_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        self.mp = mp
        self.landmarker = vision.FaceLandmarker.create_from_options(options)
        self.timestamp_ms = 0

    def estimate(self, frame, timestamp_ms=None):
        if frame is None or frame.size == 0:
            return None
        height, width = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=rgb)
        if timestamp_ms is None:
            self.timestamp_ms += 33
            timestamp_ms = self.timestamp_ms
        else:
            self.timestamp_ms = max(self.timestamp_ms + 1, int(timestamp_ms))
            timestamp_ms = self.timestamp_ms
        result = self.landmarker.detect_for_video(image, timestamp_ms)
        if not result.face_landmarks:
            return None
        if result.facial_transformation_matrixes:
            transform = np.asarray(
                result.facial_transformation_matrixes[0],
                dtype=np.float64,
            ).reshape(4, 4)
            rotation_matrix = transform[:3, :3]
            angles, *_ = cv2.RQDecomp3x3(rotation_matrix)
            pitch, yaw, roll = [float(value) for value in angles]
            return HeadPose(yaw=yaw, pitch=pitch, roll=roll)
        landmarks = result.face_landmarks[0]
        image_points = np.array(
            [
                (landmarks[index].x * width, landmarks[index].y * height)
                for index in self._LANDMARK_IDS
            ],
            dtype=np.float64,
        )
        focal_length = float(width)
        camera_matrix = np.array(
            [
                [focal_length, 0.0, width / 2.0],
                [0.0, focal_length, height / 2.0],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        )
        distortion = np.zeros((4, 1), dtype=np.float64)
        success, rotation_vector, _translation = cv2.solvePnP(
            self._MODEL_POINTS,
            image_points,
            camera_matrix,
            distortion,
            flags=cv2.SOLVEPNP_ITERATIVE,
        )
        if not success:
            return None
        rotation_matrix, _ = cv2.Rodrigues(rotation_vector)
        angles, *_ = cv2.RQDecomp3x3(rotation_matrix)
        pitch, yaw, roll = [float(value) for value in angles]
        return HeadPose(yaw=yaw, pitch=pitch, roll=roll)

    def close(self):
        self.landmarker.close()
