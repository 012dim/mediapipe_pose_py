"""action_recognizer 单元测试(v4.2 — 仅 3 种手部动作)。

镜像约定(必须与软件实测一致):
- 主循环执行 cv2.flip(frame, 1) 水平翻转画面
- 识别代码交换 LEFT_WRIST / RIGHT_WRIST 判定:
    left_up  = _is_hand_up(lm[RIGHT_WRIST], lm[NOSE])  # 用户左手 = MediaPipe RIGHT_WRIST
    right_up = _is_hand_up(lm[LEFT_WRIST],  lm[NOSE])  # 用户右手 = MediaPipe LEFT_WRIST

因此测试数据构造:
- 测试 LEFT_HAND_UP  → 让 lm[RIGHT_WRIST] 高于鼻子
- 测试 RIGHT_HAND_UP → 让 lm[LEFT_WRIST]  高于鼻子
- 测试 BOTH_HANDS_UP → 两个手腕都高于鼻子

运行:
    pytest tests/test_action_recognizer.py -v
"""
import pytest

from modules.action_recognizer import (
    ActionRecognizer,
    ActionEvent,
    LEFT_WRIST,
    RIGHT_WRIST,
    NOSE,
    LEFT_SHOULDER,
    RIGHT_SHOULDER,
    HAND_NONE,
    MATCH_CORRECT,
    MATCH_WRONG,
)
from modules.pose_detector import LandmarkPoint, PoseResult


# ---------- 测试数据构造工具 ----------

def make_landmark(x: float, y: float, z: float = 0.0, v: float = 1.0) -> LandmarkPoint:
    """构造一个 LandmarkPoint。"""
    return LandmarkPoint(x=x, y=y, z=z, visibility=v)


def make_landmarks(**overrides) -> list:
    """构造 33 个关键点,默认双手下垂姿态。

    可通过 overrides 覆盖指定关键点:
        make_landmarks(left_wrist=lm, nose=lm)

    注意:本函数参数名 left_wrist / right_wrist 指 MediaPipe 关键点索引
    (LEFT_WRIST=15, RIGHT_WRIST=16),不是用户的左右手。
    """
    name_to_idx = {
        "nose": NOSE,
        "left_shoulder": LEFT_SHOULDER,
        "right_shoulder": RIGHT_SHOULDER,
        "left_elbow": 13,
        "right_elbow": 14,
        "left_wrist": LEFT_WRIST,
        "right_wrist": RIGHT_WRIST,
    }
    # 默认姿态:鼻子在上方,双手自然下垂
    defaults = {
        "nose": (0.50, 0.10),
        "left_shoulder": (0.40, 0.25),
        "right_shoulder": (0.60, 0.25),
        "left_elbow": (0.38, 0.40),
        "right_elbow": (0.62, 0.40),
        "left_wrist": (0.36, 0.55),
        "right_wrist": (0.64, 0.55),
    }
    points = [make_landmark(0.5, 0.5) for _ in range(33)]
    for name, (x, y) in defaults.items():
        points[name_to_idx[name]] = make_landmark(x, y)
    for name, pt in overrides.items():
        points[name_to_idx[name]] = pt
    return points


def make_pose(landmarks: list) -> PoseResult:
    """构造 PoseResult(已检测到人)。"""
    return PoseResult(landmarks=landmarks, raw_landmarks=landmarks, person_detected=True)


def make_empty_pose() -> PoseResult:
    """构造未检测到人的 PoseResult。"""
    return PoseResult(landmarks=None, raw_landmarks=None, person_detected=False)


# ---------- 1~4:手部动作判定 ----------

class TestHandActionDetection:
    """手部动作判定测试(使用 recognize_current,不受冷却影响)。"""

    def test_left_hand_up(self) -> None:
        """用户左手举起 → LEFT_HAND_UP。

        镜像约定:用户左手 = MediaPipe RIGHT_WRIST。
        让 lm[RIGHT_WRIST] 高于鼻子 → 触发 LEFT_HAND_UP。
        """
        rec = ActionRecognizer()
        # 鼻子 y=0.10,阈值默认 0.05,手腕 y < 0.05
        rw_up = make_landmark(0.64, 0.03)  # MediaPipe RIGHT_WRIST 高
        lw_down = make_landmark(0.36, 0.55)  # MediaPipe LEFT_WRIST 低
        lm = make_landmarks(left_wrist=lw_down, right_wrist=rw_up)
        state = rec.recognize_current(make_pose(lm))
        assert state.hand_action == "LEFT_HAND_UP"

    def test_right_hand_up(self) -> None:
        """用户右手举起 → RIGHT_HAND_UP。

        镜像约定:用户右手 = MediaPipe LEFT_WRIST。
        让 lm[LEFT_WRIST] 高于鼻子 → 触发 RIGHT_HAND_UP。
        """
        rec = ActionRecognizer()
        lw_up = make_landmark(0.36, 0.03)  # MediaPipe LEFT_WRIST 高
        rw_down = make_landmark(0.64, 0.55)  # MediaPipe RIGHT_WRIST 低
        lm = make_landmarks(left_wrist=lw_up, right_wrist=rw_down)
        state = rec.recognize_current(make_pose(lm))
        assert state.hand_action == "RIGHT_HAND_UP"

    def test_both_hands_up(self) -> None:
        """双手都举起 → BOTH_HANDS_UP(而非 LEFT/RIGHT)。"""
        rec = ActionRecognizer()
        lw_up = make_landmark(0.36, 0.03)
        rw_up = make_landmark(0.64, 0.03)
        lm = make_landmarks(left_wrist=lw_up, right_wrist=rw_up)
        state = rec.recognize_current(make_pose(lm))
        assert state.hand_action == "BOTH_HANDS_UP"

    def test_no_hand_up(self) -> None:
        """双手自然下垂 → HAND_NONE。"""
        rec = ActionRecognizer()
        # 默认姿态:手腕 y=0.55,鼻子 y=0.10,0.55 > 0.05,不举手
        lm = make_landmarks()
        state = rec.recognize_current(make_pose(lm))
        assert state.hand_action == HAND_NONE

    def test_no_person_returns_none_action(self) -> None:
        """未检测到人时 → HAND_NONE。"""
        rec = ActionRecognizer()
        state = rec.recognize_current(make_empty_pose())
        assert state.hand_action == HAND_NONE


# ---------- 5~8:镜像交换规则与匹配判定 ----------

class TestMirrorAndMatch:
    """镜像交换规则与 check_match 测试。"""

    def test_mirror_left_wrist_is_user_right_hand(self) -> None:
        """镜像规则验证:仅 MediaPipe LEFT_WRIST 高 → 用户右手举起。

        这是 v4.2 最关键的镜像约定:不能直接"LEFT_WRIST 高就判 LEFT_HAND_UP"。
        """
        rec = ActionRecognizer()
        # 仅 LEFT_WRIST 高,RIGHT_WRIST 低
        lw_up = make_landmark(0.36, 0.03)
        rw_down = make_landmark(0.64, 0.55)
        lm = make_landmarks(left_wrist=lw_up, right_wrist=rw_down)
        state = rec.recognize_current(make_pose(lm))
        # 镜像后应为 RIGHT_HAND_UP,而非 LEFT_HAND_UP
        assert state.hand_action == "RIGHT_HAND_UP"
        assert state.hand_action != "LEFT_HAND_UP"

    def test_mirror_right_wrist_is_user_left_hand(self) -> None:
        """镜像规则验证:仅 MediaPipe RIGHT_WRIST 高 → 用户左手举起。"""
        rec = ActionRecognizer()
        lw_down = make_landmark(0.36, 0.55)
        rw_up = make_landmark(0.64, 0.03)
        lm = make_landmarks(left_wrist=lw_down, right_wrist=rw_up)
        state = rec.recognize_current(make_pose(lm))
        assert state.hand_action == "LEFT_HAND_UP"
        assert state.hand_action != "RIGHT_HAND_UP"

    def test_match_correct(self) -> None:
        """目标动作与当前动作相同 → MATCH_CORRECT。"""
        assert ActionRecognizer.check_match("LEFT_HAND_UP", "LEFT_HAND_UP") == MATCH_CORRECT
        assert ActionRecognizer.check_match("RIGHT_HAND_UP", "RIGHT_HAND_UP") == MATCH_CORRECT
        assert ActionRecognizer.check_match("BOTH_HANDS_UP", "BOTH_HANDS_UP") == MATCH_CORRECT

    def test_match_wrong(self) -> None:
        """目标动作与当前动作不同 → MATCH_WRONG(含 HAND_NONE)。"""
        assert ActionRecognizer.check_match("LEFT_HAND_UP", "RIGHT_HAND_UP") == MATCH_WRONG
        assert ActionRecognizer.check_match("LEFT_HAND_UP", "BOTH_HANDS_UP") == MATCH_WRONG
        assert ActionRecognizer.check_match("BOTH_HANDS_UP", HAND_NONE) == MATCH_WRONG
        assert ActionRecognizer.check_match("RIGHT_HAND_UP", HAND_NONE) == MATCH_WRONG


# ---------- 9~10:冷却与重置 ----------

class TestCooldownAndReset:
    """冷却与重置测试(使用 recognize,受冷却影响)。"""

    def test_cooldown_prevents_repeat(self) -> None:
        """冷却期内同一动作不应重复触发。"""
        rec = ActionRecognizer(cooldown=10.0)
        lw_up = make_landmark(0.36, 0.03)
        rw_up = make_landmark(0.64, 0.03)
        lm = make_landmarks(left_wrist=lw_up, right_wrist=rw_up)
        # 第一次触发
        ev1 = rec.recognize(make_pose(lm))
        assert ev1 is not None
        assert ev1.name == "BOTH_HANDS_UP"
        # 冷却期内再识别,不应触发
        ev2 = rec.recognize(make_pose(lm))
        assert ev2 is None

    def test_reset_clears_cooldown(self) -> None:
        """reset 后动作可立即再次触发。"""
        rec = ActionRecognizer(cooldown=10.0)
        lw_up = make_landmark(0.36, 0.03)
        rw_up = make_landmark(0.64, 0.03)
        lm = make_landmarks(left_wrist=lw_up, right_wrist=rw_up)
        ev1 = rec.recognize(make_pose(lm))
        assert ev1 is not None
        # 重置
        rec.reset()
        assert rec.recent_actions == []
        # 重置后可立即触发
        ev2 = rec.recognize(make_pose(lm))
        assert ev2 is not None
        assert ev2.name == "BOTH_HANDS_UP"
