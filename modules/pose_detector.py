"""MediaPipe Pose 姿态识别封装模块。

封装 MediaPipe Pose 的推理流程,提供:
- 33 个关键点坐标(x, y, z, visibility)的获取
- 关键点坐标平滑(用 deque 缓存最近 N 帧取平均,减少抖动)
- 骨骼连线的 MediaPipe drawing 数据
"""
import logging
from collections import deque
from dataclasses import dataclass
from typing import Optional, List

import cv2
import numpy as np
import mediapipe as mp

import config

logger = logging.getLogger(__name__)

mp_pose = mp.solutions.pose
mp_drawing = mp.solutions.drawing_utils


@dataclass
class LandmarkPoint:
    """单个关键点坐标。

    Attributes:
        x: 归一化横坐标 [0,1],左为 0。
        y: 归一化纵坐标 [0,1],上为 0。
        z: 相对深度(相对于臀部中心)。
        visibility: 可见度置信度 [0,1]。
    """
    x: float
    y: float
    z: float
    visibility: float


@dataclass
class PoseResult:
    """单帧姿态识别结果。

    Attributes:
        landmarks: 33 个关键点列表(已平滑)。未识别到时为 None。
        raw_landmarks: 原始未平滑关键点。
        person_detected: 是否检测到人。
    """
    landmarks: Optional[List[LandmarkPoint]]
    raw_landmarks: Optional[List[LandmarkPoint]]
    person_detected: bool


class PoseDetector:
    """MediaPipe Pose 姿态识别封装类。

    负责将 BGR 图像送入 MediaPipe 推理,并对关键点坐标做时序平滑。
    """

    def __init__(
        self,
        static_image_mode: bool = config.STATIC_IMAGE_MODE,
        model_complexity: int = config.MODEL_COMPLEXITY,
        smooth_landmarks: bool = config.SMOOTH_LANDMARKS,
        min_detection_confidence: float = config.MIN_DETECTION_CONFIDENCE,
        min_tracking_confidence: float = config.MIN_TRACKING_CONFIDENCE,
        buffer_size: int = config.SMOOTH_BUFFER_SIZE,
    ) -> None:
        """初始化 MediaPipe Pose 推理器。

        Args:
            static_image_mode: 是否为静态图像模式。
            model_complexity: 模型复杂度 0/1/2。
            smooth_landmarks: 是否启用 MediaPipe 内置平滑。
            min_detection_confidence: 检测最小置信度。
            min_tracking_confidence: 跟踪最小置信度。
            buffer_size: 自定义平滑缓冲帧数。

        Raises:
            RuntimeError: MediaPipe 初始化失败时抛出。
        """
        try:
            self.pose = mp_pose.Pose(
                static_image_mode=static_image_mode,
                model_complexity=model_complexity,
                smooth_landmarks=smooth_landmarks,
                min_detection_confidence=min_detection_confidence,
                min_tracking_confidence=min_tracking_confidence,
            )
        except Exception as e:  # noqa: BLE001
            logger.error("MediaPipe Pose 初始化失败: %s", e)
            raise RuntimeError(f"MediaPipe Pose 初始化失败: {e}") from e

        self._buffer: deque = deque(maxlen=buffer_size)
        self._buffer_size: int = buffer_size
        logger.info(
            "PoseDetector 初始化完成,model_complexity=%d, 平滑缓冲=%d 帧",
            model_complexity, buffer_size,
        )

    def process(self, frame_bgr: np.ndarray) -> PoseResult:
        """对一帧 BGR 图像进行姿态识别。

        Args:
            frame_bgr: OpenCV BGR 格式图像。

        Returns:
            PoseResult: 识别结果(含平滑后的关键点)。
        """
        try:
            frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
            frame_rgb.flags.writeable = False
            results = self.pose.process(frame_rgb)
        except Exception as e:  # noqa: BLE001
            logger.warning("MediaPipe 推理异常,跳过本帧: %s", e)
            return PoseResult(landmarks=None, raw_landmarks=None, person_detected=False)

        if results.pose_landmarks is None:
            # 未检测到人,清空平滑缓冲
            self._buffer.clear()
            return PoseResult(landmarks=None, raw_landmarks=None, person_detected=False)

        raw_points = [
            LandmarkPoint(
                x=lm.x, y=lm.y, z=lm.z, visibility=lm.visibility,
            )
            for lm in results.pose_landmarks.landmark
        ]

        # 平滑:缓存最近 N 帧坐标取平均
        self._buffer.append(raw_points)
        smoothed = self._smooth_landmarks()

        return PoseResult(
            landmarks=smoothed,
            raw_landmarks=raw_points,
            person_detected=True,
        )

    def _smooth_landmarks(self) -> List[LandmarkPoint]:
        """对缓冲中的关键点取平均,返回平滑后的关键点列表。

        Returns:
            List[LandmarkPoint]: 平滑后的 33 个关键点。
        """
        if not self._buffer:
            return []
        if len(self._buffer) == 1:
            return list(self._buffer[0])

        n = len(self._buffer)
        n_pts = len(self._buffer[0])
        smoothed: List[LandmarkPoint] = []
        for i in range(n_pts):
            xs = np.mean([frame[i].x for frame in self._buffer])
            ys = np.mean([frame[i].y for frame in self._buffer])
            zs = np.mean([frame[i].z for frame in self._buffer])
            vs = np.mean([frame[i].visibility for frame in self._buffer])
            smoothed.append(LandmarkPoint(x=float(xs), y=float(ys), z=float(zs), visibility=float(vs)))
        return smoothed

    def reset_smoothing(self) -> None:
        """清空平滑缓冲。"""
        self._buffer.clear()

    def close(self) -> None:
        """释放 MediaPipe 资源。"""
        try:
            self.pose.close()
        except Exception as e:  # noqa: BLE001
            logger.warning("关闭 PoseDetector 异常: %s", e)
        logger.info("PoseDetector 已关闭")

    @staticmethod
    def get_drawing_spec():
        """返回 MediaPipe 默认骨骼绘制规范,供 visualizer 使用。

        Returns:
            mp.solutions.drawing_utils.DrawingSpec 对象。
        """
        return mp_drawing
