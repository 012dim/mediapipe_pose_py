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

        共享 deadline 语义(报告 7.3 第三步):PUMP_B 无响应会耗尽
        0.8 秒总时间,PUMP_C 即使正常也来不及读 ACK 而被标记为失败;
        这保证"三板总等待时间 = response_timeout",不会拖到 3×0.8 秒。
        """
        group = make_auto_ack_group()
        # PUMP_B 不回 ACK(替换为普通 FakeSerial,不自动回)
        group.boards["PUMP_B"].serial_conn = FakeSerial()

        results = group.stop_all_best_effort()
        assert results["PUMP_A"] is True    # 先读,B 超时前已成功
        assert results["PUMP_B"] is False   # 无响应,耗尽共享时间
        assert results["PUMP_C"] is False   # 共享 deadline 已过,来不及读
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
