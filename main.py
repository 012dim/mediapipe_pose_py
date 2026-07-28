"""MediaPipe Pose 动作识别 - 主程序入口。

一键运行:
    python main.py

主循环负责:
1. 摄像头采集 + 水平翻转
2. MediaPipe Pose 推理(33 关键点)
3. 动作识别(6 种)+ 冷却
4. 可视化(骨骼 + FPS + 动作文字)
5. 键盘交互(q/s/f/c/r)
6. 串口发送(可选)

按 q 或 Esc 退出。
"""
import logging
import os
import signal
import sys
import time
from datetime import datetime
from typing import Optional

import cv2
import numpy as np

import config
from modules.action_recognizer import ActionRecognizer, ActionEvent, DualChannelState
from modules.camera import Camera
from modules.pose_detector import PoseDetector, PoseResult
from modules.serial_sender import SerialSender
from modules.visualizer import Visualizer

# ============ 日志配置 ============
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format=config.LOG_FORMAT,
)
logger = logging.getLogger("main")

WINDOW_TITLE = "MediaPipe Pose - Action Recognition (q to quit)"


class Application:
    """主应用类,负责组装所有模块并运行主循环。"""

    def __init__(self) -> None:
        """初始化所有模块。"""
        self.camera: Camera = Camera(
            camera_id=config.CAMERA_ID,
            width=config.CAMERA_WIDTH,
            height=config.CAMERA_HEIGHT,
            fps=config.CAMERA_FPS,
        )
        self.pose_detector: Optional[PoseDetector] = None
        self.visualizer: Visualizer = Visualizer(
            show_skeleton=config.SHOW_SKELETON_DEFAULT,
        )
        self.action_recognizer: ActionRecognizer = ActionRecognizer()
        self.serial_sender: Optional[SerialSender] = None
        self._running: bool = False
        # 双通道状态相关
        self._last_serial_time: float = 0.0
        self._last_state: Optional[DualChannelState] = None

    # ---------- 生命周期 ----------
    def setup(self) -> int:
        """初始化摄像头、Pose、串口。

        Returns:
            int: 0 成功,非 0 表示失败退出码。
        """
        logger.info("=" * 60)
        logger.info("MediaPipe Pose 动作识别 启动中...")
        logger.info("=" * 60)

        # 摄像头
        if not self.camera.open():
            logger.error(
                "无法打开摄像头 %d,请检查设备或修改 config.py 中的 CAMERA_ID",
                config.CAMERA_ID,
            )
            return 1

        # MediaPipe Pose
        try:
            self.pose_detector = PoseDetector()
        except RuntimeError as e:
            logger.error("MediaPipe 初始化失败: %s", e)
            self._cleanup()
            return 1

        # 截图目录
        os.makedirs(config.SCREENSHOT_DIR, exist_ok=True)

        # 串口(可选)
        if config.SERIAL_ENABLED:
            port = config.get_default_serial_port()
            self.serial_sender = SerialSender(
                port=port,
                baudrate=config.SERIAL_BAUDRATE,
                timeout=config.SERIAL_TIMEOUT,
            )
            self.serial_sender.connect()  # 失败仅警告,不退出
        else:
            logger.info("串口未启用(可在 config.py 中设置 SERIAL_ENABLED = True 开启)")

        logger.info("启动完成,进入主循环")
        return 0

    def run(self) -> int:
        """主循环。

        Returns:
            int: 进程退出码。
        """
        code = self.setup()
        if code != 0:
            return code

        self._running = True
        # 注册 Ctrl+C 信号处理
        signal.signal(signal.SIGINT, self._signal_handler)

        try:
            cv2.namedWindow(WINDOW_TITLE, cv2.WINDOW_AUTOSIZE)
            self._main_loop()
        except Exception as e:  # noqa: BLE001
            logger.exception("主循环异常: %s", e)
            return 1
        finally:
            self._cleanup()
            cv2.destroyAllWindows()
        return 0

    def _main_loop(self) -> None:
        """主循环主体:逐帧采集 -> 推理 -> 识别 -> 绘制 -> 显示。"""
        while self._running:
            ok, frame = self.camera.read()
            if not ok or frame is None:
                logger.warning("读取摄像头帧失败,重试...")
                time.sleep(0.05)
                continue

            # 水平翻转(镜像,让用户感觉更自然)
            frame = cv2.flip(frame, 1)

            try:
                pose_result = self.pose_detector.process(frame)
            except Exception as e:  # noqa: BLE001
                logger.warning("推理异常,跳过本帧: %s", e)
                pose_result = PoseResult(
                    landmarks=None, raw_landmarks=None, person_detected=False,
                )

            # 动作识别(双通道实时状态,不受冷却限制)
            state = self.action_recognizer.recognize_current(pose_result)
            self.visualizer.set_current_state(state)
            self._log_state_change(state)      # 状态变化时才打印日志
            self._send_serial_state(state)     # 每 SERIAL_INTERVAL 秒定时发送

            # FPS
            fps = self.visualizer.fps_counter.tick()

            # 绘制
            person_count = 1 if pose_result.person_detected else 0
            frame_out = self.visualizer.draw(
                frame,
                pose_result,
                fps,
                person_count,
                self.action_recognizer.recent_actions,
            )

            cv2.imshow(WINDOW_TITLE, frame_out)

            # 键盘交互
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q") or key == 27:  # q 或 Esc
                logger.info("收到退出指令,正在关闭...")
                self._running = False
            else:
                self._handle_key(key)

    # ---------- 键盘交互 ----------
    def _handle_key(self, key: int) -> None:
        """处理键盘按键。

        Args:
            key: cv2.waitKey 返回的按键码。
        """
        if key == 255:
            return
        if key == ord("s"):
            self._take_screenshot()
        elif key == ord("f"):
            self.visualizer.toggle_skeleton()
        elif key == ord("c"):
            self._switch_camera()
        elif key == ord("r"):
            self.action_recognizer.reset()
            self.visualizer._active_event = None  # noqa: SLF001
            self._last_state = None
            logger.info("已重置动作识别状态")
        elif key != 255:
            logger.debug("未映射按键: %d", key)

    def _take_screenshot(self) -> None:
        """保存当前帧截图到 screenshots/ 目录。"""
        ok, frame = self.camera.read()
        if not ok or frame is None:
            logger.warning("截图失败:无法读取摄像头帧")
            return
        frame = cv2.flip(frame, 1)
        # 重新走一遍可视化(确保截图带骨骼)
        pose_result = self.pose_detector.process(frame)
        fps = self.visualizer.fps_counter.tick()
        person_count = 1 if pose_result.person_detected else 0
        frame_out = self.visualizer.draw(
            frame, pose_result, fps, person_count,
            self.action_recognizer.recent_actions,
        )
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"pose_{ts}.png"
        filepath = os.path.join(config.SCREENSHOT_DIR, filename)
        if cv2.imwrite(filepath, frame_out):
            logger.info("截图已保存: %s", filepath)
        else:
            logger.warning("截图保存失败: %s", filepath)

    def _switch_camera(self) -> None:
        """切换到下一个摄像头 ID(0 -> 1 -> 2 -> 0)。"""
        ids = config.AVAILABLE_CAMERA_IDS
        if not ids:
            logger.warning("未配置 AVAILABLE_CAMERA_IDS")
            return
        try:
            idx = ids.index(self.camera.camera_id)
        except ValueError:
            idx = -1
        new_id = ids[(idx + 1) % len(ids)]
        if not self.camera.switch(new_id):
            logger.warning("切换到摄像头 %d 失败,切回原摄像头", new_id)
            # 尝试切回原摄像头
            self.camera.open()

    def _log_state_change(self, state: DualChannelState) -> None:
        """仅在双通道状态变化时打印日志,避免每帧刷屏。

        Args:
            state: 当前帧的双通道状态。
        """
        prev = self._last_state
        if (prev is None
                or prev.body_action != state.body_action
                or prev.hand_action != state.hand_action):
            time_str = time.strftime("%H:%M:%S", time.localtime(state.timestamp))
            logger.info("状态变更: body=%s hand=%s", state.body_action, state.hand_action)
            print(f"[{time_str}] 状态变更: body={state.body_action} hand={state.hand_action}")
        self._last_state = state

    def _send_serial_state(self, state: DualChannelState) -> None:
        """按 SERIAL_INTERVAL 定时发送双通道状态到串口(若启用)。

        无动作时根据 SERIAL_SEND_NONE 决定是否发送。

        Args:
            state: 当前帧的双通道状态。
        """
        if self.serial_sender is None or not self.serial_sender.is_connected:
            return
        now = time.time()
        if now - self._last_serial_time < config.SERIAL_INTERVAL:
            return
        self._last_serial_time = now
        # 无动作时根据配置决定是否发送
        if not config.SERIAL_SEND_NONE:
            if state.body_action == "BODY_NONE" and state.hand_action == "HAND_NONE":
                return
        self.serial_sender.send_dual_channel(state.body_action, state.hand_action)

    def _send_serial(self, event: ActionEvent) -> None:
        """通过串口发送动作事件(若启用)。

        Args:
            event: 触发的动作事件。
        """
        if self.serial_sender is not None and self.serial_sender.is_connected:
            self.serial_sender.send_action(event.name)

    # ---------- 退出 / 清理 ----------
    def _signal_handler(self, signum, frame) -> None:  # noqa: ARG002
        """Ctrl+C 信号处理。"""
        logger.info("收到 Ctrl+C 信号,正在退出...")
        self._running = False

    def _cleanup(self) -> None:
        """释放所有资源。"""
        logger.info("正在释放资源...")
        try:
            self.camera.release()
        except Exception as e:  # noqa: BLE001
            logger.warning("释放摄像头异常: %s", e)
        try:
            if self.pose_detector is not None:
                self.pose_detector.close()
        except Exception as e:  # noqa: BLE001
            logger.warning("关闭 PoseDetector 异常: %s", e)
        try:
            if self.serial_sender is not None:
                self.serial_sender.close()
        except Exception as e:  # noqa: BLE001
            logger.warning("关闭串口异常: %s", e)
        try:
            cv2.destroyAllWindows()
        except Exception:  # noqa: BLE001
            pass
        logger.info("资源已释放,程序退出。")


def main() -> int:
    """程序入口。"""
    app = Application()
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
