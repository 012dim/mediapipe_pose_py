"""可视化模块:骨骼绘制 + FPS + 文字显示。

负责在画面上:
- 用不同颜色绘制 33 个关键点(躯干/四肢/手/脚)
- 用 mp.solutions.drawing_utils 画出骨骼连线
- 左上角显示 FPS(平滑滤波)
- 右上角显示已识别人数
- 底部显示最近 3 个识别到的动作(时间戳 + 动作名)
- 底部显示当前触发的动作名(持续 2 秒)
"""
import logging
import time
from collections import deque
from typing import List, Optional
import platform

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

import config
from modules.action_recognizer import ActionEvent, ActionRecognizer, BODY_NONE, HAND_NONE
from modules.pose_detector import PoseResult, LandmarkPoint, mp_pose, mp_drawing

logger = logging.getLogger(__name__)


# ============ 中文渲染(PIL) ============
# cv2.putText 不支持中文,用 PIL 绘制中文文字。
def _get_chinese_font() -> str:
    """根据操作系统返回中文字体路径。"""
    system = platform.system()
    if system == "Windows":
        return "C:/Windows/Fonts/msyh.ttc"
    elif system == "Linux":
        return "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"
    else:
        return "/System/Library/Fonts/PingFang.ttc"


_FONT_PATH = _get_chinese_font()
_FONT_CACHE = {}


def _get_font(size: int):
    """获取指定大小的中文字体(带缓存)。"""
    if size not in _FONT_CACHE:
        try:
            _FONT_CACHE[size] = ImageFont.truetype(_FONT_PATH, size)
        except Exception:  # noqa: BLE001
            _FONT_CACHE[size] = ImageFont.load_default()
    return _FONT_CACHE[size]


def cv2_put_chinese_text(img, text, pos, color, font_size=20):
    """在 OpenCV 图像上绘制中文文字(BGR color)。

    Args:
        img: OpenCV BGR 图像。
        text: 要绘制的文字(可含中文)。
        pos: 文字左上角坐标 (x, y)。
        color: BGR 颜色元组。
        font_size: 字体大小。

    Returns:
        np.ndarray: 绘制后的 BGR 图像。
    """
    pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_img)
    font = _get_font(font_size)
    rgb_color = (color[2], color[1], color[0])  # BGR -> RGB
    draw.text(pos, text, font=font, fill=rgb_color)
    return cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)


# ============ 关键点分组(按身体部位) ============
# 索引参考 MediaPipe Pose 33 关键点
FACE_POINTS = list(range(0, 11))
TORSO_POINTS = [11, 12, 23, 24]                              # 肩 + 髋
ARM_POINTS = [13, 14, 15, 16]                                # 肘 + 腕
HAND_POINTS = [17, 18, 19, 20, 21, 22]                       # 手部细节
LEG_POINTS = [25, 26, 27, 28]                                # 膝 + 踝
FOOT_POINTS = [29, 30, 31, 32]                              # 脚部

# 颜色 (BGR)
COLOR_FACE = (220, 220, 220)        # 浅灰
COLOR_TORSO = (0, 255, 255)         # 黄
COLOR_ARM = (255, 0, 255)          # 紫
COLOR_HAND = (255, 255, 0)         # 青
COLOR_LEG = (0, 255, 0)            # 绿
COLOR_FOOT = (0, 165, 255)        # 橙

# 文字颜色
COLOR_FPS = (0, 255, 0)
COLOR_INFO = (0, 255, 255)
COLOR_ACTION = (0, 100, 255)
COLOR_RECENT = (200, 200, 200)
COLOR_BODY = (255, 120, 0)      # 身体通道(蓝)
COLOR_HAND = (0, 165, 255)      # 手部通道(橙)
COLOR_NONE = (128, 128, 128)    # 无动作(灰)


class FPSCounter:
    """FPS 计数器,带平滑滤波避免数字跳动。

    使用最近 N 帧时间的倒数计算平均 FPS。
    """

    def __init__(self, smooth_window: int = 30) -> None:
        """初始化 FPS 计数器。

        Args:
            smooth_window: 平滑窗口大小(帧数)。
        """
        self._frame_times: deque = deque(maxlen=smooth_window)
        self._last_time: float = time.time()

    def tick(self) -> float:
        """记录一帧并返回当前(平滑)FPS。

        Returns:
            float: 平滑后的 FPS 值。
        """
        now = time.time()
        dt = now - self._last_time
        self._last_time = now
        if dt <= 0:
            return 0.0
        self._frame_times.append(dt)
        if not self._frame_times:
            return 0.0
        avg_dt = sum(self._frame_times) / len(self._frame_times)
        return 1.0 / avg_dt if avg_dt > 0 else 0.0


class Visualizer:
    """可视化器:在画面上绘制关键点、骨骼、FPS、动作文字。

    Attributes:
        show_skeleton: 是否显示骨骼(按 f 切换)。
        fps_counter: FPS 计数器实例。
    """

    def __init__(self, show_skeleton: bool = config.SHOW_SKELETON_DEFAULT) -> None:
        """初始化可视化器。

        Args:
            show_skeleton: 启动时是否显示骨骼。
        """
        self.show_skeleton: bool = show_skeleton
        self.fps_counter: FPSCounter = FPSCounter()
        # 当前正在屏幕底部高亮显示的动作(事件 + 过期时间)
        self._active_event: Optional[ActionEvent] = None
        self._active_expire: float = 0.0
        # 当前双通道状态(每帧实时刷新)
        self._current_state = None

    def toggle_skeleton(self) -> None:
        """切换骨骼显示状态(按 f 键)。"""
        self.show_skeleton = not self.show_skeleton
        logger.info("骨骼显示: %s", "开启" if self.show_skeleton else "关闭")

    def set_active_action(self, event: ActionEvent) -> None:
        """设置当前要在屏幕底部高亮显示的动作。

        Args:
            event: 触发的动作事件。
        """
        self._active_event = event
        self._active_expire = time.time() + config.ACTION_DISPLAY_DURATION

    def set_current_state(self, state) -> None:
        """每帧调用,设置当前双通道状态,屏幕实时刷新。

        Args:
            state: DualChannelState 实例(body_action + hand_action)。
        """
        self._current_state = state

    def draw(
        self,
        frame: np.ndarray,
        pose_result: PoseResult,
        fps: float,
        person_count: int,
        recent_actions: List[ActionEvent],
    ) -> np.ndarray:
        """在帧上绘制所有可视化元素。

        Args:
            frame: 原始 BGR 帧。
            pose_result: 姿态识别结果。
            fps: 当前 FPS。
            person_count: 已识别到的人数。
            recent_actions: 最近动作事件列表(最多 3 个)。

        Returns:
            np.ndarray: 绘制后的帧。
        """
        out = frame

        # 1) 骨骼连线 + 关键点
        if self.show_skeleton and pose_result.person_detected and pose_result.raw_landmarks is not None:
            out = self._draw_skeleton(out, pose_result)
            out = self._draw_colored_keypoints(out, pose_result.landmarks)

        # 2) 左上角 FPS
        self._draw_fps(out, fps)

        # 3) 右上角人数
        self._draw_person_count(out, person_count)

        # 4) 底部最近动作
        out = self._draw_recent_actions(out, recent_actions)

        # 5) 底部双通道当前状态(每帧实时刷新)
        out = self._draw_dual_channel_state(out)

        # 6) 帮助提示
        self._draw_help(out)

        return out

    def _draw_skeleton(self, frame: np.ndarray, pose_result: PoseResult) -> np.ndarray:
        """用 MediaPipe drawing_utils 绘制骨骼连线与默认关键点。

        Args:
            frame: 目标帧。
            pose_result: 姿态结果(使用 raw_landmarks 还原 mp 结构)。

        Returns:
            np.ndarray: 绘制后的帧。
        """
        # 重建 NormalizedLandmarkList 供 mp_drawing 使用
        landmark_list = self._to_mp_landmark_list(pose_result.raw_landmarks)
        mp_drawing.draw_landmarks(
            frame,
            landmark_list,
            mp_pose.POSE_CONNECTIONS,
            mp_drawing.DrawingSpec(color=(80, 110, 255), thickness=2, circle_radius=2),
            mp_drawing.DrawingSpec(color=(80, 255, 128), thickness=2, circle_radius=2),
        )
        return frame

    def _draw_colored_keypoints(
        self,
        frame: np.ndarray,
        landmarks: Optional[List[LandmarkPoint]],
    ) -> np.ndarray:
        """按部位用不同颜色绘制 33 个关键点(覆盖 mp 默认圆点)。

        Args:
            frame: 目标帧。
            landmarks: 平滑后的关键点列表。

        Returns:
            np.ndarray: 绘制后的帧。
        """
        if landmarks is None:
            return frame
        h, w = frame.shape[:2]

        def draw_group(idx_list: List[int], color) -> None:
            for idx in idx_list:
                pt = landmarks[idx]
                if pt.visibility < 0.3:
                    continue
                cx = int(pt.x * w)
                cy = int(pt.y * h)
                cv2.circle(frame, (cx, cy), 5, color, -1)
                cv2.circle(frame, (cx, cy), 5, (0, 0, 0), 1)

        draw_group(FACE_POINTS, COLOR_FACE)
        draw_group(TORSO_POINTS, COLOR_TORSO)
        draw_group(ARM_POINTS, COLOR_ARM)
        draw_group(HAND_POINTS, COLOR_HAND)
        draw_group(LEG_POINTS, COLOR_LEG)
        draw_group(FOOT_POINTS, COLOR_FOOT)
        return frame

    def _draw_fps(self, frame: np.ndarray, fps: float) -> None:
        """在左上角绘制 FPS。

        Args:
            frame: 目标帧。
            fps: FPS 值。
        """
        text = f"FPS: {fps:.1f}"
        cv2.rectangle(frame, (8, 8), (160, 38), (0, 0, 0), -1)
        cv2.putText(frame, text, (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, COLOR_FPS, 2)

    def _draw_person_count(self, frame: np.ndarray, count: int) -> None:
        """在右上角绘制已识别人数。

        Args:
            frame: 目标帧。
            count: 人数。
        """
        text = f"Person: {count}"
        w = frame.shape[1]
        # 右对齐
        text_size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)[0]
        x = w - text_size[0] - 12
        cv2.rectangle(frame, (x - 4, 8), (w - 8, 38), (0, 0, 0), -1)
        cv2.putText(frame, text, (x, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, COLOR_INFO, 2)

    def _draw_recent_actions(
        self,
        frame: np.ndarray,
        recent_actions: List[ActionEvent],
    ) -> np.ndarray:
        """在底部绘制最近 3 个动作(时间戳 + 动作名)。

        Args:
            frame: 目标帧。
            recent_actions: 最近动作事件列表。

        Returns:
            np.ndarray: 绘制后的帧。
        """
        h, w = frame.shape[:2]
        # 中文显示名 + 动作事件名
        # 例如: [14:32:15] BOTH_HANDS_UP (双手举起)
        y_base = h - 10
        for i, ev in enumerate(reversed(recent_actions[-3:])):
            time_str = time.strftime("%H:%M:%S", time.localtime(ev.timestamp))
            text = f"[{time_str}] {ev.name} ({ev.display_name})"
            y = y_base - i * 22
            cv2.rectangle(
                frame,
                (8, y - 18), (8 + 360, y + 4),
                (0, 0, 0), -1,
            )
            frame = cv2_put_chinese_text(
                frame, text, (12, y - 16), COLOR_RECENT, font_size=16,
            )
        return frame

    def _draw_active_action(self, frame: np.ndarray) -> None:
        """在屏幕底部高亮显示当前动作(持续 2 秒)。

        Args:
            frame: 目标帧。
        """
        if self._active_event is None:
            return
        if time.time() > self._active_expire:
            self._active_event = None
            return
        h, w = frame.shape[:2]
        text = self._active_event.name
        text_size = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 1.6, 3)[0]
        x = (w - text_size[0]) // 2
        y = h - 60
        # 背景框
        cv2.rectangle(
            frame,
            (x - 12, y - 30), (x + text_size[0] + 12, y + 8),
            (0, 0, 0), -1,
        )
        cv2.rectangle(
            frame,
            (x - 12, y - 30), (x + text_size[0] + 12, y + 8),
            COLOR_ACTION, 2,
        )
        cv2.putText(
            frame, text, (x, y),
            cv2.FONT_HERSHEY_SIMPLEX, 1.6, COLOR_ACTION, 3,
        )

    def _draw_dual_channel_state(self, frame: np.ndarray) -> np.ndarray:
        """屏幕底部并排绘制身体(蓝)+手部(橙)双通道当前状态,每帧实时刷新。

        无动作时显示灰色。无 2 秒过期机制,始终显示当前状态。

        Args:
            frame: 目标帧。

        Returns:
            np.ndarray: 绘制后的帧。
        """
        if self._current_state is None:
            return frame
        h, w = frame.shape[:2]
        state = self._current_state
        names = ActionRecognizer.ACTION_DISPLAY_NAMES
        body_disp = names.get(state.body_action, state.body_action)
        hand_disp = names.get(state.hand_action, state.hand_action)
        body_text = f"身体: {body_disp}"
        hand_text = f"手部: {hand_disp}"
        body_color = COLOR_NONE if state.body_action == BODY_NONE else COLOR_BODY
        hand_color = COLOR_NONE if state.hand_action == HAND_NONE else COLOR_HAND

        # 底部中央并排两个标签(位于最近动作列表上方)
        y = h - 95
        body_x = w // 2 - 200
        hand_x = w // 2 + 20
        # 背景框
        cv2.rectangle(frame, (body_x - 8, y - 24), (body_x + 180, y + 6), (0, 0, 0), -1)
        cv2.rectangle(frame, (hand_x - 8, y - 24), (hand_x + 180, y + 6), (0, 0, 0), -1)
        # 文字(PIL 中文渲染)
        frame = cv2_put_chinese_text(frame, body_text, (body_x, y - 22), body_color, font_size=18)
        frame = cv2_put_chinese_text(frame, hand_text, (hand_x, y - 22), hand_color, font_size=18)
        return frame

    def _draw_help(self, frame: np.ndarray) -> None:
        """在画面顶部中央显示按键帮助。"""
        h, w = frame.shape[:2]
        help_text = "q:Quit  s:Shot  f:Skeleton  c:Camera  r:Reset"
        text_size = cv2.getTextSize(help_text, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)[0]
        x = (w - text_size[0]) // 2
        cv2.rectangle(frame, (x - 6, 44), (x + text_size[0] + 6, 64), (0, 0, 0), -1)
        cv2.putText(frame, help_text, (x, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1)

    @staticmethod
    def _to_mp_landmark_list(landmarks: Optional[List[LandmarkPoint]]):
        """将 LandmarkPoint 列表转换为 MediaPipe NormalizedLandmarkList。

        Args:
            landmarks: 自定义关键点列表。

        Returns:
            mp.solutions.framework.NormalizedLandmarkList 实例。
        """
        from mediapipe.framework.formats import landmark_pb2

        mp_list = landmark_pb2.NormalizedLandmarkList()
        for pt in landmarks:
            lm = mp_list.landmark.add()
            lm.x = pt.x
            lm.y = pt.y
            lm.z = pt.z
            lm.visibility = pt.visibility
        return mp_list
