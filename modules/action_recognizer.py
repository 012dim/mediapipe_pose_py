"""动作识别模块。

实现 6 种动作的判定逻辑:
- LEFT_HAND_UP    左手举起(左手腕高于鼻子)
- RIGHT_HAND_UP   右手举起(右手腕高于鼻子)
- BOTH_HANDS_UP   双手举起(左右手腕同时高于鼻子)
- STAND           站立(膝关节角度 > 160°)
- SIT             坐下(膝关节角度 < 130°)
- FALL_DETECTED   跌倒(髋肩高度差 / 肩宽 < 0.3)

每个动作有冷却时间,避免短时间内重复触发。
"""
import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional

import config
from modules.angle_calculator import calculate_angle
from modules.pose_detector import LandmarkPoint, PoseResult

logger = logging.getLogger(__name__)

# MediaPipe Pose 33 关键点索引
NOSE = 0
LEFT_SHOULDER = 11
RIGHT_SHOULDER = 12
LEFT_ELBOW = 13
RIGHT_ELBOW = 14
LEFT_WRIST = 15
RIGHT_WRIST = 16
LEFT_HIP = 23
RIGHT_HIP = 24
LEFT_KNEE = 25
RIGHT_KNEE = 26
LEFT_ANKLE = 27
RIGHT_ANKLE = 28

# 双通道"无动作"状态名
BODY_NONE = "BODY_NONE"
HAND_NONE = "HAND_NONE"


@dataclass
class ActionEvent:
    """识别到的动作事件。

    Attributes:
        name: 动作事件名(如 BOTH_HANDS_UP)。
        timestamp: 触发时间戳(time.time)。
        display_name: 用于屏幕显示的中文名。
    """
    name: str
    timestamp: float
    display_name: str


@dataclass
class DualChannelState:
    """双通道动作状态(每帧实时判定,不受冷却限制)。

    Attributes:
        body_action: 身体通道动作(STAND/SIT/FALL_DETECTED/BODY_NONE)。
        hand_action: 手部通道动作(BOTH_HANDS_UP/LEFT_HAND_UP/RIGHT_HAND_UP/HAND_NONE)。
        timestamp: 判定时间戳(time.time)。
    """
    body_action: str
    hand_action: str
    timestamp: float


class ActionRecognizer:
    """动作识别器,基于关键点坐标判断动作并管理冷却。

    Attributes:
        cooldown: 动作冷却时间(秒)。
        hand_threshold: 手腕高于鼻子的偏移阈值。
        knee_angle_stand: 站立角度阈值。
        knee_angle_sit: 坐下角度阈值。
        fall_ratio_threshold: 跌倒比率阈值。
    """

    # 动作事件名 -> 中文显示名
    ACTION_DISPLAY_NAMES = {
        "LEFT_HAND_UP": "左手举起",
        "RIGHT_HAND_UP": "右手举起",
        "BOTH_HANDS_UP": "双手举起",
        "STAND": "站立",
        "SIT": "坐下",
        "FALL_DETECTED": "跌倒",
        "BODY_NONE": "无身体动作",
        "HAND_NONE": "无手部动作",
    }

    def __init__(
        self,
        cooldown: float = config.ACTION_COOLDOWN,
        hand_threshold: float = config.HAND_UP_THRESHOLD,
        knee_angle_stand: float = config.KNEE_ANGLE_STAND,
        knee_angle_sit: float = config.KNEE_ANGLE_SIT,
        fall_ratio_threshold: float = config.FALL_RATIO_THRESHOLD,
    ) -> None:
        """初始化动作识别器。

        Args:
            cooldown: 动作冷却时间(秒)。
            hand_threshold: 手腕高于鼻子的偏移阈值(归一化)。
            knee_angle_stand: 站立膝关节角度阈值。
            knee_angle_sit: 坐下膝关节角度阈值。
            fall_ratio_threshold: 跌倒比率阈值。
        """
        self.cooldown: float = cooldown
        self.hand_threshold: float = hand_threshold
        self.knee_angle_stand: float = knee_angle_stand
        self.knee_angle_sit: float = knee_angle_sit
        self.fall_ratio_threshold: float = fall_ratio_threshold

        # 动作名 -> 上次触发时间戳
        self._last_trigger: dict = {}
        # 最近触发的动作列表(用于屏幕底部显示)
        self.recent_actions: List[ActionEvent] = []

    def recognize(self, pose_result: PoseResult) -> Optional[ActionEvent]:
        """对一帧姿态结果进行动作识别。

        Args:
            pose_result: PoseDetector 输出的姿态结果。

        Returns:
            Optional[ActionEvent]: 若触发新动作返回事件,否则返回 None。
        """
        if not pose_result.person_detected or pose_result.landmarks is None:
            return None
        lm = pose_result.landmarks

        # 候选动作列表(按优先级顺序检测)
        candidates = self._detect_candidates(lm)

        for action_name in candidates:
            if self._can_trigger(action_name):
                event = ActionEvent(
                    name=action_name,
                    timestamp=time.time(),
                    display_name=self.ACTION_DISPLAY_NAMES.get(action_name, action_name),
                )
                self._trigger(event)
                return event
        return None

    def recognize_current(self, pose_result: PoseResult) -> DualChannelState:
        """对一帧姿态结果进行双通道实时判定(不受冷却限制)。

        身体通道: STAND / SIT / FALL_DETECTED / BODY_NONE
        手部通道: BOTH_HANDS_UP / LEFT_HAND_UP / RIGHT_HAND_UP / HAND_NONE

        Args:
            pose_result: PoseDetector 输出的姿态结果。

        Returns:
            DualChannelState: 当前帧的双通道状态。
        """
        if not pose_result.person_detected or pose_result.landmarks is None:
            return DualChannelState(BODY_NONE, HAND_NONE, time.time())
        lm = pose_result.landmarks
        return DualChannelState(
            self._detect_body_action(lm),
            self._detect_hand_action(lm),
            time.time(),
        )

    def _detect_body_action(self, lm: List[LandmarkPoint]) -> str:
        """判定身体通道动作。

        优先级: 跌倒 > 坐/站。膝关节角度 < sit 阈值判坐,
        否则(含 130~160° 灰色地带)归为站立。

        Args:
            lm: 33 个关键点列表。

        Returns:
            str: STAND / SIT / FALL_DETECTED / BODY_NONE。
        """
        if self._check_fall(lm):
            return "FALL_DETECTED"
        knee_angle = self._calc_knee_angle(lm)
        if knee_angle is None:
            return BODY_NONE
        if knee_angle < self.knee_angle_sit:   # < 130° → SIT
            return "SIT"
        return "STAND"                          # >= 130° 算站立(含灰色地带)

    def _detect_hand_action(self, lm: List[LandmarkPoint]) -> str:
        """判定手部通道动作。

        Args:
            lm: 33 个关键点列表。

        Returns:
            str: BOTH_HANDS_UP / LEFT_HAND_UP / RIGHT_HAND_UP / HAND_NONE。
        """
        left_up = self._is_hand_up(lm[LEFT_WRIST], lm[NOSE])
        right_up = self._is_hand_up(lm[RIGHT_WRIST], lm[NOSE])
        if left_up and right_up:
            return "BOTH_HANDS_UP"
        if left_up:
            return "LEFT_HAND_UP"
        if right_up:
            return "RIGHT_HAND_UP"
        return HAND_NONE

    def _detect_candidates(self, lm: List[LandmarkPoint]) -> List[str]:
        """检测本帧所有满足条件的候选动作(按优先级排序)。

        优先级:跌倒 > 双手 > 单手 > 站坐。
        若触发高级别动作,则不再考虑低级别。

        Args:
            lm: 33 个关键点列表。

        Returns:
            List[str]: 候选动作名列表(按优先级降序)。
        """
        candidates: List[str] = []

        # 1) 跌倒:髋肩高度差 / 肩宽 < 阈值
        fall = self._check_fall(lm)
        if fall:
            candidates.append("FALL_DETECTED")

        # 2) 双手举起
        left_up = self._is_hand_up(lm[LEFT_WRIST], lm[NOSE])
        right_up = self._is_hand_up(lm[RIGHT_WRIST], lm[NOSE])
        if left_up and right_up:
            candidates.append("BOTH_HANDS_UP")
        else:
            if left_up:
                candidates.append("LEFT_HAND_UP")
            if right_up:
                candidates.append("RIGHT_HAND_UP")

        # 3) 站立 / 坐下(基于膝关节角度)
        knee_angle = self._calc_knee_angle(lm)
        if knee_angle is not None:
            if knee_angle > self.knee_angle_stand:
                candidates.append("STAND")
            elif knee_angle < self.knee_angle_sit:
                candidates.append("SIT")

        return candidates

    def _is_hand_up(self, wrist: LandmarkPoint, nose: LandmarkPoint) -> bool:
        """判断手腕是否举起(手腕 Y < 鼻子 Y - 阈值)。

        注意:图像坐标 Y 轴向下,所以 Y 越小位置越高。

        Args:
            wrist: 手腕关键点。
            nose: 鼻子关键点。

        Returns:
            bool: 手腕高于鼻子一定距离返回 True。
        """
        return wrist.y < (nose.y - self.hand_threshold)

    def _calc_knee_angle(self, lm: List[LandmarkPoint]) -> Optional[float]:
        """计算左右膝关节角度的平均值。

        Args:
            lm: 33 个关键点列表。

        Returns:
            Optional[float]: 平均膝关节角度(度),不可见时返回 None。
        """
        try:
            left = calculate_angle(
                (lm[LEFT_HIP].x, lm[LEFT_HIP].y),
                (lm[LEFT_KNEE].x, lm[LEFT_KNEE].y),
                (lm[LEFT_ANKLE].x, lm[LEFT_ANKLE].y),
            )
            right = calculate_angle(
                (lm[RIGHT_HIP].x, lm[RIGHT_HIP].y),
                (lm[RIGHT_KNEE].x, lm[RIGHT_KNEE].y),
                (lm[RIGHT_ANKLE].x, lm[RIGHT_ANKLE].y),
            )
            return (left + right) / 2.0
        except Exception as e:  # noqa: BLE001
            logger.warning("计算膝关节角度失败: %s", e)
            return None

    def _check_fall(self, lm: List[LandmarkPoint]) -> bool:
        """判断是否跌倒(髋肩高度差 / 肩宽 < 阈值)。

        站立时:肩在髋上方,y 差大,且肩宽正常,比值 > 阈值。
        跌倒时:身体接近水平,肩髋 Y 接近,比值变小,< 阈值。

        Args:
            lm: 33 个关键点列表。

        Returns:
            bool: 判定为跌倒返回 True。
        """
        try:
            shoulder_dy = abs(lm[LEFT_SHOULDER].y - lm[RIGHT_SHOULDER].y)
            # 髋肩平均高度差
            shoulder_y = (lm[LEFT_SHOULDER].y + lm[RIGHT_SHOULDER].y) / 2.0
            hip_y = (lm[LEFT_HIP].y + lm[RIGHT_HIP].y) / 2.0
            height_diff = abs(shoulder_y - hip_y)
            # 肩宽
            shoulder_width = abs(lm[LEFT_SHOULDER].x - lm[RIGHT_SHOULDER].x)
            if shoulder_width < 1e-3:
                return False
            ratio = height_diff / shoulder_width
            # 肩部本身不水平(头部歪斜)也忽略
            if shoulder_dy > 0.2:
                return False
            return ratio < self.fall_ratio_threshold
        except Exception as e:  # noqa: BLE001
            logger.warning("跌倒判定异常: %s", e)
            return False

    def _can_trigger(self, action_name: str) -> bool:
        """判断指定动作是否已过冷却期。

        Args:
            action_name: 动作事件名。

        Returns:
            bool: 可触发返回 True。
        """
        last = self._last_trigger.get(action_name, 0.0)
        return (time.time() - last) >= self.cooldown

    def _trigger(self, event: ActionEvent) -> None:
        """记录触发事件,更新冷却时间,并加入最近动作列表。

        Args:
            event: 触发的动作事件。
        """
        self._last_trigger[event.name] = event.timestamp
        self.recent_actions.append(event)
        # 保留最近 MAX_RECENT_ACTIONS 个
        if len(self.recent_actions) > config.MAX_RECENT_ACTIONS:
            self.recent_actions = self.recent_actions[-config.MAX_RECENT_ACTIONS:]

        # 终端打印:[HH:MM:SS] 动作:ACTION_NAME
        time_str = time.strftime("%H:%M:%S", time.localtime(event.timestamp))
        logger.info("动作:%s", event.name)
        print(f"[{time_str}] 动作:{event.name}")

    def reset(self) -> None:
        """重置动作识别状态:清空冷却与最近动作列表。"""
        self._last_trigger.clear()
        self.recent_actions.clear()
        logger.info("动作识别状态已重置")
