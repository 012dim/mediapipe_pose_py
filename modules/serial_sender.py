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
    ) -> None:
        """初始化串口参数。

        Args:
            port: 串口端口名,如 'COM3' 或 '/dev/ttyUSB0'。
            baudrate: 波特率,默认 9600。
            timeout: 读超时秒数,默认 1.0。
        """
        self.port: str = port
        self.baudrate: int = baudrate
        self.timeout: float = timeout
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
                self.serial_conn.write(data)
                self.serial_conn.flush()
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

    def send_dual_channel(self, body_action: str, hand_action: str) -> bool:
        """发送双通道状态(两行):POSE_BODY,动作名 / POSE_HAND,动作名。

        Args:
            body_action: 身体通道动作名,如 STAND / SIT / FALL_DETECTED / BODY_NONE。
            hand_action: 手部通道动作名,如 BOTH_HANDS_UP / HAND_NONE。

        Returns:
            bool: 两行均发送成功返回 True。
        """
        ok1 = self.send(f"POSE_BODY,{body_action}")
        ok2 = self.send(f"POSE_HAND,{hand_action}")
        return ok1 and ok2

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
