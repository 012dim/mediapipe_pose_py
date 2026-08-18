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
