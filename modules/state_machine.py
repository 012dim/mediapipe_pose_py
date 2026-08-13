"""8 状态有限状态机:控制 Arduino 气泵 + 灯箱交互流程。

状态流转:
    INIT        充气 a 秒, gass=a
        ↓
    WAITING     等人在线 ≥ n1 秒
        ↓
    EXTRACTING  n+=1, 随机抽动作, 亮对应灯
        ↓
    COUNTING    计时 random(n2,n3), 判动作正确性
        ↓ (错误)             ↓ (计时完)
    INFLATING   暂停计时,       INTERVAL  灭灯
        ↓ (正确)              n<3 → EXTRACTING ; n=3 → ENDING
        ↗ 回 COUNTING          ↓
                            ENDING  三灯闪 3 次, 人离开 ≥ n4 → DEFLATING
                                ↓
                            DEFLATING  放气 b 秒, gass=0
                                ↓
                              回 INIT

安全机制:在 EXTRACTING/COUNTING/INFLATING/INTERVAL/ENDING 状态下,
若人离开 ≥ n4 秒,立即触发 DEFLATING(发 STOP_ALL → LIGHT_ALL_OFF → DEFLATE_ALL)。
INFLATING 中若 gass 达到 GAS_MAX,同样触发安全放气(防过充)。
"""
import logging
import random
import time
from dataclasses import dataclass
from typing import Callable, Dict

import config
from modules.action_recognizer import (
    ActionRecognizer,
    HAND_NONE,
    MATCH_CORRECT,
    MATCH_WRONG,
)
from modules.pose_detector import LandmarkPoint, PoseResult
from modules.serial_sender import LightSender, PumpGroupSender

logger = logging.getLogger(__name__)

# ============ 状态名 ============
STATE_INIT = "INIT"
STATE_WAITING = "WAITING"
STATE_EXTRACTING = "EXTRACTING"
STATE_COUNTING = "COUNTING"
STATE_INFLATING = "INFLATING"
STATE_INTERVAL = "INTERVAL"
STATE_ENDING = "ENDING"
STATE_DEFLATING = "DEFLATING"
STATE_SAFE_STOP = "SAFE_STOP"   # 安全停止:任一泵控板发送失败,全组停机放气后等待退出

# 候选动作池
ACTION_POOL = ("LEFT_HAND_UP", "RIGHT_HAND_UP", "BOTH_HANDS_UP")

# 核心躯干关键点索引(鼻子 / 双肩),用于可靠性校验
# 误检产生的"人"通常这些点可见度极低,此校验能挡掉椅子/衣架/海报等误检
# 注意:不强制要求双髋(23,24),因为半身入镜时髋部常不可见,会导致真人被误判为不可靠
CORE_LANDMARK_INDICES = (0, 11, 12)
# 核心关键点可见度阈值(低于此值视为不可靠)
CORE_VISIBILITY_THRESHOLD = 0.5

# 状态中文名(供可视化使用)
STATE_DISPLAY_NAMES: Dict[str, str] = {
    STATE_INIT: "充气",
    STATE_WAITING: "等人",
    STATE_EXTRACTING: "抽题",
    STATE_COUNTING: "计时",
    STATE_INFLATING: "惩罚充气",
    STATE_INTERVAL: "间隔",
    STATE_ENDING: "结束",
    STATE_DEFLATING: "放气",
    STATE_SAFE_STOP: "安全停止",
}


@dataclass
class StateSnapshot:
    """状态机快照(供可视化器使用)。

    Attributes:
        state: 当前状态名。
        state_display: 状态中文名。
        gass: 当前充气次数。
        target_action: 当前目标动作(COUNTING/INFLATING 中有效,否则 HAND_NONE)。
        n_count: 已抽取动作次数(0..LOOP_COUNT_MAX)。
        time_remaining: 计时剩余(秒);-1 表示不显示。
        elapsed: 当前状态已耗时(秒)。
        progress: 0..1 进度。
        no_person: 当前帧是否无可靠人在线(供 WAITING 状态可视化区分"等人中"与"卡死")。
        inflate_locked: gass 达上限后充气锁定,后续充气指令不触发,直到 DEFLATING 恢复。
        lights_on: 当前稳定亮着的灯号元组(如 (1,) 或 (1,2,3)),供可视化显示灯泡状态。
    """
    state: str
    state_display: str
    gass: int
    target_action: str
    n_count: int
    time_remaining: float
    elapsed: float
    progress: float
    no_person: bool = False
    inflate_locked: bool = False
    lights_on: tuple = ()


class StateMachine:
    """8 状态有限状态机。

    主循环每帧调用 update(person_detected, hand_action),返回 StateSnapshot。
    所有串口发送失败均由 PumpGroupSender/LightSender 内部容错;泵组任一板
    发送失败会触发 stop_all_best_effort 并进入 SAFE_STOP 态。
    """

    # 在这些状态下,人消失 ≥ n4 触发安全放气
    SAFETY_STATES = frozenset({
        STATE_EXTRACTING, STATE_COUNTING, STATE_INFLATING,
        STATE_INTERVAL, STATE_ENDING,
    })

    def __init__(self, pump: PumpGroupSender, light: LightSender) -> None:
        """初始化状态机。

        Args:
            pump: 泵组发送器(3 块泵控 UNO: PUMP_A/B/C)。
            light: 灯箱串口发送器(第 4 块 UNO)。
        """
        self.pump: PumpGroupSender = pump
        self.light: LightSender = light

        # 状态变量
        self.state: str = STATE_INIT
        self.gass: int = 0
        self.n_count: int = 0
        self.target_action: str = HAND_NONE
        # gass 达 GAS_MAX 后锁定,后续充气指令不触发,直到 DEFLATING 重置
        self._inflate_locked: bool = False
        # 当前稳定亮着的灯号集合 {1,2,3}(ENDING 闪烁/其他灭灯时为空集)
        self._lights_on: set = set()

        # 计时变量
        self._state_enter_time: float = time.time()
        self._last_update_time: float = time.time()
        self._person_confirm_start: float = 0.0       # WAITING 中人持续在线起点
        self._person_confirm_elapsed: float = 0.0
        self._last_person_seen_time: float = time.time()
        self._counting_duration: float = 0.0           # COUNTING 目标时长
        self._counting_elapsed: float = 0.0            # COUNTING 已计时(扣除 INFLATING)
        self._last_inflate_m_time: float = 0.0         # 上次 INFLATE_M 时间
        # 当前帧是否为可靠人(供 _snapshot 的 no_person 字段使用)
        self._current_person_reliable: bool = False

        # 进入 INIT
        self._enter_init()

    # ============ 主循环接口 ============
    def update(self, pose_result: PoseResult, hand_action: str) -> StateSnapshot:
        """每帧调用,推进状态机。

        对 person_detected 叠加核心关键点可见度校验,得到 person_reliable,
        再用 person_reliable 驱动 WAITING 确认计时与安全机制,避免 MediaPipe
        对椅子/衣架/海报等杂物的误检导致状态机空转。

        Args:
            pose_result: PoseDetector 输出的姿态结果(含 person_detected 与 landmarks)。
            hand_action: 当前帧识别到的手部动作名。

        Returns:
            StateSnapshot: 当前状态快照(供可视化)。
        """
        now = time.time()
        dt = now - self._last_update_time if self._last_update_time > 0 else 0.0
        self._last_update_time = now

        # 可靠性校验:person_detected + 核心躯干关键点可见度
        person_reliable = self._is_person_reliable(
            pose_result.person_detected, pose_result.landmarks,
        )
        self._current_person_reliable = person_reliable
        if person_reliable:
            self._last_person_seen_time = now

        # 安全机制:可靠人离开 ≥ n4
        if self.state in self.SAFETY_STATES:
            absence = now - self._last_person_seen_time
            if absence >= config.ABSENCE_TIMEOUT_N4:
                logger.warning("人离开 %.1f 秒,触发安全放气", absence)
                self._trigger_safety()
                return self._snapshot(now)

        # 状态分发(传 person_reliable 而非原始 person_detected)
        handler = self._handlers().get(self.state)
        if handler is not None:
            handler(now, dt, person_reliable, hand_action)
        return self._snapshot(now)

    def reset(self) -> None:
        """重置状态机到 INIT(用于异常恢复或手动重置)。"""
        logger.info("状态机重置")
        self._enter_init()

    # ============ 状态进入方法 ============
    def _enter_init(self) -> None:
        self.state = STATE_INIT
        self._state_enter_time = time.time()
        self.gass = 0
        self.n_count = 0
        self.target_action = HAND_NONE
        self._inflate_locked = False
        self._lights_on = set()
        self._person_confirm_start = 0.0
        self._person_confirm_elapsed = 0.0
        self._counting_duration = 0.0
        self._counting_elapsed = 0.0
        if not self.pump.send_inflate_all(config.INFLATE_TIME_A):
            self._enter_safe_stop()
            return
        logger.info("[INIT] 充气 %d 秒", config.INFLATE_TIME_A)

    def _enter_waiting(self) -> None:
        self.state = STATE_WAITING
        self._state_enter_time = time.time()
        self.gass = int(config.INFLATE_TIME_A)
        self._person_confirm_start = 0.0
        self._person_confirm_elapsed = 0.0
        logger.info("[WAITING] 等待人在线 ≥ %.1f 秒", config.PERSON_CONFIRM_N1)

    def _enter_extracting(self) -> None:
        self.state = STATE_EXTRACTING
        self._state_enter_time = time.time()
        self.n_count += 1
        self.target_action = random.choice(ACTION_POOL)
        light_id = self.light.light_id_for_action(self.target_action)
        if light_id is not None:
            self.light.send_light_on(light_id)
            self._lights_on = {light_id}
        else:
            self._lights_on = set()
        logger.info("[EXTRACTING] 第 %d/%d 题,目标: %s (灯 %s)",
                    self.n_count, config.LOOP_COUNT_MAX,
                    self.target_action, light_id)

    def _enter_counting_fresh(self) -> None:
        """从 EXTRACTING 进入 COUNTING:随机生成计时时长。"""
        self.state = STATE_COUNTING
        self._state_enter_time = time.time()
        self._counting_duration = random.uniform(
            config.COUNT_MIN_N2, config.COUNT_MAX_N3,
        )
        self._counting_elapsed = 0.0
        logger.info("[COUNTING] 计时 %.2f 秒,目标: %s",
                    self._counting_duration, self.target_action)

    def _enter_counting_resume(self) -> None:
        """从 INFLATING 回到 COUNTING:保留原计时,继续累计。"""
        self.state = STATE_COUNTING
        self._state_enter_time = time.time()
        logger.info("[COUNTING] 恢复计时,剩余 %.2f 秒",
                    max(0.0, self._counting_duration - self._counting_elapsed))

    def _enter_inflating(self) -> None:
        self.state = STATE_INFLATING
        self._state_enter_time = time.time()
        self._last_inflate_m_time = 0.0  # 触发立即发送第一次
        logger.info("[INFLATING] 动作错误,开始惩罚充气,当前 gass=%d", self.gass)

    def _enter_interval(self) -> None:
        self.state = STATE_INTERVAL
        self._state_enter_time = time.time()
        self.light.send_all_off()
        self._lights_on = set()
        logger.info("[INTERVAL] 灭灯,等待 %.1f 秒", config.LOOP_INTERVAL)

    def _enter_ending(self) -> None:
        self.state = STATE_ENDING
        self._state_enter_time = time.time()
        self.light.send_flash(config.LOOP_COUNT_MAX)
        logger.info("[ENDING] 三灯闪 %d 次,等待人离开 ≥ %.1f 秒",
                    config.LOOP_COUNT_MAX, config.ABSENCE_TIMEOUT_N4)

    def _enter_deflating(self) -> None:
        self.state = STATE_DEFLATING
        self._state_enter_time = time.time()
        if not self.pump.send_deflate_all(config.DEFLATE_TIME_B):
            self._enter_safe_stop()
            return
        self.light.send_all_off()
        self._lights_on = set()
        logger.info("[DEFLATING] 放气 %d 秒", config.DEFLATE_TIME_B)

    def _enter_safe_stop(self) -> None:
        """安全停止态:任一泵控板发送失败时进入。

        PumpGroupSender 内部已 best-effort 广播 STOP_ALL,此处再尝试放气,
        放气满 SAFE_STOP_DEFLATE_TIME 秒后保持等待退出(不自动恢复)。
        """
        self.state = STATE_SAFE_STOP
        self._state_enter_time = time.time()
        self._lights_on = set()
        # best-effort 尝试放气(可能也失败,但尽力)
        self.pump.send_deflate_all(config.SAFE_STOP_DEFLATE_TIME)
        self.light.send_all_off()
        logger.error("[SAFE_STOP] 泵控板发送失败,全组停机,放气 %.1f 秒后等待用户退出",
                     config.SAFE_STOP_DEFLATE_TIME)

    # ============ 状态更新方法 ============
    def _update_init(self, now: float, dt: float, person: bool, hand: str) -> None:
        if now - self._state_enter_time >= config.INFLATE_TIME_A:
            self._enter_waiting()

    def _update_waiting(self, now: float, dt: float, person: bool, hand: str) -> None:
        if person:
            if self._person_confirm_start <= 0.0:
                self._person_confirm_start = now
            self._person_confirm_elapsed = now - self._person_confirm_start
            if self._person_confirm_elapsed >= config.PERSON_CONFIRM_N1:
                self._enter_extracting()
        else:
            # 人中途消失,重置确认计时
            self._person_confirm_start = 0.0
            self._person_confirm_elapsed = 0.0

    def _update_extracting(self, now: float, dt: float, person: bool, hand: str) -> None:
        # EXTRACTING 为瞬态:下一帧立即进入 COUNTING
        self._enter_counting_fresh()

    def _update_counting(self, now: float, dt: float, person: bool, hand: str) -> None:
        match = ActionRecognizer.check_match(self.target_action, hand)
        if match == MATCH_CORRECT:
            # 只有动作正确时才推进计时(错误/无动作均暂停,不重置)
            self._counting_elapsed += dt
            if self._counting_elapsed >= self._counting_duration:
                logger.info("[COUNTING] 计时完成,进入 INTERVAL")
                self._enter_interval()
        elif match == MATCH_WRONG:
            # 动作错误(含无动作):暂停计时
            if self._inflate_locked:
                # 充气已锁定,不再触发充气,计时暂停等待用户做对动作
                pass
            else:
                self._enter_inflating()

    def _update_inflating(self, now: float, dt: float, person: bool, hand: str) -> None:
        # 每秒发一次 INFLATE_M, gass += 1(达上限后不再发)
        if self._last_inflate_m_time <= 0.0 or now - self._last_inflate_m_time >= 1.0:
            if self.gass < config.GAS_MAX:
                if not self.pump.send_inflate_m():
                    self._enter_safe_stop()
                    return
                self.gass += 1
                self._last_inflate_m_time = now
                logger.info("[INFLATING] INFLATE_M, gass=%d/%d",
                            self.gass, config.GAS_MAX)
            if self.gass >= config.GAS_MAX and not self._inflate_locked:
                # 达上限:停止充气,锁定后续充气指令,回 COUNTING 继续计时
                # 不触发 DEFLATING,状态机正常流转直到 ENDING → DEFLATING 后恢复
                logger.warning("[INFLATING] gass 达上限 %d,锁定充气,后续不再充气",
                               config.GAS_MAX)
                self._inflate_locked = True
                self.pump.send_stop_all()  # best-effort
                self._enter_counting_resume()
                return
        # 检查是否回到正确动作
        match = ActionRecognizer.check_match(self.target_action, hand)
        if match == MATCH_CORRECT:
            self.pump.send_stop_all()  # best-effort
            logger.info("[INFLATING] 动作正确,回 COUNTING")
            self._enter_counting_resume()

    def _update_interval(self, now: float, dt: float, person: bool, hand: str) -> None:
        if now - self._state_enter_time >= config.LOOP_INTERVAL:
            if self.n_count < config.LOOP_COUNT_MAX:
                self._enter_extracting()
            else:
                self._enter_ending()

    def _update_ending(self, now: float, dt: float, person: bool, hand: str) -> None:
        # 兜底:超时强制放气,防止误检导致 ENDING 死锁
        if now - self._state_enter_time >= config.ENDING_TIMEOUT:
            logger.warning("[ENDING] 超时 %.0f 秒,强制放气", config.ENDING_TIMEOUT)
            self.pump.send_stop_all()  # best-effort
            self._enter_deflating()

    def _update_deflating(self, now: float, dt: float, person: bool, hand: str) -> None:
        if now - self._state_enter_time >= config.DEFLATE_TIME_B:
            self.gass = 0
            self._enter_init()

    def _update_safe_stop(self, now: float, dt: float, person: bool, hand: str) -> None:
        """SAFE_STOP 态:放气完成后保持等待用户退出,不自动恢复。"""
        # 不做任何转换,等待用户按 q 退出
        pass

    # ============ 安全机制 ============
    def _trigger_safety(self) -> None:
        """人离开超时或充气超限,触发安全放气序列。

        顺序:STOP_ALL → LIGHT_ALL_OFF → DEFLATE_ALL(在 _enter_deflating 中发送)。
        """
        self.pump.send_stop_all()  # best-effort
        self.light.send_all_off()
        self._enter_deflating()

    def _is_person_reliable(
        self,
        person_detected: bool,
        landmarks,
    ) -> bool:
        """判断是否为可靠的人在线。

        person_detected 为 True 且核心上半身关键点(鼻子/双肩)可见度均高于阈值
        才视为可靠。MediaPipe 在低置信度阈值下会对椅子靠背、挂衣、海报等产生误检,
        这类误检的躯干点可见度通常极低,此校验能挡掉绝大多数误检。

        不强制要求双髋可见,因为半身入镜(坐姿/近距离)时髋部常不在画面内,
        强制要求会导致真人被误判为不可靠。

        Args:
            person_detected: MediaPipe 原始 person_detected 信号。
            landmarks: 33 个关键点列表(PoseResult.landmarks,可能为 None)。

        Returns:
            bool: 可靠人在线返回 True。
        """
        if not person_detected or landmarks is None:
            return False
        try:
            return all(
                landmarks[i].visibility > CORE_VISIBILITY_THRESHOLD
                for i in CORE_LANDMARK_INDICES
            )
        except (IndexError, AttributeError):
            return False

    # ============ 快照 ============
    def _snapshot(self, now: float) -> StateSnapshot:
        """生成当前状态快照供可视化使用。"""
        elapsed = now - self._state_enter_time
        remaining = -1.0
        progress = 0.0

        if self.state == STATE_INIT:
            dur = config.INFLATE_TIME_A
            remaining = max(0.0, dur - elapsed)
            progress = elapsed / dur if dur > 0 else 0.0
        elif self.state == STATE_WAITING:
            # 显示确认进度
            remaining = max(0.0, config.PERSON_CONFIRM_N1 - self._person_confirm_elapsed)
            progress = (self._person_confirm_elapsed / config.PERSON_CONFIRM_N1
                        if config.PERSON_CONFIRM_N1 > 0 else 0.0)
        elif self.state == STATE_COUNTING:
            remaining = max(0.0, self._counting_duration - self._counting_elapsed)
            progress = (self._counting_elapsed / self._counting_duration
                        if self._counting_duration > 0 else 0.0)
        elif self.state == STATE_INFLATING:
            # 显示 gass / GAS_MAX 进度
            remaining = max(0.0, float(config.GAS_MAX - self.gass))
            progress = self.gass / config.GAS_MAX if config.GAS_MAX > 0 else 0.0
        elif self.state == STATE_INTERVAL:
            dur = config.LOOP_INTERVAL
            remaining = max(0.0, dur - elapsed)
            progress = elapsed / dur if dur > 0 else 0.0
        elif self.state == STATE_DEFLATING:
            dur = config.DEFLATE_TIME_B
            remaining = max(0.0, dur - elapsed)
            progress = elapsed / dur if dur > 0 else 0.0
        elif self.state == STATE_SAFE_STOP:
            # 显示安全放气进度(放气完成后保持 100%)
            dur = config.SAFE_STOP_DEFLATE_TIME
            remaining = max(0.0, dur - elapsed)
            progress = min(1.0, elapsed / dur) if dur > 0 else 0.0
        # EXTRACTING / ENDING: 不显示计时

        # no_person:当前帧无可靠人(主要用于 WAITING 状态可视化区分"等人中")
        no_person = not self._current_person_reliable

        return StateSnapshot(
            state=self.state,
            state_display=STATE_DISPLAY_NAMES.get(self.state, self.state),
            gass=self.gass,
            target_action=self.target_action,
            n_count=self.n_count,
            time_remaining=remaining,
            elapsed=elapsed,
            progress=min(1.0, max(0.0, progress)),
            no_person=no_person,
            inflate_locked=self._inflate_locked,
            lights_on=tuple(sorted(self._lights_on)),
        )

    # ============ Handler 表(惰性构建) ============
    def _handlers(self) -> Dict[str, Callable]:
        """返回状态 -> 处理函数的映射。"""
        return {
            STATE_INIT: self._update_init,
            STATE_WAITING: self._update_waiting,
            STATE_EXTRACTING: self._update_extracting,
            STATE_COUNTING: self._update_counting,
            STATE_INFLATING: self._update_inflating,
            STATE_INTERVAL: self._update_interval,
            STATE_ENDING: self._update_ending,
            STATE_DEFLATING: self._update_deflating,
            STATE_SAFE_STOP: self._update_safe_stop,
        }
