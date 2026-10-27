import argparse
import base64
import ctypes
import shutil
import tempfile
import time
from collections import defaultdict, deque
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

from head_pose import HeadPoseEstimator, classify_head_direction
from head_debug import HeadDebugServer
import cv2
import numpy as np


def window_match_fragments(title_fragment):
    title_fragment = title_fragment.strip()
    fragments = [title_fragment] if title_fragment else []
    if " - " in title_fragment:
        stable_suffix = title_fragment.rsplit(" - ", 1)[-1].strip()
        if len(stable_suffix) >= 4 and stable_suffix.casefold() not in {
            fragment.casefold() for fragment in fragments
        }:
            fragments.append(stable_suffix)
    return fragments


def title_matches_window(window_title, title_fragment):
    window_title = window_title.casefold()
    return any(
        fragment.casefold() in window_title
        for fragment in window_match_fragments(title_fragment)
    )


def activate_window(title_fragment, hwnd=None):
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    matches = []

    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsWindow.argtypes = [wintypes.HWND]
    user32.IsWindow.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.BringWindowToTop.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL
    user32.SetFocus.argtypes = [wintypes.HWND]
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    if hwnd and user32.IsWindow(hwnd) and user32.IsWindowVisible(hwnd):
        length = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        if title.value:
            matches.append((hwnd, title.value))

    @enum_proc
    def callback(hwnd, _):
        if matches:
            return True
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        if title_matches_window(title.value, title_fragment):
            matches.append((hwnd, title.value))
        return True

    user32.EnumWindows(callback, 0)
    if not matches:
        return None

    hwnd, title = matches[0]
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)
    foreground = user32.GetForegroundWindow()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None)
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    current_thread = kernel32.GetCurrentThreadId()
    attached_threads = []
    for thread_id in {foreground_thread, target_thread}:
        if thread_id and thread_id != current_thread:
            if user32.AttachThreadInput(current_thread, thread_id, True):
                attached_threads.append(thread_id)
    try:
        user32.BringWindowToTop(hwnd)
        activated = bool(user32.SetForegroundWindow(hwnd))
        user32.SetFocus(hwnd)
    finally:
        for thread_id in attached_threads:
            user32.AttachThreadInput(current_thread, thread_id, False)
    return title if activated or user32.GetForegroundWindow() == hwnd else None


def list_window_records():
    user32 = ctypes.windll.user32
    windows = []
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL

    @enum_proc
    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                title = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, title, length + 1)
                windows.append((hwnd, title.value))
        return True

    user32.EnumWindows(callback, 0)
    return sorted(windows, key=lambda item: item[1].casefold())


def list_windows():
    return [title for _, title in list_window_records()]


def is_background_person(box, frame_width, frame_height, zone_bottom):
    x1, y1, x2, y2 = box
    center_x = (x1 + x2) / 2
    center_y = (y1 + y2) / 2
    return 0 <= center_x < frame_width and 0 <= center_y < frame_height * zone_bottom


def is_camera_owner(box, frame_width, frame_height):
    x1, y1, x2, y2 = box
    center_x = (x1 + x2) / 2
    return (
        frame_width * 0.40 < center_x < frame_width * 0.60
        and y2 > frame_height * 0.92
        and y2 - y1 > frame_height * 0.38
    )


def is_background_candidate(box, frame_width, frame_height, zone_bottom):
    return (
        is_background_person(box, frame_width, frame_height, zone_bottom)
        and not is_camera_owner(box, frame_width, frame_height)
    )


def has_moved(history, center, minimum_pixels):
    history.append(center)
    if len(history) < 3:
        return False
    horizontal = [point[0] for point in history]
    vertical = [point[1] for point in history]
    return (
        max(horizontal) - min(horizontal) >= minimum_pixels
        or max(vertical) - min(vertical) >= minimum_pixels
    )


def create_face_detector():
    source = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
    if not source.exists():
        print(f"Face orientation disabled: missing {source}")
        return None
    detector_path = source
    if not str(source).isascii():
        detector_path = Path(tempfile.gettempdir()) / "person_watch_frontalface.xml"
        shutil.copyfile(source, detector_path)
    detector = cv2.CascadeClassifier(str(detector_path))
    if detector.empty():
        print("Face orientation disabled: could not load the frontal-face classifier")
        return None
    return detector


def face_looks_toward_camera(face_detector, crop):
    if crop.size == 0:
        return False
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    faces = face_detector.detectMultiScale(
        gray,
        scaleFactor=1.05,
        minNeighbors=3,
        minSize=(20, 20),
    )
    return len(faces) > 0


def is_trigger_signal(passed, facing_camera, owner, look_trigger):
    return passed or (look_trigger and facing_camera and not owner)


def save_trigger_frame(frame, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"trigger_{datetime.now():%Y%m%d_%H%M%S_%f}.jpg"
    if not cv2.imwrite(str(path), frame):
        raise OSError(f"Could not save trigger frame to {path}")
    return path


def main():
    parser = argparse.ArgumentParser(
        description="Detect people behind the camera owner and switch to an IDE."
    )
    parser.add_argument("--camera", type=int, default=0, help="Camera index (default: 0)")
    parser.add_argument(
        "--video",
        help="Replay a recorded video instead of opening a webcam",
    )
    parser.add_argument(
        "--model",
        default=str(Path(__file__).parent / "weights" / "yolo26n.pt"),
        help="YOLO model path",
    )
    parser.add_argument(
        "--ide-title",
        default="Visual Studio Code",
        help="Window-title text used to find the IDE",
    )
    parser.add_argument(
        "--ide-hwnd",
        type=int,
        default=0,
        help="Selected window handle; survives title changes",
    )
    parser.add_argument("--confidence", type=float, default=0.35)
    parser.add_argument("--trigger-frames", type=int, default=6)
    parser.add_argument(
        "--movement-pixels",
        type=int,
        default=18,
        help="Minimum tracked-center movement in pixels",
    )
    parser.add_argument("--cooldown", type=float, default=10.0)
    parser.add_argument(
        "--zone-bottom",
        type=float,
        default=0.72,
        help="Bottom edge of the background zone as a fraction of frame height",
    )
    parser.add_argument(
        "--look-trigger",
        action="store_true",
        help="Also trigger for a non-owner person facing the camera",
    )
    parser.add_argument(
        "--head-direction",
        action="store_true",
        help="Estimate the camera owner's head direction in the same camera loop",
    )
    parser.add_argument(
        "--head-process-every",
        type=int,
        default=2,
        help="Run head-pose estimation every N frames",
    )
    parser.add_argument("--head-left-threshold", type=float, default=-12.0)
    parser.add_argument("--head-right-threshold", type=float, default=12.0)
    parser.add_argument("--head-stability-frames", type=int, default=5)
    parser.add_argument("--head-smoothing-frames", type=int, default=5)
    parser.add_argument(
        "--head-yaw-invert",
        action="store_true",
        help="Invert yaw before classifying left and right",
    )
    parser.add_argument(
        "--debug-web",
        action="store_true",
        help="Serve a local real-time detection debug page",
    )
    parser.add_argument("--debug-web-port", type=int, default=8765)
    parser.add_argument("--no-preview", action="store_true")
    parser.add_argument(
        "--capture-dir",
        default=str(Path(__file__).parent / "captures"),
        help="Directory for trigger-frame screenshots",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report triggers without activating the IDE",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=0,
        help="Exit after this many frames; 0 runs until q is pressed",
    )
    args = parser.parse_args()

    if not 0 < args.confidence < 1:
        parser.error("--confidence must be between 0 and 1")
    if args.trigger_frames < 1:
        parser.error("--trigger-frames must be at least 1")
    if args.movement_pixels < 1:
        parser.error("--movement-pixels must be at least 1")
    if args.head_process_every < 1:
        parser.error("--head-process-every must be at least 1")
    if args.head_stability_frames < 1:
        parser.error("--head-stability-frames must be at least 1")
    if args.head_smoothing_frames < 1:
        parser.error("--head-smoothing-frames must be at least 1")
    if args.head_left_threshold >= args.head_right_threshold:
        parser.error("--head-left-threshold must be less than --head-right-threshold")
    if not 0 < args.debug_web_port < 65536:
        parser.error("--debug-web-port must be between 1 and 65535")
    if args.max_frames < 0:
        parser.error("--max-frames must be non-negative")
    if args.cooldown < 0:
        parser.error("--cooldown must be non-negative")
    if not 0 < args.zone_bottom <= 1:
        parser.error("--zone-bottom must be in (0, 1]")

    head_estimator = HeadPoseEstimator() if args.head_direction else None
    if head_estimator is not None:
        print("Head pose enabled: MediaPipe Face Landmarker", flush=True)
    from ultralytics import YOLO

    model = YOLO(args.model)
    camera = (
        cv2.VideoCapture(args.video)
        if args.video
        else cv2.VideoCapture(args.camera, cv2.CAP_DSHOW)
    )
    if not camera.isOpened():
        source = args.video or f"camera {args.camera}"
        raise RuntimeError(f"Could not open {source}")

    consecutive = 0
    last_trigger = 0.0
    track_history = defaultdict(lambda: deque(maxlen=10))
    frames_seen = 0
    face_detector = create_face_detector() if args.look_trigger else None
    head_yaws = deque(maxlen=args.head_smoothing_frames)
    head_direction = "unknown"
    head_candidate = "unknown"
    head_candidate_frames = 0
    head_missing_frames = 0
    last_head_yaw = None
    last_head_pitch = None
    last_head_roll = None
    smoothed_yaw = None
    debug_server = None
    if args.debug_web:
        debug_server = HeadDebugServer(port=args.debug_web_port)
        print(f"Debug web: {debug_server.url}", flush=True)
    frame_started = time.monotonic()
    frame_rate = 0.0
    try:
        while True:
            if args.max_frames and frames_seen >= args.max_frames:
                print(f"Stopped after {frames_seen} frames")
                break
            ok, frame = camera.read()
            if not ok:
                if args.video:
                    print(f"Reached end of video after {frames_seen} frames")
                    break
                raise RuntimeError("Could not read a frame from the camera")
            frames_seen += 1

            height, width = frame.shape[:2]
            zone_bottom = int(height * args.zone_bottom)
            result = model.track(
                frame,
                classes=[0],
                conf=args.confidence,
                persist=True,
                tracker="bytetrack.yaml",
                verbose=False,
            )[0]
            trigger_person = False

            if head_estimator is not None and frames_seen % args.head_process_every == 0:
                pose = head_estimator.estimate(frame, frames_seen * 33)
                if pose is None:
                    head_missing_frames += 1
                else:
                    head_missing_frames = 0
                    last_head_yaw = pose.yaw
                    last_head_pitch = pose.pitch
                    last_head_roll = pose.roll
                    head_yaws.append(pose.yaw)
                if head_yaws:
                    smoothed_yaw = float(np.median(head_yaws))
                    raw_direction = classify_head_direction(
                        smoothed_yaw,
                        args.head_left_threshold,
                        args.head_right_threshold,
                        args.head_yaw_invert,
                    )
                else:
                    smoothed_yaw = None
                    raw_direction = "unknown"
                if head_missing_frames >= args.head_stability_frames * 2:
                    raw_direction = "unknown"
                if raw_direction == head_candidate:
                    head_candidate_frames += 1
                else:
                    head_candidate = raw_direction
                    head_candidate_frames = 1
                if (
                    head_candidate_frames >= args.head_stability_frames
                    and head_direction != head_candidate
                ):
                    head_direction = head_candidate
                    yaw_text = "none" if smoothed_yaw is None else f"{smoothed_yaw:.1f}"
                    print(
                        f"Head direction: {head_direction} yaw={yaw_text}",
                        flush=True,
                    )

            track_ids = (
                result.boxes.id.int().tolist()
                if result.boxes.id is not None
                else [None] * len(result.boxes)
            )
            for box, confidence, track_id in zip(
                result.boxes.xyxy, result.boxes.conf, track_ids
            ):
                coordinates = box.tolist()
                x1, y1, x2, y2 = [int(value) for value in coordinates]
                in_zone = is_background_candidate(
                    coordinates, width, height, args.zone_bottom
                )
                owner = is_camera_owner(coordinates, width, height)
                center = ((x1 + x2) / 2, (y1 + y2) / 2)
                history = track_history[track_id] if track_id is not None else ()
                passed = (
                    in_zone
                    and track_id is not None
                    and has_moved(history, center, args.movement_pixels)
                )
                facing_camera = False
                if not owner and face_detector is not None:
                    crop = frame[max(0, y1):min(height, y2), max(0, x1):min(width, x2)]
                    facing_camera = face_looks_toward_camera(face_detector, crop)
                trigger_person = trigger_person or is_trigger_signal(
                    passed,
                    facing_camera,
                    owner,
                    args.look_trigger,
                )
                color = (0, 200, 0) if passed else (120, 120, 120)
                label = f"person {float(confidence):.2f}"
                if passed:
                    label += " passing"
                if facing_camera:
                    label += " looking"
                    color = (0, 0, 255)
                cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
                cv2.putText(
                    frame,
                    label,
                    (x1, max(20, y1 - 8)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    color,
                    2,
                )

            consecutive = consecutive + 1 if trigger_person else 0
            stale_ids = [
                track_id
                for track_id, history in track_history.items()
                if track_id not in track_ids
            ]
            for track_id in stale_ids:
                del track_history[track_id]
            now = time.monotonic()
            ready = now - last_trigger >= args.cooldown
            if consecutive >= args.trigger_frames and ready:
                cv2.putText(
                    frame,
                    "TRIGGER",
                    (12, 58),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (0, 0, 255),
                    2,
                )
                capture_path = save_trigger_frame(frame, args.capture_dir)
                print(f"Capture saved: {capture_path.resolve()}")
                print(
                    "Trigger detected: "
                    f"passing after {args.trigger_frames} consecutive frames",
                    flush=True,
                )
                if args.dry_run:
                    print(f"DRY RUN: would activate a window matching {args.ide_title!r}")
                else:
                    activated_title = activate_window(
                        args.ide_title,
                        hwnd=args.ide_hwnd or None,
                    )
                    if activated_title:
                        print(f"Triggered: activated {activated_title!r}")
                    else:
                        print(f"Triggered, but no window matched {args.ide_title!r}")
                last_trigger = now
                consecutive = 0

            status = (
                f"person={int(trigger_person)} "
                f"frames={consecutive}/{args.trigger_frames}"
            )
            if head_estimator is not None:
                yaw_text = "none" if last_head_yaw is None else f"{last_head_yaw:.1f}"
                status += f" head={head_direction} yaw={yaw_text}"
            cv2.rectangle(frame, (0, 0), (width - 1, zone_bottom), (255, 180, 0), 1)
            cv2.putText(
                frame,
                status,
                (12, 28),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (0, 220, 255),
                2,
            )
            if not args.no_preview:
                cv2.imshow("person watch", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            if debug_server is not None:
                elapsed = time.monotonic() - frame_started
                frame_rate = 1.0 / elapsed if elapsed > 0 else 0.0
                small_frame = frame
                if width > 960:
                    scale = 960 / width
                    small_frame = cv2.resize(
                        frame,
                        (int(width * scale), int(height * scale)),
                        interpolation=cv2.INTER_AREA,
                    )
                encoded, buffer = cv2.imencode(
                    ".jpg",
                    small_frame,
                    [cv2.IMWRITE_JPEG_QUALITY, 75],
                )
                if encoded:
                    debug_server.publish(
                        {
                            "frame": base64.b64encode(buffer).decode("ascii"),
                            "frame_number": frames_seen,
                            "width": width,
                            "height": height,
                            "fps": round(frame_rate, 1),
                            "direction": head_direction,
                            "candidate": head_candidate,
                            "yaw": None if last_head_yaw is None else round(last_head_yaw, 1),
                            "smooth": None if smoothed_yaw is None else round(smoothed_yaw, 1),
                            "pitch": None if last_head_pitch is None else round(last_head_pitch, 1),
                            "roll": None if last_head_roll is None else round(last_head_roll, 1),
                            "stable": head_candidate_frames,
                            "missing": head_missing_frames,
                        }
                    )
                frame_started = time.monotonic()
    finally:
        if head_estimator is not None:
            head_estimator.close()
        if debug_server is not None:
            debug_server.close()
        camera.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
