"""串口发送模块:异步把动作事件通过串口发送给 Arduino。

当 SERIAL_ENABLED = True 时启用。串口打开失败不会导致程序崩溃,
仅打印警告继续运行。
"""
import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class SerialSender:
    """串口发送封装类,线程安全,失败容错。

    串口通信协议:每行一个事件,以 \\n 结尾,格式 POSE,动作名。
    例如:POSE,BOTH_HANDS_UP\\n

    Attributes:
        port: 串口端口名。
        baudrate: 波特率。
        timeout: 读超时(秒)。
    """

    def __init__(
        self,
        port: str,
        baudrate: int = 9600,
        timeout: float = 1.0,
        write_timeout: float = 0.5,
    ) -> None:
        """初始化串口参数。

        Args:
            port: 串口端口名,如 'COM3' 或 '/dev/ttyUSB0'。
            baudrate: 波特率,默认 9600。
            timeout: 读超时秒数,默认 1.0。
            write_timeout: 写超时秒数,默认 0.5。防止对端不消费数据时
                write/flush 永久阻塞导致主循环卡死。
        """
        self.port: str = port
        self.baudrate: int = baudrate
        self.timeout: float = timeout
        self.write_timeout: float = write_timeout
        self.serial_conn = None
        self._lock = threading.Lock()
        self._connected: bool = False

    def connect(self) -> bool:
        """打开串口连接。

        pyserial 未安装或端口不存在时不会抛异常,仅打印警告。

        Returns:
            bool: 成功连接返回 True,失败返回 False。
        """
        try:
            import serial  # type: ignore
        except ImportError:
            logger.warning(
                "未安装 pyserial,串口未连接,继续运行(动作识别正常,仅不发送)。"
                " 可执行 pip install pyserial 安装。"
            )
            self._connected = False
            return False

        try:
            self.serial_conn = serial.Serial(
                self.port,
                self.baudrate,
                timeout=self.timeout,
                write_timeout=self.write_timeout,
            )
            # 等待串口稳定(部分 Arduino 板复位需要时间)
            time.sleep(0.5)
            self._connected = self.serial_conn.is_open
            if self._connected:
                logger.info("串口 %s 已连接 @ %d baud", self.port, self.baudrate)
            else:
                logger.warning("串口 %s 打开失败", self.port)
            return self._connected
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "串口 %s 打开失败: %s,继续运行(动作识别正常,仅不发送)。",
                self.port, e,
            )
            self._connected = False
            self.serial_conn = None
            return False

    def send(self, message: str) -> bool:
        """发送一行文本到串口(自动加换行符)。

        Args:
            message: 要发送的文本内容(不含换行符)。

        Returns:
            bool: 发送成功返回 True,失败或未连接返回 False。
        """
        if not self._connected or self.serial_conn is None:
            return False
        data = (message + "\n").encode("utf-8")
        with self._lock:
            try:
                # write 会把数据写入 OS 串口发送缓冲区,由硬件异步发送。
                # 不调用 flush():Windows 上 FlushFileBuffers 会等待对端接收,
                # 若对端不消费数据会永久阻塞,导致主循环卡死。
                # write_timeout 保证 write 本身不会永久阻塞。
                self.serial_conn.write(data)
                return True
            except Exception as e:  # noqa: BLE001
                logger.warning("串口发送失败: %s", e)
                self._connected = False
                return False

    def send_action(self, action_name: str) -> bool:
        """按协议发送动作事件。

        协议格式: POSE,动作名\\n

        Args:
            action_name: 动作事件名,如 BOTH_HANDS_UP。

        Returns:
            bool: 发送成功返回 True。
        """
        return self.send(f"POSE,{action_name}")

    def send_hand_action(self, hand_action: str) -> bool:
        """发送手部动作状态。

        协议格式: POSE_HAND,动作名\\n

        Args:
            hand_action: 手部动作名,如 BOTH_HANDS_UP / HAND_NONE。

        Returns:
            bool: 发送成功返回 True。
        """
        return self.send(f"POSE_HAND,{hand_action}")

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


# ============ 气泵串口(Uno-A) ============
class PumpSender(SerialSender):
    """气泵控制串口(Uno-A,3 气泵 / 6 路继电器)。

    协议(每行一条,以 \\n 结尾):
        INFLATE_ALL,a       全部气泵充气 a 秒
        DEFLATE_ALL,b       全部气泵放气 b 秒
        INFLATE_M           主气泵点充一次(用于 INFLATING 阶段每秒一次)
        STOP_ALL            立即停止所有气泵
    """

    def send_inflate_all(self, seconds: float) -> bool:
        """全部气泵充气指定秒数。

        Args:
            seconds: 充气时长(秒)。

        Returns:
            bool: 发送成功返回 True。
        """
        return self.send(f"INFLATE_ALL,{seconds}")

    def send_deflate_all(self, seconds: float) -> bool:
        """全部气泵放气指定秒数。

        Args:
            seconds: 放气时长(秒)。

        Returns:
            bool: 发送成功返回 True。
        """
        return self.send(f"DEFLATE_ALL,{seconds}")

    def send_inflate_m(self) -> bool:
        """主气泵点充一次(INFLATING 阶段每秒一次)。"""
        return self.send("INFLATE_M")

    def send_stop_all(self) -> bool:
        """立即停止所有气泵。"""
        return self.send("STOP_ALL")


# ============ 灯箱串口(Uno-B) ============
class LightSender(SerialSender):
    """灯箱控制串口(Uno-B,3 路继电器控制 3 个灯泡)。

    协议(每行一条,以 \\n 结尾):
        LIGHT_ON,id         点亮指定编号灯泡(id ∈ {1,2,3})
        LIGHT_OFF,id        熄灭指定编号灯泡
        LIGHT_ALL_OFF       全部熄灭
        LIGHT_FLASH,3       三灯同时闪烁 3 次(用于 ENDING)
    """

    # 动作 -> 灯泡编号映射
    ACTION_TO_LIGHT_ID = {
        "LEFT_HAND_UP": 1,
        "RIGHT_HAND_UP": 2,
        "BOTH_HANDS_UP": 3,
    }

    def send_light_on(self, light_id: int) -> bool:
        """点亮指定编号灯泡。

        Args:
            light_id: 灯泡编号(1/2/3)。

        Returns:
            bool: 发送成功返回 True。
        """
        return self.send(f"LIGHT_ON,{light_id}")

    def send_light_off(self, light_id: int) -> bool:
        """熄灭指定编号灯泡。

        Args:
            light_id: 灯泡编号(1/2/3)。

        Returns:
            bool: 发送成功返回 True。
        """
        return self.send(f"LIGHT_OFF,{light_id}")

    def send_all_off(self) -> bool:
        """全部灯泡熄灭。"""
        return self.send("LIGHT_ALL_OFF")

    def send_flash(self, times: int = 3) -> bool:
        """三灯同时闪烁若干次。

        Args:
            times: 闪烁次数(默认 3)。

        Returns:
            bool: 发送成功返回 True。
        """
        return self.send(f"LIGHT_FLASH,{times}")

    def light_id_for_action(self, action_name: str) -> Optional[int]:
        """根据手部动作名返回对应灯泡编号。

        Args:
            action_name: 手部动作名(LEFT_HAND_UP / RIGHT_HAND_UP / BOTH_HANDS_UP)。

        Returns:
            Optional[int]: 灯泡编号;无映射时返回 None。
        """
        return self.ACTION_TO_LIGHT_ID.get(action_name)


# ============ 泵组控制(3 板 UNO 联机)============
class PumpGroupSender:
    """泵组发送器:管理 3 块泵控 UNO(PUMP_A/B/C),统一广播指令。

    状态机只产生一条逻辑命令,由本类广播给 3 块 UNO。
    - 普通命令(INFLATE_ALL/DEFLATE_ALL/INFLATE_M)任一板失败 → 触发
      stop_all_best_effort() 并返回 False,状态机进入 SAFE_STOP。
    - STOP_ALL 采用 best-effort:即使某板异常也继续尝试其他板。
    - test_mode=True(SERIAL_ENABLED=False):跳过实际发送,所有方法返回
      成功,用于无硬件的开发/测试环境,状态机可正常流转而不进入 SAFE_STOP。
    - 每次发送记录板号、指令、结果,便于诊断哪块 UNO 掉线。

    Attributes:
        boards: dict[板ID, PumpSender] 已连接的泵控板。
        board_ids: list 板ID顺序,如 ['PUMP_A','PUMP_B','PUMP_C']。
        test_mode: bool 测试模式开关,True 时所有发送跳过并返回成功。
    """

    def __init__(
        self,
        boards_config: list,
        baudrate: int = 9600,
        timeout: float = 1.0,
        write_timeout: float = 0.5,
        test_mode: bool = False,
    ) -> None:
        """初始化泵组。

        Args:
            boards_config: 板配置列表,每项 {'id': 'PUMP_A', 'port': 'COM3'}。
            baudrate: 波特率。
            timeout: 读超时。
            write_timeout: 写超时(防卡死)。
            test_mode: 测试模式,True 时跳过所有串口发送并返回成功
                (用于 SERIAL_ENABLED=False 的开发/测试环境)。
        """
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
            )
            self.boards[board_id] = sender
            self.board_ids.append(board_id)

    def connect_all(self) -> bool:
        """连接所有泵控板。

        test_mode=True 时直接返回 True,不实际连接串口。

        Returns:
            bool: 全部 3 板连接成功返回 True;任一失败返回 False
                  (仍会继续尝试连接其他板,以便 stop_all_best_effort 可用)。
        """
        if self.test_mode:
            logger.info("[PUMP_GROUP] TEST_MODE: 跳过 3 板串口连接")
            return True
        all_ok = True
        for board_id in self.board_ids:
            ok = self.boards[board_id].connect()
            if not ok:
                logger.error("泵控板 %s 连接失败", board_id)
                all_ok = False
        if all_ok:
            logger.info("3 块泵控 UNO 全部连接成功: %s", ", ".join(self.board_ids))
        else:
            connected = self.get_connected_board_ids()
            if connected:
                logger.warning(
                    "部分泵控板连接成功: %s;失败板将无法接收指令",
                    ", ".join(connected),
                )
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

    def send_all(self, command: str) -> dict:
        """向所有已连接板广播一条命令。

        test_mode=True 时跳过实际发送,返回全 True 的结果。

        Args:
            command: 命令文本(不含换行符)。

        Returns:
            dict[板ID, bool]: 每板发送结果。
        """
        if self.test_mode:
            logger.info("[PUMP_GROUP] TEST_MODE: %s (3 板跳过)", command)
            return {bid: True for bid in self.board_ids}
        results: dict = {}
        for board_id in self.board_ids:
            sender = self.boards[board_id]
            ok = sender.send(command)
            results[board_id] = ok
            logger.info("[PUMP_GROUP] %s <- %s : %s", board_id, command, "OK" if ok else "FAIL")
        return results

    def _check_all_ok(self, results: dict) -> bool:
        """检查广播结果是否全部成功。"""
        return all(results.values())

    def stop_all_best_effort(self) -> None:
        """best-effort 广播 STOP_ALL。

        test_mode=True 时直接返回,不实际发送。

        即使某板异常也继续尝试其他板,确保尽可能多的板停止。
        不会抛异常,不会阻塞。
        """
        if self.test_mode:
            logger.info("[PUMP_GROUP] TEST_MODE: STOP_ALL (3 板跳过)")
            return
        for board_id in self.board_ids:
            sender = self.boards[board_id]
            try:
                ok = sender.send("STOP_ALL")
                logger.info("[PUMP_GROUP] %s <- STOP_ALL : %s", board_id, "OK" if ok else "FAIL")
            except Exception as e:  # noqa: BLE001
                logger.error("[PUMP_GROUP] %s STOP_ALL 异常: %s", board_id, e)

    def send_inflate_all(self, seconds: float) -> bool:
        """广播 INFLATE_ALL,seconds 秒。

        任一板失败 → 触发 stop_all_best_effort 并返回 False。
        """
        results = self.send_all(f"INFLATE_ALL,{seconds}")
        if not self._check_all_ok(results):
            logger.error("[PUMP_GROUP] INFLATE_ALL 部分失败,触发 STOP_ALL: %s", results)
            self.stop_all_best_effort()
            return False
        return True

    def send_deflate_all(self, seconds: float) -> bool:
        """广播 DEFLATE_ALL,seconds 秒。

        任一板失败 → 触发 stop_all_best_effort 并返回 False。
        """
        results = self.send_all(f"DEFLATE_ALL,{seconds}")
        if not self._check_all_ok(results):
            logger.error("[PUMP_GROUP] DEFLATE_ALL 部分失败,触发 STOP_ALL: %s", results)
            self.stop_all_best_effort()
            return False
        return True

    def send_inflate_m(self) -> bool:
        """广播 INFLATE_M(每秒一次的惩罚充气,9 泵同步)。

        任一板失败 → 触发 stop_all_best_effort 并返回 False。
        """
        results = self.send_all("INFLATE_M")
        if not self._check_all_ok(results):
            logger.error("[PUMP_GROUP] INFLATE_M 部分失败,触发 STOP_ALL: %s", results)
            self.stop_all_best_effort()
            return False
        return True

    def send_stop_all(self) -> bool:
        """广播 STOP_ALL(最高优先级,best-effort)。

        test_mode=True 时直接返回 True,不实际发送。

        Returns:
            bool: 全部板发送成功返回 True(任一板失败仍继续尝试其他板)。
        """
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

    def close_all(self) -> None:
        """关闭所有泵控板串口。"""
        for board_id in self.board_ids:
            try:
                self.boards[board_id].close()
            except Exception as e:  # noqa: BLE001
                logger.error("[PUMP_GROUP] %s 关闭异常: %s", board_id, e)
