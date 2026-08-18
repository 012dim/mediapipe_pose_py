"""串口发送模块:管理 4 块 Arduino UNO(3 泵控 + 1 灯箱)的串口通信。

v4.2 新增:
- connect(expected_board_id) 读取 READY 并校验板号
- send_and_wait() 读取 ACK/ERR 响应
- PumpGroupSender 三板先写后收集(不串行等待)
- send_deflate_all_best_effort() 仅供 SAFE_STOP,部分失败不再 STOP_ALL

当 SERIAL_ENABLED = False(test_mode)时,所有方法跳过实际发送并返回成功,
状态机可正常流转。
"""
import logging
import threading
import time
from typing import Optional, Set

logger = logging.getLogger(__name__)


class SerialSender:
    """串口发送封装类,线程安全,失败容错。

    Attributes:
        port: 串口端口名。
        baudrate: 波特率。
        timeout: 读超时(秒)。
        write_timeout: 写超时(秒,防卡死)。
        board_id: 本串口对应的板号(如 PUMP_A / LIGHT),用于校验。
    """

    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        timeout: float = 1.0,
        write_timeout: float = 0.5,
        board_id: str = "",
    ) -> None:
        self.port: str = port
        self.baudrate: int = baudrate
        self.timeout: float = timeout
        self.write_timeout: float = write_timeout
        self.board_id: str = board_id
        self.serial_conn = None
        self._lock = threading.Lock()
        self._connected: bool = False

    def connect(
        self,
        expected_board_id: str = "",
        ready_timeout: float = 3.0,
    ) -> bool:
        """打开串口,读取 READY 并校验板号。

        流程:
        1. 打开 COM 口
        2. 清除打开前遗留的输入数据
        3. 等待 Arduino 复位并读取 READY
        4. 校验板号(若 expected_board_id 非空)
        5. 超时或板号错误则关闭串口并返回失败

        Args:
            expected_board_id: 期望的板号(如 PUMP_A / LIGHT)。空字符串表示不校验。
            ready_timeout: 等待 READY 的超时秒数。

        Returns:
            bool: 成功连接且板号匹配返回 True,失败返回 False。
        """
        try:
            import serial  # type: ignore
        except ImportError:
            logger.warning(
                "未安装 pyserial,串口未连接,继续运行。"
                " 可执行 pip install pyserial 安装。"
            )
            self._connected = False
            return False

        try:
            self.serial_conn = serial.Serial(
                self.port,
                self.baudrate,
                timeout=0.1,
                write_timeout=self.write_timeout,
            )
            # 清除打开前遗留的输入数据
            self.serial_conn.reset_input_buffer()

            # 等待 Arduino 复位并读取 READY
            deadline = time.monotonic() + ready_timeout
            while time.monotonic() < deadline:
                line = self.serial_conn.readline()
                if not line:
                    continue
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                parts = text.split(",")
                if len(parts) >= 2 and parts[0] == "READY":
                    got_board = parts[1]
                    if expected_board_id and got_board != expected_board_id:
                        logger.error(
                            "串口 %s 板号不匹配:期望 %s,收到 %s",
                            self.port, expected_board_id, got_board,
                        )
                        self.close()
                        return False
                    self._connected = True
                    # 记录 READY 中的点充时长参数(如有)
                    if len(parts) > 2:
                        logger.info(
                            "串口 %s 已连接 %s,点充时长=%s",
                            self.port, got_board, ",".join(parts[2:]),
                        )
                    else:
                        logger.info("串口 %s 已连接 %s", self.port, got_board)
                    return True

            # 超时未收到 READY
            logger.warning("串口 %s 等待 READY 超时", self.port)
            self.close()
            return False
        except Exception as e:  # noqa: BLE001
            logger.warning("串口 %s 打开失败: %s", self.port, e)
            self._connected = False
            self.serial_conn = None
            return False

    def _write(self, message: str) -> bool:
        """写入一行文本到串口(内部方法,不加锁)。"""
        if not self._connected or self.serial_conn is None:
            return False
        data = (message + "\n").encode("utf-8")
        try:
            self.serial_conn.write(data)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("串口 %s 发送失败: %s", self.port, e)
            self._connected = False
            return False

    def _read_ack(
        self,
        expected_board_id: str,
        accepted_commands: Set[str],
        timeout: float = 0.8,
    ) -> bool:
        """读取并解析 ACK/ERR 响应(内部方法)。

        响应格式:
            ACK,<板号>,<命令> → 成功(命令须在 accepted_commands 中)
            ERR,<板号>,<原因> → 失败
            READY,<板号>      → 忽略(仅连接阶段)
            STATUS,<板号>,... → 忽略(状态查询结果,不作 ACK)

        Args:
            expected_board_id: 期望的板号。
            accepted_commands: 接受的命令集合(如 {"INFLATE_ALL", "INFLATE_M_REFRESH"})。
            timeout: 读取超时秒数。

        Returns:
            bool: 收到正确板号的 ACK 返回 True,ERR/板号错/超时返回 False。
        """
        if not self._connected or self.serial_conn is None:
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.serial_conn.readline()
            if not line:
                continue
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            parts = text.split(",")
            if len(parts) < 2:
                continue
            msg_type = parts[0]
            board = parts[1]

            if msg_type == "ACK":
                if expected_board_id and board != expected_board_id:
                    logger.warning(
                        "串口 %s ACK 板号不匹配:期望 %s,收到 %s",
                        self.port, expected_board_id, board,
                    )
                    return False
                cmd = parts[2] if len(parts) > 2 else ""
                if cmd not in accepted_commands:
                    logger.warning(
                        "串口 %s ACK 命令不在接受列表: %s(接受: %s)",
                        self.port, cmd, accepted_commands,
                    )
                    return False
                return True
            elif msg_type == "ERR":
                reason = parts[2] if len(parts) > 2 else "UNKNOWN"
                logger.warning(
                    "串口 %s 收到 ERR: %s,%s",
                    self.port, board, reason,
                )
                return False
            elif msg_type in ("READY", "STATUS"):
                # READY/STATUS 不是 ACK,继续读取
                continue
            else:
                logger.debug("串口 %s 收到未知响应: %s", self.port, text)
                continue
        logger.warning("串口 %s 等待 ACK 超时", self.port)
        return False

    def send(self, message: str) -> bool:
        """只写入一行文本到串口,不等待响应(best-effort 场景)。

        Args:
            message: 要发送的文本内容(不含换行符)。

        Returns:
            bool: 写入成功返回 True,失败或未连接返回 False。
        """
        if not self._connected or self.serial_conn is None:
            return False
        with self._lock:
            return self._write(message)

    def send_and_wait(
        self,
        message: str,
        expected_board_id: str,
        accepted_commands: Set[str],
        response_timeout: float = 0.8,
    ) -> bool:
        """发送命令并等待 ACK/ERR 响应。

        Args:
            message: 命令文本(不含换行符)。
            expected_board_id: 期望的板号。
            accepted_commands: 接受的命令集合。
            response_timeout: 响应超时秒数。

        Returns:
            bool: 收到正确板号的 ACK 返回 True,ERR/板号错/超时返回 False。
        """
        if not self._connected or self.serial_conn is None:
            return False
        with self._lock:
            if not self._write(message):
                return False
            return self._read_ack(expected_board_id, accepted_commands, response_timeout)

    def close(self) -> None:
        """关闭串口连接。"""
        with self._lock:
            if self.serial_conn is not None:
                try:
                    self.serial_conn.close()
                except Exception as e:  # noqa: BLE001
                    logger.warning("关闭串口异常: %s", e)
                finally:
                    self.serial_conn = None
                    self._connected = False
                    logger.info("串口 %s 已关闭", self.port)

    @property
    def is_connected(self) -> bool:
        """返回串口是否已连接。"""
        return self._connected


# ============ 气泵串口(泵控 UNO) ============
class PumpSender(SerialSender):
    """单块泵控 UNO 串口(3 气泵 + 3 电磁阀)。

    协议(每行一条,以 \\n 结尾):
        INFLATE_ALL,a       全部 3 泵充气 a 秒
        DEFLATE_ALL,b       全部 3 阀打开放气 b 秒
        INFLATE_M           9 泵同步点充(每泵独立时长)
        STOP_ALL            立即停止全部 6 设备
        STATUS              查询当前板状态
        TEST_PUMP,i,t      测试第 i 号泵,持续 t 秒
    """

    def __init__(self, port: str, board_id: str = "", **kwargs) -> None:
        super().__init__(port, board_id=board_id, **kwargs)

    def send_inflate_all(self, seconds: float) -> bool:
        """全部 3 泵充气指定秒数(等待 ACK)。"""
        return self.send_and_wait(
            f"INFLATE_ALL,{seconds}",
            expected_board_id=self.board_id,
            accepted_commands={"INFLATE_ALL"},
        )

    def send_deflate_all(self, seconds: float) -> bool:
        """全部 3 阀打开放气指定秒数(等待 ACK)。"""
        return self.send_and_wait(
            f"DEFLATE_ALL,{seconds}",
            expected_board_id=self.board_id,
            accepted_commands={"DEFLATE_ALL"},
        )

    def send_inflate_m(self) -> bool:
        """9 泵同步点充(等待 ACK,同时接受 INFLATE_M 和 INFLATE_M_REFRESH)。"""
        return self.send_and_wait(
            "INFLATE_M",
            expected_board_id=self.board_id,
            accepted_commands={"INFLATE_M", "INFLATE_M_REFRESH"},
        )

    def send_stop_all(self) -> bool:
        """立即停止全部 6 设备(best-effort,不等待 ACK)。"""
        return self.send("STOP_ALL")


# ============ 灯箱串口 ============
class LightSender(SerialSender):
    """灯箱控制串口(3 路继电器控制 3 个灯泡)。

    协议(每行一条,以 \\n 结尾):
        LIGHT_ON,id         点亮指定编号灯泡(id ∈ {1,2,3})
        LIGHT_OFF,id        熄灭指定编号灯泡
        LIGHT_ALL_OFF       全部熄灭
        LIGHT_FLASH,3       三灯同时闪烁 3 次(用于 ENDING)
    """

    ACTION_TO_LIGHT_ID = {
        "LEFT_HAND_UP": 1,
        "RIGHT_HAND_UP": 2,
        "BOTH_HANDS_UP": 3,
    }

    def __init__(self, port: str, board_id: str = "LIGHT", **kwargs) -> None:
        super().__init__(port, board_id=board_id, **kwargs)

    def send_light_on(self, light_id: int) -> bool:
        """点亮指定编号灯泡(等待 ACK)。"""
        return self.send_and_wait(
            f"LIGHT_ON,{light_id}",
            expected_board_id=self.board_id,
            accepted_commands={"LIGHT_ON"},
        )

    def send_light_off(self, light_id: int) -> bool:
        """熄灭指定编号灯泡(等待 ACK)。"""
        return self.send_and_wait(
            f"LIGHT_OFF,{light_id}",
            expected_board_id=self.board_id,
            accepted_commands={"LIGHT_OFF"},
        )

    def send_all_off(self) -> bool:
        """全部灯泡熄灭(等待 ACK)。"""
        return self.send_and_wait(
            "LIGHT_ALL_OFF",
            expected_board_id=self.board_id,
            accepted_commands={"LIGHT_ALL_OFF"},
        )

    def send_flash(self, times: int = 3) -> bool:
        """三灯同时闪烁若干次(等待 ACK)。"""
        return self.send_and_wait(
            f"LIGHT_FLASH,{times}",
            expected_board_id=self.board_id,
            accepted_commands={"LIGHT_FLASH"},
        )

    def light_id_for_action(self, action_name: str) -> Optional[int]:
        """根据手部动作名返回对应灯泡编号。"""
        return self.ACTION_TO_LIGHT_ID.get(action_name)


# ============ 泵组控制(3 板 UNO 联机)============
class PumpGroupSender:
    """泵组发送器:管理 3 块泵控 UNO(PUMP_A/B/C),统一广播指令。

    - 普通命令(INFLATE_ALL/DEFLATE_ALL/INFLATE_M):先向 3 板写入,再统一收集 ACK/ERR。
      任一板失败 → 触发 stop_all_best_effort() 并返回 False,状态机进入 SAFE_STOP。
    - STOP_ALL 采用 best-effort:即使某板异常也继续尝试其他板。
    - send_deflate_all_best_effort():仅供 SAFE_STOP 使用,部分失败不再 STOP_ALL。
    - test_mode=True(SERIAL_ENABLED=False):跳过实际发送,所有方法返回成功。
    """

    def __init__(
        self,
        boards_config: list,
        baudrate: int = 9600,
        timeout: float = 1.0,
        write_timeout: float = 0.5,
        test_mode: bool = False,
    ) -> None:
        self.boards: dict = {}
        self.board_ids: list = []
        self.test_mode: bool = test_mode
        for cfg in boards_config:
            board_id = cfg['id']
            port = cfg['port']
            sender = PumpSender(
                port=port,
                baudrate=baudrate,
                timeout=timeout,
                write_timeout=write_timeout,
                board_id=board_id,
            )
            self.boards[board_id] = sender
            self.board_ids.append(board_id)

    def connect_all(self) -> bool:
        """连接所有泵控板并校验板号。

        test_mode=True 时直接返回 True,不实际连接串口。

        Returns:
            bool: 全部 3 板连接成功且板号匹配返回 True;任一失败返回 False。
        """
        if self.test_mode:
            logger.info("[PUMP_GROUP] TEST_MODE: 跳过 3 板串口连接")
            return True
        all_ok = True
        for board_id in self.board_ids:
            ok = self.boards[board_id].connect(expected_board_id=board_id)
            if not ok:
                logger.error("泵控板 %s 连接失败", board_id)
                all_ok = False
        if all_ok:
            logger.info("3 块泵控 UNO 全部连接成功: %s", ", ".join(self.board_ids))
        else:
            connected = self.get_connected_board_ids()
            if connected:
                logger.warning("部分泵控板连接成功: %s", ", ".join(connected))
        return all_ok

    def get_connected_board_ids(self) -> list:
        """返回当前已连接的板ID列表。"""
        if self.test_mode:
            return list(self.board_ids)
        return [bid for bid in self.board_ids if self.boards[bid].is_connected]

    @property
    def all_connected(self) -> bool:
        """是否全部 3 板都已连接。"""
        if self.test_mode:
            return True
        return all(self.boards[bid].is_connected for bid in self.board_ids)

    def _send_all_and_collect(
        self,
        command: str,
        accepted_commands: Set[str],
        response_timeout: float = 0.8,
    ) -> dict:
        """先向 3 板写入命令,再统一收集 ACK/ERR(不串行等待)。

        报告 6.3:为了保持 3 板同步,先连续写入,再在统一超时内收集。
        不采用"等待 A 完整回复后才给 B 发送"的串行结构。

        Args:
            command: 命令文本(不含换行符)。
            accepted_commands: 接受的命令集合。
            response_timeout: 每板响应超时秒数。

        Returns:
            dict[板ID, bool]: 每板发送+ACK 结果。
        """
        if self.test_mode:
            logger.info("[PUMP_GROUP] TEST_MODE: %s (3 板跳过)", command)
            return {bid: True for bid in self.board_ids}

        results: dict = {}
        # 1. 先连续向 3 板写入命令(不等待响应)
        for board_id in self.board_ids:
            sender = self.boards[board_id]
            ok = sender._write(command)
            if not ok:
                results[board_id] = False
            else:
                results[board_id] = None  # 待收集

        # 2. 再逐板收集 ACK/ERR
        for board_id in self.board_ids:
            if results[board_id] is not None:
                continue  # 写入已失败,跳过
            sender = self.boards[board_id]
            ok = sender._read_ack(board_id, accepted_commands, response_timeout)
            results[board_id] = ok
            logger.info("[PUMP_GROUP] %s <- %s : %s",
                        board_id, command, "OK" if ok else "FAIL")
        return results

    def _check_all_ok(self, results: dict) -> bool:
        """检查广播结果是否全部成功。"""
        return all(results.values())

    def stop_all_best_effort(self) -> None:
        """best-effort 广播 STOP_ALL(不等待 ACK)。"""
        if self.test_mode:
            logger.info("[PUMP_GROUP] TEST_MODE: STOP_ALL (3 板跳过)")
            return
        for board_id in self.board_ids:
            sender = self.boards[board_id]
            try:
                ok = sender.send("STOP_ALL")
                logger.info("[PUMP_GROUP] %s <- STOP_ALL : %s",
                            board_id, "OK" if ok else "FAIL")
            except Exception as e:  # noqa: BLE001
                logger.error("[PUMP_GROUP] %s STOP_ALL 异常: %s", board_id, e)

    def send_inflate_all(self, seconds: float) -> bool:
        """广播 INFLATE_ALL,seconds 秒。任一板失败 → STOP_ALL + 返回 False。"""
        results = self._send_all_and_collect(
            f"INFLATE_ALL,{seconds}",
            accepted_commands={"INFLATE_ALL"},
        )
        if not self._check_all_ok(results):
            logger.error("[PUMP_GROUP] INFLATE_ALL 部分失败,触发 STOP_ALL: %s", results)
            self.stop_all_best_effort()
            return False
        return True

    def send_deflate_all(self, seconds: float) -> bool:
        """广播 DEFLATE_ALL,seconds 秒。任一板失败 → STOP_ALL + 返回 False。"""
        results = self._send_all_and_collect(
            f"DEFLATE_ALL,{seconds}",
            accepted_commands={"DEFLATE_ALL"},
        )
        if not self._check_all_ok(results):
            logger.error("[PUMP_GROUP] DEFLATE_ALL 部分失败,触发 STOP_ALL: %s", results)
            self.stop_all_best_effort()
            return False
        return True

    def send_inflate_m(self) -> bool:
        """广播 INFLATE_M(每秒一次的惩罚充气,9 泵同步)。任一板失败 → STOP_ALL + 返回 False。"""
        results = self._send_all_and_collect(
            "INFLATE_M",
            accepted_commands={"INFLATE_M", "INFLATE_M_REFRESH"},
        )
        if not self._check_all_ok(results):
            logger.error("[PUMP_GROUP] INFLATE_M 部分失败,触发 STOP_ALL: %s", results)
            self.stop_all_best_effort()
            return False
        return True

    def send_stop_all(self) -> bool:
        """广播 STOP_ALL(best-effort,不等待 ACK)。"""
        if self.test_mode:
            logger.info("[PUMP_GROUP] TEST_MODE: STOP_ALL (3 板跳过)")
            return True
        results = {}
        for board_id in self.board_ids:
            sender = self.boards[board_id]
            try:
                ok = sender.send("STOP_ALL")
                results[board_id] = ok
            except Exception as e:  # noqa: BLE001
                logger.error("[PUMP_GROUP] %s STOP_ALL 异常: %s", board_id, e)
                results[board_id] = False
        return self._check_all_ok(results)

    def send_deflate_all_best_effort(self, seconds: float) -> dict:
        """仅供 SAFE_STOP 使用的放气方法。

        报告 7.2 关键要求:
        - 逐板发送 DEFLATE_ALL 并等待 ACK
        - 无论部分成功还是部分失败,都不得再次广播 STOP_ALL
        - 在线的正常板应让电磁阀持续打开指定时间
        - 失联板依靠 Arduino 本地定时停止

        Args:
            seconds: 放气秒数。

        Returns:
            dict[板ID, bool]: 每板发送结果。
        """
        if self.test_mode:
            logger.info("[PUMP_GROUP] TEST_MODE: DEFLATE_ALL,%s (3 板跳过)", seconds)
            return {bid: True for bid in self.board_ids}
        results: dict = {}
        for board_id in self.board_ids:
            sender = self.boards[board_id]
            try:
                ok = sender.send_and_wait(
                    f"DEFLATE_ALL,{seconds}",
                    expected_board_id=board_id,
                    accepted_commands={"DEFLATE_ALL"},
                )
                results[board_id] = ok
                logger.info("[PUMP_GROUP] %s <- DEFLATE_ALL,%s : %s",
                            board_id, seconds, "OK" if ok else "FAIL")
            except Exception as e:  # noqa: BLE001
                logger.error("[PUMP_GROUP] %s DEFLATE_ALL 异常: %s", board_id, e)
                results[board_id] = False
        # 关键:不再调用 stop_all_best_effort(否则会取消正常板的放气)
        return results

    def close_all(self) -> None:
        """关闭所有泵控板串口。"""
        for board_id in self.board_ids:
            try:
                self.boards[board_id].close()
            except Exception as e:  # noqa: BLE001
                logger.error("[PUMP_GROUP] %s 关闭异常: %s", board_id, e)
