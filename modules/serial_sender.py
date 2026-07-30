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
