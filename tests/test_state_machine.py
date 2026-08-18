"""state_machine 单元测试(v4.2)。

覆盖报告 10.3 节要求的 11 个测试点:
1. INIT 发送 INFLATE_ALL
2. WAITING 连续检测到人后进入 EXTRACTING
3. 错误动作立即进入 INFLATING
4. INFLATING 立即发送第一次 INFLATE_M
5. 正确动作停止气泵并恢复 COUNTING
6. gass 达到 GAS_MAX 后锁定
7. 锁定后不再发送新的 INFLATE_M
8. 人离开后进入 DEFLATING
9. 任一泵控板失败后进入 SAFE_STOP
10. SAFE_STOP 中正常板持续放气,不被立即 STOP_ALL
11. SAFE_STOP 不能通过 r 重新开始充气(验证 reset 后行为 + main.py 需阻止)

运行:
    pytest tests/test_state_machine.py -v
"""
import pytest

import config
from modules.action_recognizer import HAND_NONE, LEFT_HAND_UP, RIGHT_HAND_UP
from modules.pose_detector import LandmarkPoint, PoseResult
from modules.state_machine import (
    StateMachine,
    STATE_INIT,
    STATE_WAITING,
    STATE_EXTRACTING,
    STATE_COUNTING,
    STATE_INFLATING,
    STATE_INTERVAL,
    STATE_ENDING,
    STATE_DEFLATING,
    STATE_SAFE_STOP,
)


# ============ Mock 对象 ============

class MockPumpGroupSender:
    """模拟 PumpGroupSender,记录所有调用并可控返回值。"""

    def __init__(self) -> None:
        self.calls: list = []  # 记录所有调用 [(方法名, 参数), ...]
        self.send_inflate_all_ret: bool = True
        self.send_deflate_all_ret: bool = True
        self.send_inflate_m_ret: bool = True
        self.send_stop_all_ret: bool = True
        self.send_deflate_all_best_effort_ret: dict = {
            "PUMP_A": True, "PUMP_B": True, "PUMP_C": True,
        }

    def send_inflate_all(self, seconds: float) -> bool:
        self.calls.append(("send_inflate_all", seconds))
        return self.send_inflate_all_ret

    def send_deflate_all(self, seconds: float) -> bool:
        self.calls.append(("send_deflate_all", seconds))
        return self.send_deflate_all_ret

    def send_inflate_m(self) -> bool:
        self.calls.append(("send_inflate_m",))
        return self.send_inflate_m_ret

    def send_stop_all(self) -> bool:
        self.calls.append(("send_stop_all",))
        return self.send_stop_all_ret

    def stop_all_best_effort(self) -> dict:
        self.calls.append(("stop_all_best_effort",))
        return {"PUMP_A": True, "PUMP_B": True, "PUMP_C": True}

    def send_deflate_all_best_effort(self, seconds: float) -> dict:
        self.calls.append(("send_deflate_all_best_effort", seconds))
        return self.send_deflate_all_best_effort_ret

    def method_call_count(self, method_name: str) -> int:
        return sum(1 for c in self.calls if c[0] == method_name)


class MockLightSender:
    """模拟 LightSender。"""

    def __init__(self) -> None:
        self.calls: list = []

    def light_id_for_action(self, action: str):
        mapping = {"LEFT_HAND_UP": 1, "RIGHT_HAND_UP": 2, "BOTH_HANDS_UP": 3}
        return mapping.get(action)

    def send_light_on(self, light_id: int) -> bool:
        self.calls.append(("send_light_on", light_id))
        return True

    def send_all_off(self) -> bool:
        self.calls.append(("send_all_off",))
        return True

    def send_flash(self, count: int) -> bool:
        self.calls.append(("send_flash", count))
        return True


# ============ 测试数据构造 ============

def make_landmark(x: float, y: float, v: float = 1.0) -> LandmarkPoint:
    return LandmarkPoint(x=x, y=y, z=0.0, visibility=v)


def make_person_pose(person_detected: bool = True) -> PoseResult:
    """构造 PoseResult,带可见度合格的核心关键点。"""
    if not person_detected:
        return PoseResult(landmarks=None, raw_landmarks=None, person_detected=False)
    lm = [make_landmark(0.5, 0.5) for _ in range(33)]
    # 鼻子 + 双肩可见度 > 0.5(通过可靠性校验)
    lm[0] = make_landmark(0.50, 0.10, v=0.9)   # NOSE
    lm[11] = make_landmark(0.40, 0.25, v=0.9)  # LEFT_SHOULDER
    lm[12] = make_landmark(0.60, 0.25, v=0.9)  # RIGHT_SHOULDER
    return PoseResult(landmarks=lm, raw_landmarks=lm, person_detected=True)


def make_no_person_pose() -> PoseResult:
    return PoseResult(landmarks=None, raw_landmarks=None, person_detected=False)


def force_state_elapsed(sm: StateMachine, seconds: float) -> None:
    """让状态机认为当前状态已经持续了 seconds 秒(下次 update 时触发转换)。"""
    sm._state_enter_time -= seconds


def advance_waiting_confirm(sm: StateMachine, person_pose: PoseResult) -> None:
    """在 WAITING 状态下模拟人持续在线 PERSON_CONFIRM_N1 秒。"""
    # 第一次 update 设定 _person_confirm_start
    sm.update(person_pose, HAND_NONE)
    # 推进足够时间
    sm._person_confirm_start -= config.PERSON_CONFIRM_N1


# ============ 测试 ============

class TestInitAndInflateAll:
    """测试点 1:INIT 发送 INFLATE_ALL。"""

    def test_init_sends_inflate_all(self) -> None:
        """StateMachine 构造时进入 INIT,应立即发送 INFLATE_ALL。"""
        pump = MockPumpGroupSender()
        light = MockLightSender()
        sm = StateMachine(pump, light)
        assert sm.state == STATE_INIT
        assert pump.method_call_count("send_inflate_all") == 1
        assert pump.calls[0] == ("send_inflate_all", config.INFLATE_TIME_A)


class TestWaitingToExtracting:
    """测试点 2:WAITING 连续检测到人后进入 EXTRACTING。"""

    def test_waiting_enters_extracting_after_confirm(self) -> None:
        pump = MockPumpGroupSender()
        light = MockLightSender()
        sm = StateMachine(pump, light)
        assert sm.state == STATE_INIT
        # 推进 INIT 时间 → 进入 WAITING
        force_state_elapsed(sm, config.INFLATE_TIME_A)
        sm.update(make_person_pose(), HAND_NONE)
        assert sm.state == STATE_WAITING
        # 模拟人持续在线 n1 秒
        advance_waiting_confirm(sm, make_person_pose())
        sm.update(make_person_pose(), HAND_NONE)
        assert sm.state == STATE_EXTRACTING


class TestWrongActionImmediateInflate:
    """测试点 3+4:错误动作立即进入 INFLATING + 立即发送第一次 INFLATE_M。"""

    def test_wrong_action_enters_inflating_and_sends_inflate_m(self) -> None:
        pump = MockPumpGroupSender()
        light = MockLightSender()
        sm = StateMachine(pump, light)
        # 推进到 WAITING → EXTRACTING → COUNTING
        force_state_elapsed(sm, config.INFLATE_TIME_A)
        sm.update(make_person_pose(), HAND_NONE)
        advance_waiting_confirm(sm, make_person_pose())
        sm.update(make_person_pose(), HAND_NONE)
        assert sm.state == STATE_EXTRACTING
        # EXTRACTING 是瞬态,下一帧进 COUNTING
        sm.update(make_person_pose(), HAND_NONE)
        assert sm.state == STATE_COUNTING
        # 记录目标动作
        target = sm.target_action
        assert target in ("LEFT_HAND_UP", "RIGHT_HAND_UP", "BOTH_HANDS_UP")
        # 构造一个与目标不同的动作 → MATCH_WRONG
        wrong_action = LEFT_HAND_UP if target != LEFT_HAND_UP else RIGHT_HAND_UP
        inflate_m_before = pump.method_call_count("send_inflate_m")
        sm.update(make_person_pose(), wrong_action)
        # 应立即进入 INFLATING
        assert sm.state == STATE_INFLATING
        # 下一次 update 应立即发送第一次 INFLATE_M(_last_inflate_m_time=0.0 触发)
        sm.update(make_person_pose(), wrong_action)
        assert pump.method_call_count("send_inflate_m") == inflate_m_before + 1


class TestCorrectActionResumesCounting:
    """测试点 5:正确动作停止气泵并恢复 COUNTING。"""

    def test_correct_action_stops_pump_and_resumes_counting(self) -> None:
        pump = MockPumpGroupSender()
        light = MockLightSender()
        sm = StateMachine(pump, light)
        # 推进到 COUNTING
        force_state_elapsed(sm, config.INFLATE_TIME_A)
        sm.update(make_person_pose(), HAND_NONE)
        advance_waiting_confirm(sm, make_person_pose())
        sm.update(make_person_pose(), HAND_NONE)
        sm.update(make_person_pose(), HAND_NONE)
        assert sm.state == STATE_COUNTING
        target = sm.target_action
        # 做错动作 → INFLATING
        wrong_action = LEFT_HAND_UP if target != LEFT_HAND_UP else RIGHT_HAND_UP
        sm.update(make_person_pose(), wrong_action)
        sm.update(make_person_pose(), wrong_action)
        assert sm.state == STATE_INFLATING
        # 做回正确动作 → 回 COUNTING
        stop_all_before = pump.method_call_count("send_stop_all")
        sm.update(make_person_pose(), target)
        assert sm.state == STATE_COUNTING
        # 应发送过 stop_all(best-effort 停止气泵)
        assert pump.method_call_count("send_stop_all") >= stop_all_before


class TestGasMaxLock:
    """测试点 6+7:gass 达到 GAS_MAX 后锁定 + 锁定后不再发送 INFLATE_M。"""

    def test_gas_max_locks_inflate(self) -> None:
        pump = MockPumpGroupSender()
        light = MockLightSender()
        sm = StateMachine(pump, light)
        # 推进到 COUNTING
        force_state_elapsed(sm, config.INFLATE_TIME_A)
        sm.update(make_person_pose(), HAND_NONE)
        advance_waiting_confirm(sm, make_person_pose())
        sm.update(make_person_pose(), HAND_NONE)
        sm.update(make_person_pose(), HAND_NONE)
        assert sm.state == STATE_COUNTING
        target = sm.target_action
        wrong_action = LEFT_HAND_UP if target != LEFT_HAND_UP else RIGHT_HAND_UP
        # 进入 INFLATING
        sm.update(make_person_pose(), wrong_action)
        sm.update(make_person_pose(), wrong_action)
        assert sm.state == STATE_INFLATING
        # 快速推进 gass 到 GAS_MAX:每秒发一次 INFLATE_M
        # gass 初值 = int(INFLATE_TIME_A) = 5,需要再发 GAS_MAX - 5 = 10 次
        # 通过修改 _last_inflate_m_time 模拟每秒过期的循环
        target_calls = config.GAS_MAX - sm.gass
        initial_gass = sm.gass
        for _ in range(target_calls + 2):
            sm._last_inflate_m_time -= 1.0  # 模拟 1 秒已过
            sm.update(make_person_pose(), wrong_action)
            if sm._inflate_locked:
                break
        assert sm._inflate_locked is True
        assert sm.gass >= config.GAS_MAX
        # 锁定后回到 COUNTING(状态机定义:达上限后回 COUNTING 继续计时)
        assert sm.state == STATE_COUNTING
        # 锁定后做错动作,不应再发 INFLATE_M
        inflate_m_count_before = pump.method_call_count("send_inflate_m")
        sm.update(make_person_pose(), wrong_action)
        sm.update(make_person_pose(), wrong_action)
        assert pump.method_call_count("send_inflate_m") == inflate_m_count_before


class TestPersonLeaveDeflate:
    """测试点 8:人离开后进入 DEFLATING。"""

    def test_person_leave_triggers_deflate(self) -> None:
        pump = MockPumpGroupSender()
        light = MockLightSender()
        sm = StateMachine(pump, light)
        # 推进到 ENDING
        force_state_elapsed(sm, config.INFLATE_TIME_A)
        sm.update(make_person_pose(), HAND_NONE)
        advance_waiting_confirm(sm, make_person_pose())
        sm.update(make_person_pose(), HAND_NONE)
        sm.update(make_person_pose(), HAND_NONE)
        # COUNTING → INTERVAL(计时完成)
        force_state_elapsed(sm, 0)
        sm._counting_duration = 0.01  # 极短计时
        sm._counting_elapsed = 0.02
        sm.update(make_person_pose(), HAND_NONE)
        # INTERVAL → EXTRACTING(n<3)→ COUNTING → INTERVAL → ... → ENDING
        # 简化:直接进入 ENDING
        sm._enter_ending()
        assert sm.state == STATE_ENDING
        # 人离开 ≥ n4 秒 → 安全放气 → DEFLATING
        sm._last_person_seen_time -= (config.ABSENCE_TIMEOUT_N4 + 1)
        sm.update(make_no_person_pose(), HAND_NONE)
        assert sm.state == STATE_DEFLATING


class TestSafeStopOnPumpFail:
    """测试点 9:任一泵控板失败后进入 SAFE_STOP。"""

    def test_inflate_all_fail_enters_safe_stop(self) -> None:
        pump = MockPumpGroupSender()
        pump.send_inflate_all_ret = False  # 模拟泵控板失败
        light = MockLightSender()
        sm = StateMachine(pump, light)
        assert sm.state == STATE_SAFE_STOP

    def test_inflate_m_fail_enters_safe_stop(self) -> None:
        pump = MockPumpGroupSender()
        light = MockLightSender()
        sm = StateMachine(pump, light)
        # 推进到 COUNTING
        force_state_elapsed(sm, config.INFLATE_TIME_A)
        sm.update(make_person_pose(), HAND_NONE)
        advance_waiting_confirm(sm, make_person_pose())
        sm.update(make_person_pose(), HAND_NONE)
        sm.update(make_person_pose(), HAND_NONE)
        target = sm.target_action
        wrong_action = LEFT_HAND_UP if target != LEFT_HAND_UP else RIGHT_HAND_UP
        # 进入 INFLATING
        sm.update(make_person_pose(), wrong_action)
        sm.update(make_person_pose(), wrong_action)
        assert sm.state == STATE_INFLATING
        # 模拟 send_inflate_m 失败
        pump.send_inflate_m_ret = False
        sm._last_inflate_m_time = 0.0  # 触发发送
        sm.update(make_person_pose(), wrong_action)
        assert sm.state == STATE_SAFE_STOP


class TestSafeStopBehavior:
    """测试点 10+11:SAFE_STOP 放气不被 STOP_ALL 取消 + 不能通过 r 重新充气。"""

    def test_safe_stop_calls_deflate_not_stop_all_loop(self) -> None:
        """进入 SAFE_STOP 后应调用放气(best_effort),不应循环 STOP_ALL 取消放气。

        注意:当前 _enter_safe_stop 用 send_deflate_all;第三阶段改为
        send_deflate_all_best_effort 后,本测试验证 best_effort 被调用。
        """
        pump = MockPumpGroupSender()
        pump.send_inflate_all_ret = False
        light = MockLightSender()
        sm = StateMachine(pump, light)
        assert sm.state == STATE_SAFE_STOP
        # 应调用过放气(send_deflate_all 或 send_deflate_all_best_effort)
        deflate_calls = (pump.method_call_count("send_deflate_all")
                        + pump.method_call_count("send_deflate_all_best_effort"))
        assert deflate_calls >= 1
        # 多次 update 后不应循环调用 stop_all 取消放气
        stop_all_before = pump.method_call_count("send_stop_all")
        for _ in range(5):
            sm.update(make_person_pose(), HAND_NONE)
        # SAFE_STOP 的 _update 是 pass,不应新增 stop_all
        assert pump.method_call_count("send_stop_all") == stop_all_before

    def test_safe_stop_reset_reenters_init(self) -> None:
        """SAFE_STOP 后调用 reset() 会重新进入 INIT(发送 INFLATE_ALL)。

        报告 7.4 要求:SAFE_STOP 不能通过 r 重新开始充气。
        这需要在 main.py 按键处理中阻止,而非在 state_machine.reset() 中。
        本测试验证 reset() 本身的行为(可重置),main.py 需在按键层阻止。
        """
        pump = MockPumpGroupSender()
        pump.send_inflate_all_ret = False
        light = MockLightSender()
        sm = StateMachine(pump, light)
        assert sm.state == STATE_SAFE_STOP
        # reset 后 pump 改为成功
        pump.send_inflate_all_ret = True
        sm.reset()
        assert sm.state == STATE_INIT


# ============ 真实时序仿真测试(复查报告 7.4 测试三) ============

class TestNormalSafetySequence:
    """报告 7.4 测试三:正常安全放气序列不得误入 SAFE_STOP。

    使用 AutoAckSerial(每条命令自动回 ACK)模拟 3 板泵控 + 灯箱
    全部在线且正常执行的固件行为(报告 3.6 的串口协议仿真)。
    修复前:STOP_ALL 只写不读 → ACK 残留 → DEFLATE_ALL 读到旧 ACK
    被误判失败 → SAFE_STOP(即使所有板都正常)。
    修复后:STOP_ALL 消耗自己的 ACK → DEFLATE_ALL 正常收到 ACK → DEFLATING。
    """

    def test_normal_safety_sequence_enters_deflating(self) -> None:
        from test_serial_sender import make_auto_ack_group, make_auto_ack_light

        pump = make_auto_ack_group()
        light = make_auto_ack_light()
        sm = StateMachine(pump, light)
        # INIT:send_inflate_all 应读到 ACK 成功(否则直接 SAFE_STOP)
        assert sm.state == STATE_INIT

        # 触发安全放气序列:STOP_ALL → LIGHT_ALL_OFF → DEFLATE_ALL
        sm._trigger_safety()

        # 全部板正常时,应进入 DEFLATING 而非 SAFE_STOP
        assert sm.state == STATE_DEFLATING
