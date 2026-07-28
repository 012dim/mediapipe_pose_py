"""action_recognizer 单元测试。

使用 mock 的 PoseResult / LandmarkPoint 验证 6 种动作识别 + 冷却逻辑。

运行:
    pytest tests/test_action_recognizer.py -v
"""
import time

import pytest

from modules.action_recognizer import (
    ActionRecognizer,
    ActionEvent,
    LEFT_WRIST,
    RIGHT_WRIST,
    NOSE,
    LEFT_SHOULDER,
    RIGHT_SHOULDER,
    LEFT_HIP,
    RIGHT_HIP,
    LEFT_KNEE,
    RIGHT_KNEE,
    LEFT_ANKLE,
    RIGHT_ANKLE,
)
from modules.pose_detector import LandmarkPoint, PoseResult


def make_landmark(x: float, y: float, z: float = 0.0, v: float = 1.0) -> LandmarkPoint:
    """构造一个 LandmarkPoint。

    Args:
        x: 归一化横坐标。
        y: 归一化纵坐标。
        z: 深度。
        v: 可见度。

    Returns:
        LandmarkPoint 实例。
    """
    return LandmarkPoint(x=x, y=y, z=z, visibility=v)


def make_landmarks(**overrides) -> list:
    """构造 33 个关键点,默认站立姿态。

    可通过 overrides 覆盖指定关键点:make_landmarks(left_wrist=lm, nose=lm)

    Returns:
        List[LandmarkPoint]: 33 个关键点。
    """
    # 关键点中文名 -> 索引
    name_to_idx = {
        "nose": NOSE,
        "left_shoulder": LEFT_SHOULDER, "right_shoulder": RIGHT_SHOULDER,
        "left_elbow": 13, "right_elbow": 14,
        "left_wrist": LEFT_WRIST, "right_wrist": RIGHT_WRIST,
        "left_hip": LEFT_HIP, "right_hip": RIGHT_HIP,
        "left_knee": LEFT_KNEE, "right_knee": RIGHT_KNEE,
        "left_ankle": LEFT_ANKLE, "right_ankle": RIGHT_ANKLE,
    }
    # 默认站立姿态(垂直,膝伸直)
    defaults = {
        "nose": (0.50, 0.10),
        "left_shoulder": (0.40, 0.25), "right_shoulder": (0.60, 0.25),
        "left_elbow": (0.38, 0.40), "right_elbow": (0.62, 0.40),
        "left_wrist": (0.36, 0.55), "right_wrist": (0.64, 0.55),
        "left_hip": (0.43, 0.55), "right_hip": (0.57, 0.55),
        "left_knee": (0.43, 0.75), "right_knee": (0.57, 0.75),
        "left_ankle": (0.43, 0.95), "right_ankle": (0.57, 0.95),
    }
    points = []
    for i in range(33):
        points.append(make_landmark(0.5, 0.5))
    for name, (x, y) in defaults.items():
        idx = name_to_idx[name]
        points[idx] = make_landmark(x, y)
    for name, pt in overrides.items():
        idx = name_to_idx[name]
        points[idx] = pt
    return points


def make_pose(landmarks: list) -> PoseResult:
    """构造 PoseResult(已检测到人)。"""
    return PoseResult(landmarks=landmarks, raw_landmarks=landmarks, person_detected=True)


def make_empty_pose() -> PoseResult:
    """构造未检测到人的 PoseResult。"""
    return PoseResult(landmarks=None, raw_landmarks=None, person_detected=False)


class TestHandUp:
    """举手类动作测试。"""

    def test_left_hand_up(self) -> None:
        """左手腕高于鼻子 0.05 触发 LEFT_HAND_UP。"""
        rec = ActionRecognizer()
        # 鼻子 y=0.10,阈值 0.05,左手腕 y < 0.05
        lw = make_landmark(0.36, 0.03)
        rw = make_landmark(0.64, 0.55)  # 右手未举起
        lm = make_landmarks(left_wrist=lw, right_wrist=rw)
        event = rec.recognize(make_pose(lm))
        assert event is not None
        assert event.name == "LEFT_HAND_UP"

    def test_right_hand_up(self) -> None:
        """右手腕高于鼻子 0.05 触发 RIGHT_HAND_UP。"""
        rec = ActionRecognizer()
        lw = make_landmark(0.36, 0.55)
        rw = make_landmark(0.64, 0.03)
        lm = make_landmarks(left_wrist=lw, right_wrist=rw)
        event = rec.recognize(make_pose(lm))
        assert event is not None
        assert event.name == "RIGHT_HAND_UP"

    def test_both_hands_up(self) -> None:
        """双手都举起触发 BOTH_HANDS_UP(而非 LEFT/RIGHT)。"""
        rec = ActionRecognizer()
        lw = make_landmark(0.36, 0.03)
        rw = make_landmark(0.64, 0.03)
        lm = make_landmarks(left_wrist=lw, right_wrist=rw)
        event = rec.recognize(make_pose(lm))
        assert event is not None
        assert event.name == "BOTH_HANDS_UP"

    def test_no_hand_up(self) -> None:
        """双手自然下垂时不应触发举手。"""
        rec = ActionRecognizer()
        # 默认手腕 y=0.55, 鼻子 y=0.10, 0.55 > 0.05,不举手
        lm = make_landmarks()
        event = rec.recognize(make_pose(lm))
        # 站姿默认应触发 STAND 而非举手
        assert event is None or event.name != "LEFT_HAND_UP"
        assert event is None or event.name != "RIGHT_HAND_UP"


class TestStandSit:
    """站立 / 坐下测试。"""

    def test_stand(self) -> None:
        """膝伸直(角度>160)触发 STAND。"""
        rec = ActionRecognizer()
        # 默认姿态髋膝踝垂直共线 -> 180 度
        lm = make_landmarks()
        event = rec.recognize(make_pose(lm))
        # 优先级:FALL/BOTH_HANDS 不满足,可能触发 STAND
        assert event is not None
        assert event.name == "STAND"

    def test_sit(self) -> None:
        """膝弯曲(角度<130)触发 SIT。"""
        rec = ActionRecognizer()
        # 让膝弯曲:髋在膝上方,踝在膝右侧(90度)
        lk = make_landmark(0.43, 0.75)
        rk = make_landmark(0.57, 0.75)
        la = make_landmark(0.55, 0.75)  # 左踝向右偏
        ra = make_landmark(0.45, 0.75)  # 右踝向左偏
        # 同时抬高髋部使坐姿合理
        lh = make_landmark(0.43, 0.65)
        rh = make_landmark(0.57, 0.65)
        lm = make_landmarks(left_knee=lk, right_knee=rk, left_ankle=la, right_ankle=ra,
                            left_hip=lh, right_hip=rh)
        event = rec.recognize(make_pose(lm))
        # 此时膝角度约 90 度,触发 SIT
        assert event is not None
        assert event.name == "SIT"


class TestFall:
    """跌倒测试。"""

    def test_fall_detected(self) -> None:
        """肩髋高度差/肩宽 < 0.3 触发 FALL_DETECTED。"""
        rec = ActionRecognizer()
        # 跌倒:身体水平,肩髋 y 接近
        ls = make_landmark(0.40, 0.50)
        rs = make_landmark(0.50, 0.50)  # 肩宽 0.10, 肩水平
        lh = make_landmark(0.55, 0.52)  # 髋略低于肩
        rh = make_landmark(0.65, 0.52)
        # ratio = |0.51 - 0.52| / 0.10 = 0.1 < 0.3
        lm = make_landmarks(left_shoulder=ls, right_shoulder=rs, left_hip=lh, right_hip=rh)
        event = rec.recognize(make_pose(lm))
        assert event is not None
        assert event.name == "FALL_DETECTED"

    def test_no_fall_when_standing(self) -> None:
        """站立时不应触发跌倒。"""
        rec = ActionRecognizer()
        # 站立:肩 y=0.25, 髋 y=0.55, 肩宽 0.2
        # ratio = 0.30 / 0.20 = 1.5 > 0.3, 不跌倒
        lm = make_landmarks()
        event = rec.recognize(make_pose(lm))
        assert event is not None
        assert event.name != "FALL_DETECTED"


class TestCooldownAndReset:
    """冷却与重置测试。"""

    def test_cooldown_prevents_repeat(self) -> None:
        """冷却期内同一动作不应重复触发。"""
        rec = ActionRecognizer(cooldown=10.0)
        lw = make_landmark(0.36, 0.03)
        rw = make_landmark(0.64, 0.03)
        # 用弯曲膝盖(角度~140°,介于 130/160 之间)避免 STAND/SIT 干扰
        lk = make_landmark(0.43, 0.75)
        rk = make_landmark(0.57, 0.75)
        la = make_landmark(0.50, 0.83)
        ra = make_landmark(0.50, 0.83)
        lh = make_landmark(0.43, 0.65)
        rh = make_landmark(0.57, 0.65)
        lm = make_landmarks(left_wrist=lw, right_wrist=rw,
                            left_knee=lk, right_knee=rk,
                            left_ankle=la, right_ankle=ra,
                            left_hip=lh, right_hip=rh)
        # 第一次触发
        ev1 = rec.recognize(make_pose(lm))
        assert ev1 is not None
        assert ev1.name == "BOTH_HANDS_UP"
        # 冷却期内再识别,不应触发任何动作
        ev2 = rec.recognize(make_pose(lm))
        assert ev2 is None

    def test_reset_clears_cooldown(self) -> None:
        """reset 后动作可立即再次触发。"""
        rec = ActionRecognizer(cooldown=10.0)
        lw = make_landmark(0.36, 0.03)
        rw = make_landmark(0.64, 0.03)
        lk = make_landmark(0.43, 0.75)
        rk = make_landmark(0.57, 0.75)
        la = make_landmark(0.50, 0.83)
        ra = make_landmark(0.50, 0.83)
        lh = make_landmark(0.43, 0.65)
        rh = make_landmark(0.57, 0.65)
        lm = make_landmarks(left_wrist=lw, right_wrist=rw,
                            left_knee=lk, right_knee=rk,
                            left_ankle=la, right_ankle=ra,
                            left_hip=lh, right_hip=rh)
        ev1 = rec.recognize(make_pose(lm))
        assert ev1 is not None
        # 重置
        rec.reset()
        assert rec.recent_actions == []
        # 重置后可立即触发
        ev2 = rec.recognize(make_pose(lm))
        assert ev2 is not None
        assert ev2.name == "BOTH_HANDS_UP"


class TestNoPerson:
    """无人时的测试。"""

    def test_no_person_returns_none(self) -> None:
        """未检测到人时返回 None。"""
        rec = ActionRecognizer()
        ev = rec.recognize(make_empty_pose())
        assert ev is None
