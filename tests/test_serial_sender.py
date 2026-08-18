"""serial_sender 单元测试(v4.2)。

使用 FakeSerial 模拟 Arduino 串口,测试:
- connect(expected_board_id) 读取 READY 并校验板号
- send_and_wait() 读取 ACK/ERR
- PumpGroupSender 三板广播与部分失败
- INFLATE_M_REFRESH 被正确接受

注意:本测试预期第三阶段实现的新接口:
- SerialSender.connect(expected_board_id, ready_timeout) -> bool
- SerialSender.send_and_wait(message, expected_board_id, accepted_commands, response_timeout) -> bool
- PumpGroupSender.send_deflate_all_best_effort(seconds) -> dict

运行:
    pytest tests/test_serial_sender.py -v
"""
import sys
import types

import pytest

from modules.serial_sender import (
    SerialSender,
    PumpSender,
    LightSender,
    PumpGroupSender,
)


# ============ FakeSerial ============

class FakeSerial:
    """模拟 pyserial.Serial,预设响应数据。"""

    def __init__(self, is_open: bool = True) -> None:
        self.is_open: bool = is_open
        self._input_buffer: str = ""
        self._output: list = []

    def write(self, data) -> int:
        self._output.append(data)
        return len(data)

    def readline(self) -> bytes:
        """读取一行(含换行符)。无数据时返回 b""。"""
        if "\n" not in self._input_buffer:
            return b""
        line, self._input_buffer = self._input_buffer.split("\n", 1)
        return (line + "\n").encode("utf-8")

    def reset_input_buffer(self) -> None:
        """no-op:测试中 feed() 的数据代表 Arduino 已发送的响应,应保留可读。

        真实串口中 reset_input_buffer 清除打开前 OS 缓冲区遗留的脏数据;
        FakeSerial 不模拟此行为,feed() 注入的数据始终可读,
        以便测试可在 connect() 之前预填 READY/ACK 响应。
        """
        pass

    def close(self) -> None:
        self.is_open = False

    def feed(self, text: str) -> None:
        """向输入缓冲区添加数据(模拟 Arduino 发送的响应)。"""
        self._input_buffer += text

    @property
    def in_waiting(self) -> int:
        """返回输入缓冲区可读字节数(模拟 pyserial Serial.in_waiting)。

        报告 7.2:公平轮询依赖该属性判断某板是否有数据可读,
        避免无响应的板阻塞已回复的板。
        """
        return len(self._input_buffer.encode("utf-8"))

    @property
    def written_texts(self) -> list:
        """已写入的文本列表(解码)。"""
        result = []
        for data in self._output:
            if isinstance(data, bytes):
                result.append(data.decode("utf-8"))
            else:
                result.append(str(data))
        return result


@pytest.fixture
def fake_serial_module(monkeypatch):
    """注入 fake serial 模块,使 import serial 不报错。"""
    fake_module = types.ModuleType("serial")
    fake_module.Serial = FakeSerial
    monkeypatch.setitem(sys.modules, "serial", fake_module)
    return fake_module


# ============ connect() READY 校验测试 ============

class TestConnectReady:
    """测试 connect(expected_board_id) 读取 READY 并校验板号。"""

    def test_correct_ready(self, fake_serial_module) -> None:
        """收到正确板号的 READY → 连接成功。"""
        sender = PumpSender(port="COM3")
        # 预设 Arduino 发送的 READY
        fake_serial_module.Serial = lambda *a, **kw: FakeSerial()
        # 手动注入:让 connect 使用的 Serial 返回预设好响应的 FakeSerial
        fake = FakeSerial()
        fake.feed("READY,PUMP_A\n")
        fake_serial_module.Serial = lambda *a, **kw: fake

        result = sender.connect(expected_board_id="PUMP_A", ready_timeout=1.0)
        assert result is True
        assert sender.is_connected is True

    def test_board_id_mismatch(self, fake_serial_module) -> None:
        """READY 板号不匹配 → 连接失败。"""
        sender = PumpSender(port="COM3")
        fake = FakeSerial()
        fake.feed("READY,PUMP_B\n")  # 期望 PUMP_A,收到 PUMP_B
        fake_serial_module.Serial = lambda *a, **kw: fake

        result = sender.connect(expected_board_id="PUMP_A", ready_timeout=1.0)
        assert result is False
        assert sender.is_connected is False

    def test_ready_timeout(self, fake_serial_module) -> None:
        """READY 超时(未收到任何数据)→ 连接失败。"""
        sender = PumpSender(port="COM3")
        fake = FakeSerial()
        # 不 feed 任何数据
        fake_serial_module.Serial = lambda *a, **kw: fake

        result = sender.connect(expected_board_id="PUMP_A", ready_timeout=0.2)
        assert result is False
        assert sender.is_connected is False

    def test_ready_with_inflate_m_ms(self, fake_serial_module) -> None:
        """READY 带点充时长参数(READY,PUMP_A,300,500,800)→ 仍正确匹配板号。"""
        sender = PumpSender(port="COM3")
        fake = FakeSerial()
        fake.feed("READY,PUMP_A,300,500,800\n")
        fake_serial_module.Serial = lambda *a, **kw: fake

        result = sender.connect(expected_board_id="PUMP_A", ready_timeout=1.0)
        assert result is True


# ============ send_and_wait() ACK/ERR 测试 ============

class TestSendAndWait:
    """测试 send_and_wait() 读取 ACK/ERR。"""

    def _make_connected_sender(self) -> PumpSender:
        """构造一个已连接的 PumpSender(不经过 connect)。"""
        sender = PumpSender(port="COM3")
        sender._connected = True
        sender.serial_conn = FakeSerial()
        return sender

    def test_ack_success(self) -> None:
        """收到正确板号的 ACK → 返回 True。"""
        sender = self._make_connected_sender()
        sender.serial_conn.feed("ACK,PUMP_A,INFLATE_ALL\n")

        result = sender.send_and_wait(
            "INFLATE_ALL,5.0",
            expected_board_id="PUMP_A",
            accepted_commands={"INFLATE_ALL"},
            response_timeout=0.5,
        )
        assert result is True

    def test_err_failure(self) -> None:
        """收到 ERR → 返回 False。"""
        sender = self._make_connected_sender()
        sender.serial_conn.feed("ERR,PUMP_A,BAD_DURATION\n")

        result = sender.send_and_wait(
            "INFLATE_ALL,abc",
            expected_board_id="PUMP_A",
            accepted_commands={"INFLATE_ALL"},
            response_timeout=0.5,
        )
        assert result is False

    def test_ack_timeout(self) -> None:
        """ACK 超时(未收到响应)→ 返回 False。"""
        sender = self._make_connected_sender()
        # 不 feed 任何数据

        result = sender.send_and_wait(
            "INFLATE_ALL,5.0",
            expected_board_id="PUMP_A",
            accepted_commands={"INFLATE_ALL"},
            response_timeout=0.2,
        )
        assert result is False

    def test_wrong_board_ack_rejected(self) -> None:
        """ACK 板号不匹配 → 返回 False。"""
        sender = self._make_connected_sender()
        sender.serial_conn.feed("ACK,PUMP_B,INFLATE_ALL\n")  # 期望 A,收到 B

        result = sender.send_and_wait(
            "INFLATE_ALL,5.0",
            expected_board_id="PUMP_A",
            accepted_commands={"INFLATE_ALL"},
            response_timeout=0.5,
        )
        assert result is False

    def test_inflate_m_refresh_accepted(self) -> None:
        """INFLATE_M 同时接受 ACK,...,INFLATE_M 和 ACK,...,INFLATE_M_REFRESH。"""
        # 测试 INFLATE_M_REFRESH
        sender = self._make_connected_sender()
        sender.serial_conn.feed("ACK,PUMP_A,INFLATE_M_REFRESH\n")

        result = sender.send_and_wait(
            "INFLATE_M",
            expected_board_id="PUMP_A",
            accepted_commands={"INFLATE_M", "INFLATE_M_REFRESH"},
            response_timeout=0.5,
        )
        assert result is True

        # 测试 INFLATE_M
        sender2 = self._make_connected_sender()
        sender2.serial_conn.feed("ACK,PUMP_A,INFLATE_M\n")

        result2 = sender2.send_and_wait(
            "INFLATE_M",
            expected_board_id="PUMP_A",
            accepted_commands={"INFLATE_M", "INFLATE_M_REFRESH"},
            response_timeout=0.5,
        )
        assert result2 is True

    def test_status_not_treated_as_ack(self) -> None:
        """STATUS 响应不作为普通命令的 ACK。"""
        sender = self._make_connected_sender()
        sender.serial_conn.feed("STATUS,PUMP_A,mode=IDLE,relay=000000,servo=000000\n")

        result = sender.send_and_wait(
            "STOP_ALL",
            expected_board_id="PUMP_A",
            accepted_commands={"STOP_ALL"},
            response_timeout=0.2,
        )
        assert result is False

    def test_not_connected_returns_false(self) -> None:
        """未连接时 send_and_wait 返回 False。"""
        sender = PumpSender(port="COM3")
        sender._connected = False
        result = sender.send_and_wait(
            "STOP_ALL",
            expected_board_id="PUMP_A",
            accepted_commands={"STOP_ALL"},
        )
        assert result is False


# ============ PumpGroupSender 测试 ============

class TestPumpGroupSender:
    """测试 PumpGroupSender 三板广播与部分失败。"""

    def _make_group_with_fakes(self, results_map: dict = None) -> PumpGroupSender:
        """构造 PumpGroupSender,注入 FakeSerial 并按需控制每板成败。

        关键:_send_all_and_collect 内部直接调用 sender._write / sender._read_ack,
        不经过 send_and_wait,因此 mock send_and_wait 无效。这里直接 mock
        _send_all_and_collect 控制广播结果,同时为每板设置 FakeSerial +
        mock send_and_wait(供 send_deflate_all_best_effort 使用)。

        Args:
            results_map: {板ID: True/False} 控制每板返回值。None/缺省表示成功。
        """
        boards_config = [
            {"id": "PUMP_A", "port": "COM3"},
            {"id": "PUMP_B", "port": "COM5"},
            {"id": "PUMP_C", "port": "COM7"},
        ]
        group = PumpGroupSender(boards_config)
        results_map = results_map or {}
        # 每板设置已连接 + FakeSerial(供 stop_all_best_effort 的 send 使用)
        for board_id in group.board_ids:
            sender = group.boards[board_id]
            sender._connected = True
            sender.serial_conn = FakeSerial()
            # mock send_and_wait(供 send_deflate_all_best_effort 使用)
            ret = results_map.get(board_id, True)
            sender.send_and_wait = lambda msg, expected_board_id=board_id, accepted_commands=None, response_timeout=0.8, _ret=ret: _ret
        # mock _send_all_and_collect(供 send_inflate_all/deflate_all/inflate_m 使用)
        def fake_collect(command, accepted_commands, response_timeout=0.8):
            return {bid: results_map.get(bid, True) for bid in group.board_ids}
        group._send_all_and_collect = fake_collect
        return group

    def test_all_three_boards_success(self) -> None:
        """3 板全 ACK → send_inflate_all 返回 True。"""
        group = self._make_group_with_fakes()
        assert group.send_inflate_all(5.0) is True

    def test_partial_failure_returns_false(self) -> None:
        """PUMP_B 失败 → send_inflate_all 返回 False(触发 SAFE_STOP)。"""
        group = self._make_group_with_fakes({
            "PUMP_A": True, "PUMP_B": False, "PUMP_C": True,
        })
        assert group.send_inflate_all(5.0) is False

    def test_inflate_m_partial_failure(self) -> None:
        """INFLATE_M 部分失败 → 返回 False。"""
        group = self._make_group_with_fakes({
            "PUMP_A": True, "PUMP_B": True, "PUMP_C": False,
        })
        assert group.send_inflate_m() is False

    def test_stop_all_best_effort(self) -> None:
        """STOP_ALL best-effort:即使部分板失败也继续尝试,不抛异常。"""
        group = self._make_group_with_fakes()
        # stop_all_best_effort 应不抛异常
        group.stop_all_best_effort()

    def test_send_deflate_all_best_effort_no_stop_all_after(self) -> None:
        """send_deflate_all_best_effort 部分失败后不再广播 STOP_ALL。

        报告 7.2 关键要求:该方法无论部分成功还是部分失败,
        都不得再次广播 STOP_ALL(否则会取消正常板的放气)。
        """
        group = self._make_group_with_fakes({
            "PUMP_A": True, "PUMP_B": False, "PUMP_C": True,
        })
        # 记录 stop_all 调用次数
        stop_all_calls = []
        original_stop_all = group.stop_all_best_effort
        group.stop_all_best_effort = lambda: stop_all_calls.append(1)

        results = group.send_deflate_all_best_effort(5.0)
        # PUMP_B 失败,但 A 和 C 成功
        assert results["PUMP_A"] is True
        assert results["PUMP_B"] is False
        assert results["PUMP_C"] is True
        # 不应调用 stop_all_best_effort(否则会取消 A/C 的放气)
        assert len(stop_all_calls) == 0

    def test_test_mode_skips_send(self) -> None:
        """test_mode=True 时所有发送跳过并返回成功。"""
        boards_config = [{"id": "PUMP_A", "port": "COM3"}]
        group = PumpGroupSender(boards_config, test_mode=True)
        assert group.send_inflate_all(5.0) is True
        assert group.send_inflate_m() is True
        assert group.send_stop_all() is True


# ============ LightSender ACK 测试 ============

class TestLightSenderAck:
    """测试 LightSender 的 ACK 响应。"""

    def _make_connected_light(self) -> LightSender:
        sender = LightSender(port="COM4")
        sender._connected = True
        sender.serial_conn = FakeSerial()
        return sender

    def test_light_on_ack(self) -> None:
        """LIGHT_ON 收到 ACK,LIGHT → 返回 True。"""
        sender = self._make_connected_light()
        sender.serial_conn.feed("ACK,LIGHT,LIGHT_ON\n")
        result = sender.send_and_wait(
            "LIGHT_ON,1",
            expected_board_id="LIGHT",
            accepted_commands={"LIGHT_ON"},
            response_timeout=0.5,
        )
        assert result is True

    def test_light_err_bad_id(self) -> None:
        """非法灯号 → ERR,LIGHT,BAD_LIGHT_ID → 返回 False。"""
        sender = self._make_connected_light()
        sender.serial_conn.feed("ERR,LIGHT,BAD_LIGHT_ID\n")
        result = sender.send_and_wait(
            "LIGHT_ON,9",
            expected_board_id="LIGHT",
            accepted_commands={"LIGHT_ON"},
            response_timeout=0.5,
        )
        assert result is False


# ============ AutoAckSerial:真实时序仿真(报告 3.6/7.4) ============

class AutoAckSerial(FakeSerial):
    """每次 write 后自动回 ACK,<板号>,<命令> 的仿真串口。

    模拟 v4.2 Arduino 固件行为(每条命令执行后立即回 ACK),
    用于测试连续命令时序(如 STOP_ALL → DEFLATE_ALL),
    验证 ACK 不残留、不误读。
    """

    def __init__(self, board_id: str) -> None:
        super().__init__()
        self.board_id: str = board_id

    def write(self, data) -> int:
        n = super().write(data)
        text = data.decode("utf-8") if isinstance(data, bytes) else str(data)
        cmd = text.strip().split(",")[0]
        self.feed(f"ACK,{self.board_id},{cmd}\n")
        return n


def make_auto_ack_group() -> PumpGroupSender:
    """构造 3 板全部自动 ACK 的 PumpGroupSender(真实时序仿真)。"""
    boards_config = [
        {"id": "PUMP_A", "port": "COM3"},
        {"id": "PUMP_B", "port": "COM5"},
        {"id": "PUMP_C", "port": "COM7"},
    ]
    group = PumpGroupSender(boards_config)
    for board_id in group.board_ids:
        sender = group.boards[board_id]
        sender._connected = True
        sender.serial_conn = AutoAckSerial(board_id)
    return group


def make_auto_ack_light() -> LightSender:
    """构造自动 ACK 的 LightSender(真实时序仿真)。"""
    sender = LightSender(port="COM4")
    sender._connected = True
    sender.serial_conn = AutoAckSerial("LIGHT")
    return sender


# ============ 连续命令时序测试(报告 7.4) ============

class TestStaleAckTiming:
    """报告 7.4:旧 STOP_ALL ACK 不得让正常命令被误判失败。"""

    def test_stale_stop_ack_is_skipped_before_deflate(self) -> None:
        """旧 ACK,STOP_ALL 残留时发送 DEFLATE_ALL → 跳过旧 ACK,读新 ACK 成功。"""
        sender = PumpSender(port="COM3")
        sender._connected = True
        sender.serial_conn = FakeSerial()
        # 模拟旧版"只写不读"留下的 STOP_ALL ACK + 本次 DEFLATE_ALL 的 ACK
        sender.serial_conn.feed(
            "ACK,PUMP_A,STOP_ALL\n"
            "ACK,PUMP_A,DEFLATE_ALL\n"
        )

        result = sender.send_and_wait(
            "DEFLATE_ALL,5.0",
            expected_board_id="PUMP_A",
            accepted_commands={"DEFLATE_ALL"},
            response_timeout=0.5,
        )
        assert result is True

    def test_send_stop_all_collects_ack(self) -> None:
        """STOP_ALL 必须消耗自己的 ACK:执行后输入缓冲区应为空。"""
        group = make_auto_ack_group()

        assert group.send_stop_all() is True

        for sender in group.boards.values():
            assert sender.serial_conn.readline() == b""

    def test_stop_then_deflate_all_success(self) -> None:
        """仿真 3.6 场景:STOP_ALL → DEFLATE_ALL 连续命令不误判失败。

        (修复前该序列会被误判为 DEFLATE_ALL 失败 → SAFE_STOP)
        """
        group = make_auto_ack_group()

        assert group.send_stop_all() is True
        assert group.send_deflate_all(5.0) is True

    def test_stop_all_partial_failure_no_recursive_retry(self) -> None:
        """STOP_ALL 部分板失败:不递归重发,返回 False 但不抛异常。

        报告 7.2 公平轮询语义:PUMP_B 无响应时,A/C 的 ACK 仍能在
        共享 deadline 内被读到(旧版顺序读取会让 B 占满 0.8s 导致
        C 被误判失败)。B 超时标记失败,但不影响 A/C。
        """
        group = make_auto_ack_group()
        # PUMP_B 不回 ACK(替换为普通 FakeSerial,不自动回)
        group.boards["PUMP_B"].serial_conn = FakeSerial()

        results = group.stop_all_best_effort()
        assert results["PUMP_A"] is True    # 公平轮询:B 阻塞前已读成功
        assert results["PUMP_B"] is False   # 无响应,共享 deadline 后标记失败
        assert results["PUMP_C"] is True    # 报告 7.2:B 不再阻塞 C
        assert group.send_stop_all() is False

    def test_light_flash_stale_ack_not_pollute_next(self) -> None:
        """报告 8.4:LIGHT_FLASH 迟到 ACK 不污染下一條 LIGHT_ALL_OFF。"""
        light = make_auto_ack_light()
        assert light.send_flash(3) is True
        # 模拟旧版固件闪烁结束后的迟到 ACK 残留
        light.serial_conn.feed("ACK,LIGHT,LIGHT_FLASH\n")

        assert light.send_all_off() is True
        # 且不应有残留
        assert light.serial_conn.readline() == b""


# ============ 公平 ACK 收集测试(报告 7.2) ============

class TestFairAckCollection:
    """报告 7.2:PUMP_A 无响应时 B/C 仍能在共享 deadline 内被读到 ACK。

    旧版顺序读取(A→B→C)会让无响应的 A 占满 0.8s 共享 deadline,
    导致已回复的 B/C 也被误判失败;新版使用公平轮询解决该边界问题。
    """

    def test_a_no_response_bc_succeed_within_deadline(self) -> None:
        """A 无响应、B/C 在 50ms 内返回 ACK → 结果 A=False, B/C=True。"""
        group = make_auto_ack_group()
        # PUMP_A 替换为不自动回 ACK 的 FakeSerial
        group.boards["PUMP_A"].serial_conn = FakeSerial()

        results = group._send_all_and_collect(
            "STOP_ALL",
            accepted_commands={"STOP_ALL"},
            response_timeout=0.8,
        )
        assert results["PUMP_A"] is False
        assert results["PUMP_B"] is True
        assert results["PUMP_C"] is True

    def test_all_respond_success(self) -> None:
        """三板均自动 ACK → 全部成功,无超时。"""
        group = make_auto_ack_group()

        results = group._send_all_and_collect(
            "INFLATE_M",
            accepted_commands={"INFLATE_M", "INFLATE_M_REFRESH"},
            response_timeout=0.8,
        )
        assert results == {"PUMP_A": True, "PUMP_B": True, "PUMP_C": True}

    def test_one_err_marks_only_that_board_failed(self) -> None:
        """B 返回 ERR → 仅 B=False,A/C 仍为 True。"""
        group = make_auto_ack_group()
        # PUMP_B 预填一个 ERR 响应
        group.boards["PUMP_B"].serial_conn = FakeSerial()
        group.boards["PUMP_B"].serial_conn.feed("ERR,PUMP_B,BAD_ARGS\n")

        results = group._send_all_and_collect(
            "INFLATE_ALL,5.0",
            accepted_commands={"INFLATE_ALL"},
            response_timeout=0.8,
        )
        assert results["PUMP_A"] is True
        assert results["PUMP_B"] is False
        assert results["PUMP_C"] is True

    def test_bc_ack_not_left_in_buffer_after_a_timeout(self) -> None:
        """报告 7.2 验收点:B/C 的 ACK 不应遗留缓冲区(被本轮消费干净)。"""
        group = make_auto_ack_group()
        group.boards["PUMP_A"].serial_conn = FakeSerial()

        group._send_all_and_collect(
            "STOP_ALL",
            accepted_commands={"STOP_ALL"},
            response_timeout=0.3,
        )
        # B/C 的 ACK 应已被本轮消费,readline 应返回 b""
        assert group.boards["PUMP_B"].serial_conn.readline() == b""
        assert group.boards["PUMP_C"].serial_conn.readline() == b""


# ============ READY 点充时长校验测试(报告 10.3) ============

class TestReadyParamValidation:
    """报告 10.3:READY 中的每泵点充时长与期望配置比对。"""

    def _connect_with_ready(self, fake_serial_module, ready_line: str,
                            expected_params) -> tuple:
        sender = PumpSender(port="COM3")
        fake = FakeSerial()
        fake.feed(ready_line + "\n")
        fake_serial_module.Serial = lambda *a, **kw: fake
        ok = sender.connect(
            expected_board_id="PUMP_A",
            ready_timeout=1.0,
            expected_ready_params=expected_params,
        )
        return ok, sender

    def test_ready_params_match(self, fake_serial_module) -> None:
        """READY,PUMP_A,300,500,800 与期望一致 → 连接成功。"""
        ok, sender = self._connect_with_ready(
            fake_serial_module, "READY,PUMP_A,300,500,800", [300, 500, 800],
        )
        assert ok is True
        assert sender.is_connected is True

    def test_ready_params_mismatch(self, fake_serial_module) -> None:
        """READY,PUMP_A,300,500,900 与期望 [300,500,800] 不一致 → 拒绝。"""
        ok, sender = self._connect_with_ready(
            fake_serial_module, "READY,PUMP_A,300,500,900", [300, 500, 800],
        )
        assert ok is False
        assert sender.is_connected is False

    def test_ready_board_mismatch_with_params(self, fake_serial_module) -> None:
        """READY,PUMP_B,300,500,800 但端口期望 PUMP_A → 拒绝连接。"""
        sender = PumpSender(port="COM3")
        fake = FakeSerial()
        fake.feed("READY,PUMP_B,300,500,800\n")
        fake_serial_module.Serial = lambda *a, **kw: fake

        ok = sender.connect(
            expected_board_id="PUMP_A",
            ready_timeout=1.0,
            expected_ready_params=[300, 500, 800],
        )
        assert ok is False
        assert sender.is_connected is False

    def test_ready_non_integer_rejected(self, fake_serial_module) -> None:
        """READY,PUMP_A,a,500,800(非整数)→ 拒绝连接。"""
        ok, _ = self._connect_with_ready(
            fake_serial_module, "READY,PUMP_A,a,500,800", [300, 500, 800],
        )
        assert ok is False

    def test_ready_missing_params_rejected(self, fake_serial_module) -> None:
        """READY,PUMP_A 缺少时长参数 → 正式模式(带期望)拒绝连接。"""
        ok, _ = self._connect_with_ready(
            fake_serial_module, "READY,PUMP_A", [300, 500, 800],
        )
        assert ok is False


# ============ 串口异常容错测试(报告 10.1) ============

class BrokenSerial(FakeSerial):
    """模拟 USB 拔出/in_waiting 抛异常的故障串口。

    报告 10.1:真实 USB 串口突然拔出时,in_waiting 属性和 readline() 都可能
    抛 SerialException/OSError。旧版 _send_all_and_collect 没有逐板
    try/except,异常会冒泡中断整个广播方法。
    """

    def __init__(self, fail_on: str = "in_waiting") -> None:
        super().__init__()
        # fail_on: "in_waiting" 或 "readline"
        self.fail_on: str = fail_on

    @property
    def in_waiting(self) -> int:
        if self.fail_on == "in_waiting":
            raise OSError("设备未连接 (模拟 USB 拔出)")
        return super().in_waiting

    def readline(self) -> bytes:
        if self.fail_on == "readline":
            raise OSError("读串口失败 (模拟 USB 拔出)")
        return super().readline()


class TestFairPollingExceptionHandling:
    """报告 10.1:逐板 try/except 捕获串口异常,不影响其他板。

    场景:
    - PUMP_A 串口异常(in_waiting/readline 抛 OSError)
    - PUMP_B/PUMP_C 正常返回 ACK
    - 期望:A=False, B/C=True,方法不向主循环抛异常
    """

    def test_a_in_waiting_exception_bc_succeed(self) -> None:
        """A 的 in_waiting 抛异常 → A=False,B/C 仍能收到 ACK。"""
        group = make_auto_ack_group()
        # PUMP_A 替换为 in_waiting 抛异常的故障串口
        group.boards["PUMP_A"].serial_conn = BrokenSerial(fail_on="in_waiting")

        results = group._send_all_and_collect(
            "STOP_ALL",
            accepted_commands={"STOP_ALL"},
            response_timeout=0.5,
        )
        assert results["PUMP_A"] is False
        assert results["PUMP_B"] is True
        assert results["PUMP_C"] is True

    def test_a_readline_exception_bc_succeed(self) -> None:
        """A 的 readline 抛异常 → A=False,B/C 仍能收到 ACK。"""
        group = make_auto_ack_group()
        # PUMP_A 的 in_waiting 返回数据但 readline 抛异常
        broken = BrokenSerial(fail_on="readline")
        # AutoAckSerial 在 write 时已 feed ACK,但 readline 会抛异常
        # 这里手动注入 ACK 到输入缓冲
        broken.feed("ACK,PUMP_A,STOP_ALL\n")
        # 替换 PUMP_A 的串口,write 走 BrokenSerial 父类(正常 feed ACK)
        # 但 readline 抛异常
        group.boards["PUMP_A"].serial_conn = broken

        # 触发 write 后 broken 应已自动 feed ACK(经父类 write)
        # 但 readline 会抛异常 → A 应被标记失败
        results = group._send_all_and_collect(
            "STOP_ALL",
            accepted_commands={"STOP_ALL"},
            response_timeout=0.5,
        )
        assert results["PUMP_A"] is False
        assert results["PUMP_B"] is True
        assert results["PUMP_C"] is True

    def test_all_boards_exception_returns_all_false(self) -> None:
        """三板 in_waiting 都抛异常 → 全部 False,方法不抛异常。"""
        group = make_auto_ack_group()
        for board_id in group.board_ids:
            group.boards[board_id].serial_conn = BrokenSerial(fail_on="in_waiting")

        results = group._send_all_and_collect(
            "STOP_ALL",
            accepted_commands={"STOP_ALL"},
            response_timeout=0.3,
        )
        # 全部失败,但方法不抛异常
        assert results == {"PUMP_A": False, "PUMP_B": False, "PUMP_C": False}

    def test_exception_marks_sender_disconnected(self) -> None:
        """串口异常后 sender._connected 应被标记为 False,后续不再尝试。"""
        group = make_auto_ack_group()
        group.boards["PUMP_A"].serial_conn = BrokenSerial(fail_on="in_waiting")

        group._send_all_and_collect(
            "STOP_ALL",
            accepted_commands={"STOP_ALL"},
            response_timeout=0.3,
        )
        assert group.boards["PUMP_A"].is_connected is False
        # B/C 仍连接正常
        assert group.boards["PUMP_B"].is_connected is True
        assert group.boards["PUMP_C"].is_connected is True


# ============ 灯箱 _read_ack 异常容错测试(报告 9.4) ============

class BrokenReadlineSerial(FakeSerial):
    """模拟 readline() 抛 OSError 的故障串口(报告 9.1)。

    报告 9.1:灯箱 USB 拔出时 readline() 会抛 OSError,
    旧版 _read_ack() 未捕获导致异常冒泡主循环退出。
    """

    def __init__(self) -> None:
        super().__init__()

    def readline(self) -> bytes:
        raise OSError("simulated USB unplug")


class TestLightReadAckException:
    """报告 9.4:灯箱 _read_ack 捕获 OSError 后应:

    1. 返回 False(不抛异常)
    2. 标记 sender._connected = False
    3. 关闭并清理 serial_conn(不泄漏句柄)
    4. 后续调用不再尝试访问已断开串口
    """

    def _make_light_with_broken_read(self) -> LightSender:
        light = LightSender(port="COM4")
        light._connected = True
        light.serial_conn = BrokenReadlineSerial()
        return light

    def test_light_read_exception_returns_false(self) -> None:
        """灯箱 send_light_on 的 readline 抛 OSError → 返回 False(不抛异常)。"""
        light = self._make_light_with_broken_read()
        # send_light_on 内部走 send_and_wait → _read_ack,
        # readline 抛 OSError 应被捕获,返回 False
        result = light.send_light_on(1)
        assert result is False

    def test_light_read_exception_marks_disconnected(self) -> None:
        """异常后 sender.is_connected 应为 False。"""
        light = self._make_light_with_broken_read()
        light.send_light_on(1)
        assert light.is_connected is False

    def test_light_read_exception_clears_serial_conn(self) -> None:
        """异常后 serial_conn 应被置 None,防止后续误用已关闭句柄。"""
        light = self._make_light_with_broken_read()
        light.send_light_on(1)
        assert light.serial_conn is None

    def test_light_subsequent_call_after_disconnect_returns_false(self) -> None:
        """断开后再次调用应直接返回 False,不访问 serial_conn。"""
        light = self._make_light_with_broken_read()
        # 第一次调用触发异常并标记断开
        assert light.send_light_on(1) is False
        # 第二次调用应直接走"未连接"分支,不抛异常
        assert light.send_light_off(1) is False
        assert light.send_all_off() is False
        assert light.send_flash(3) is False

    def test_light_off_exception_returns_false(self) -> None:
        """send_light_off 也能正确处理 readline 异常。"""
        light = self._make_light_with_broken_read()
        assert light.send_light_off(1) is False
        assert light.is_connected is False

    def test_light_all_off_exception_returns_false(self) -> None:
        """send_all_off 也能正确处理 readline 异常。"""
        light = self._make_light_with_broken_read()
        assert light.send_all_off() is False
        assert light.is_connected is False

    def test_light_flash_exception_returns_false(self) -> None:
        """send_flash 也能正确处理 readline 异常。"""
        light = self._make_light_with_broken_read()
        assert light.send_flash(3) is False
        assert light.is_connected is False


# ============ PumpSender / PumpGroupSender HOLD_ALL 测试(v4.4) ============

class TestHoldAll:
    """v4.4 新增:HOLD_ALL 命令对应固件 holdPressure()(报告 7.4)。

    验证:
    - 单板 send_hold_all() 发送 HOLD_ALL 并等待 ACK
    - 三板广播 send_hold_all() 部分失败时触发 STOP_ALL + 返回 False
    - 三板广播全部成功时返回 True
    """

    def test_single_board_hold_all_success(self) -> None:
        """单板 HOLD_ALL 收到 ACK → 返回 True。"""
        sender = PumpSender(port="COM3")
        sender._connected = True
        sender.serial_conn = AutoAckSerial("PUMP_A")

        result = sender.send_hold_all()
        assert result is True
        # 验证发送的命令文本
        sent = b"".join(sender.serial_conn._output).decode("utf-8")
        assert "HOLD_ALL\n" in sent

    def test_single_board_hold_all_err(self) -> None:
        """单板 HOLD_ALL 收到 ERR → 返回 False。"""
        sender = PumpSender(port="COM3")
        sender._connected = True
        sender.serial_conn = FakeSerial()
        sender.serial_conn.feed("ERR,PUMP_A,NOT_ARMED\n")

        result = sender.send_hold_all()
        assert result is False

    def test_group_hold_all_all_success(self) -> None:
        """三板广播 HOLD_ALL 全部 ACK → 返回 True。"""
        group = make_auto_ack_group()
        assert group.send_hold_all() is True

    def test_group_hold_all_partial_failure_triggers_stop_all(self) -> None:
        """PUMP_B 失败 → send_hold_all 返回 False 并触发 STOP_ALL。"""
        group = make_auto_ack_group()
        # PUMP_B 替换为不自动回 ACK 的 FakeSerial
        group.boards["PUMP_B"].serial_conn = FakeSerial()

        # 拦截 stop_all_best_effort 调用计数
        stop_calls = []
        original_stop_all = group.stop_all_best_effort
        group.stop_all_best_effort = lambda: stop_calls.append(1) or original_stop_all()

        result = group.send_hold_all()
        assert result is False
        # 应触发 STOP_ALL 兜底
        assert len(stop_calls) == 1

    def test_group_hold_all_test_mode_skips_send(self) -> None:
        """test_mode=True 时 HOLD_ALL 跳过发送并返回成功。"""
        boards_config = [{"id": "PUMP_A", "port": "COM3"}]
        group = PumpGroupSender(boards_config, test_mode=True)
        assert group.send_hold_all() is True
