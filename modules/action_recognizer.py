"""动作识别模块。

实现 3 种手部动作的判定逻辑:
- LEFT_HAND_UP    左手举起(左手腕高于鼻子)
- RIGHT_HAND_UP   右手举起(右手腕高于鼻子)
- BOTH_HANDS_UP   双手举起(左右手腕同时高于鼻子)

每个动作有冷却时间,避免短时间内重复触发。
"""
import logging
import time
from dataclasses import dataclass
from typing import List, Optional

import config
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

# 无动作状态名
HAND_NONE = "HAND_NONE"

# 手部动作名(3 种,与 HAND_NONE 一致使用模块级常量,便于外部导入与去重)
LEFT_HAND_UP = "LEFT_HAND_UP"
RIGHT_HAND_UP = "RIGHT_HAND_UP"
BOTH_HANDS_UP = "BOTH_HANDS_UP"

# 动作匹配结果(用于状态机判定 COUNTING 阶段动作正确性)
MATCH_CORRECT = "MATCH_CORRECT"     # 当前动作 == 目标动作
MATCH_WRONG = "MATCH_WRONG"         # 当前动作 != 目标 且 != HAND_NONE
MATCH_NEUTRAL = "MATCH_NEUTRAL"     # 当前动作 == HAND_NONE(中性,不计错误)


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
class HandActionState:
    """手部动作状态(每帧实时判定,不受冷却限制)。

    Attributes:
        hand_action: 手部动作(BOTH_HANDS_UP / LEFT_HAND_UP / RIGHT_HAND_UP / HAND_NONE)。
        timestamp: 判定时间戳(time.time)。
    """
    hand_action: str
    timestamp: float


class ActionRecognizer:
    """动作识别器,基于关键点坐标判断手部动作并管理冷却。

    Attributes:
        cooldown: 动作冷却时间(秒)。
        hand_threshold: 手腕高于鼻子的偏移阈值。
    """

    # 动作事件名 -> 中文显示名
    ACTION_DISPLAY_NAMES = {
        "LEFT_HAND_UP": "左手举起",
        "RIGHT_HAND_UP": "右手举起",
        "BOTH_HANDS_UP": "双手举起",
        "HAND_NONE": "无手部动作",
    }

    def __init__(
        self,
        cooldown: float = config.ACTION_COOLDOWN,
        hand_threshold: float = config.HAND_UP_THRESHOLD,
    ) -> None:
        """初始化动作识别器。

        Args:
            cooldown: 动作冷却时间(秒)。
            hand_threshold: 手腕高于鼻子的偏移阈值(归一化)。
        """
        self.cooldown: float = cooldown
        self.hand_threshold: float = hand_threshold

        # 动作名 -> 上次触发时间戳
        self._last_trigger: dict = {}
        # 最近触发的动作列表(用于屏幕底部显示)
        self.recent_actions: List[ActionEvent] = []

    def recognize(self, pose_result: PoseResult) -> Optional[ActionEvent]:
        """对一帧姿态结果进行手部动作识别。

        Args:
            pose_result: PoseDetector 输出的姿态结果。

        Returns:
            Optional[ActionEvent]: 若触发新动作返回事件,否则返回 None。
        """
        if not pose_result.person_detected or pose_result.landmarks is None:
            return None
        lm = pose_result.landmarks

        candidates = self._detect_hand_candidates(lm)

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

    def recognize_current(self, pose_result: PoseResult) -> HandActionState:
        """对一帧姿态结果进行手部实时判定(不受冷却限制)。

        Args:
            pose_result: PoseDetector 输出的姿态结果。

        Returns:
            HandActionState: 当前帧的手部动作状态。
        """
        if not pose_result.person_detected or pose_result.landmarks is None:
            return HandActionState(HAND_NONE, time.time())
        lm = pose_result.landmarks
        return HandActionState(
            self._detect_hand_action(lm),
            time.time(),
        )

    def _detect_hand_action(self, lm: List[LandmarkPoint]) -> str:
        """判定手部动作。

        注意:主循环对画面做了水平翻转(镜像),因此 MediaPipe 的
        LEFT_WRIST 实际对应用户右手,RIGHT_WRIST 对应用户左手,
        判定时需交换两者,使识别结果与用户直觉一致。

        Args:
            lm: 33 个关键点列表。

        Returns:
            str: BOTH_HANDS_UP / LEFT_HAND_UP / RIGHT_HAND_UP / HAND_NONE。
        """
        # 画面镜像:MediaPipe 的 LEFT_WRIST=用户右手,RIGHT_WRIST=用户左手
        left_up = self._is_hand_up(lm[RIGHT_WRIST], lm[NOSE])
        right_up = self._is_hand_up(lm[LEFT_WRIST], lm[NOSE])
        if left_up and right_up:
            return "BOTH_HANDS_UP"
        if left_up:
            return "LEFT_HAND_UP"
        if right_up:
            return "RIGHT_HAND_UP"
        return HAND_NONE

    def _detect_hand_candidates(self, lm: List[LandmarkPoint]) -> List[str]:
        """检测本帧满足条件的手部候选动作(按优先级排序)。

        优先级:双手 > 单手。
        注意:画面水平翻转(镜像),LEFT_WRIST/RIGHT_WRIST 需交换判定。

        Args:
            lm: 33 个关键点列表。

        Returns:
            List[str]: 候选动作名列表(按优先级降序)。
        """
        candidates: List[str] = []
        # 画面镜像:MediaPipe 的 LEFT_WRIST=用户右手,RIGHT_WRIST=用户左手
        left_up = self._is_hand_up(lm[RIGHT_WRIST], lm[NOSE])
        right_up = self._is_hand_up(lm[LEFT_WRIST], lm[NOSE])
        if left_up and right_up:
            candidates.append("BOTH_HANDS_UP")
        else:
            if left_up:
                candidates.append("LEFT_HAND_UP")
            if right_up:
                candidates.append("RIGHT_HAND_UP")
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

    @staticmethod
    def check_match(target: str, hand: str) -> str:
        """判断当前手部动作与目标动作的匹配关系(供状态机使用)。

        规则:
            - hand == target  -> MATCH_CORRECT
            - hand != target(含 HAND_NONE) -> MATCH_WRONG

        无动作(HAND_NONE)也视为错误,触发惩罚充气。

        Args:
            target: 目标动作名(LEFT_HAND_UP / RIGHT_HAND_UP / BOTH_HANDS_UP)。
            hand: 当前帧识别到的手部动作名。

        Returns:
            str: MATCH_CORRECT / MATCH_WRONG。
        """
        if hand == target:
            return MATCH_CORRECT
        return MATCH_WRONG
