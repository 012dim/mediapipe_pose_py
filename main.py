"""MediaPipe Pose 动作识别 - 主程序入口。

一键运行:
    python main.py

主循环负责:
1. 摄像头采集 + 水平翻转
2. MediaPipe Pose 推理(33 关键点)
3. 动作识别(手部 3 种)+ 冷却
4. 状态机推进(8 状态 Arduino 交互流程)
5. 可视化(骨骼 + FPS + 状态面板)
6. 键盘交互(q/s/f/c/r)

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
from modules.action_recognizer import ActionRecognizer, ActionEvent, HandActionState
from modules.camera import Camera
from modules.pose_detector import PoseDetector, PoseResult
from modules.serial_sender import LightSender, PumpGroupSender
from modules.state_machine import StateMachine, STATE_SAFE_STOP
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
        # 4 板 Arduino 串口(3 泵控 PUMP_A/B/C + 1 灯箱)
        self.pump_group: Optional[PumpGroupSender] = None
        self.light_sender: Optional[LightSender] = None
        self.state_machine: Optional[StateMachine] = None
        self._running: bool = False
        # 手部状态变化日志
        self._last_state: Optional[HandActionState] = None

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

        # 4 板 Arduino 串口(3 泵控 PUMP_A/B/C + 1 灯箱)
        # SERIAL_ENABLED=True:严格门禁,3 块泵控必须全部连接才进入运行态
        # SERIAL_ENABLED=False:测试模式,串口未连接,状态机仍可流转
        #   (PumpGroupSender.test_mode=True 时所有 send 跳过并返回成功,
        #    避免 send 失败触发 SAFE_STOP 导致状态机卡死)
        self.pump_group = PumpGroupSender(
            boards_config=config.PUMP_BOARDS,
            baudrate=config.ARDUINO_BAUDRATE,
            timeout=config.SERIAL_TIMEOUT,
            write_timeout=config.SERIAL_WRITE_TIMEOUT,
            test_mode=not config.SERIAL_ENABLED,
        )
        self.light_sender = LightSender(
            port=config.LIGHT_SERIAL_PORT,
            baudrate=config.ARDUINO_BAUDRATE,
            timeout=config.SERIAL_TIMEOUT,
            write_timeout=config.SERIAL_WRITE_TIMEOUT,
        )

        if config.SERIAL_ENABLED:
            # 三板门禁:必须 3 块泵控全部连接,且每板 READY 中的
            # 板号 + 3 泵点充时长均与 config.INFLATE_M_MS_PER_BOARD 一致
            # (拦截"烧错参数"或"改了配置未重烧 Arduino")
            if not self.pump_group.connect_all(
                expected_inflate_m_ms=config.INFLATE_M_MS_PER_BOARD,
            ):
                connected = self.pump_group.get_connected_board_ids()
                missing = [bid for bid in ('PUMP_A', 'PUMP_B', 'PUMP_C')
                           if bid not in connected]
                logger.error("泵控板连接失败,缺失: %s;拒绝进入运行态", missing)
                # 对已连接板 best-effort 发 STOP_ALL 再清理退出
                self.pump_group.stop_all_best_effort()
                self._cleanup()
                return 2
            if not self.light_sender.connect(expected_board_id="LIGHT"):
                logger.error("灯箱串口 %s 连接失败;拒绝进入运行态",
                             config.LIGHT_SERIAL_PORT)
                self.pump_group.stop_all_best_effort()
                self._cleanup()
                return 2
            logger.info("4 板 Arduino 串口全部连接: 泵控=%s, 灯箱=%s",
                        ",".join(b['port'] for b in config.PUMP_BOARDS),
                        config.LIGHT_SERIAL_PORT)
        else:
            # 测试模式:不连接串口,状态机仍可运行(发送静默失败)
            logger.warning(
                "SERIAL_ENABLED=False,测试模式:串口未连接,状态机仍可运行"
            )

        # 状态机
        self.state_machine = StateMachine(self.pump_group, self.light_sender)

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

            # 仅供动作历史/冷却显示,不参与状态机充气判定
            # (状态机使用的是上面不受冷却限制的 state.hand_action)
            self.action_recognizer.recognize(pose_result)

            # 状态机推进(每帧调用,返回快照供可视化)
            if self.state_machine is not None:
                snapshot = self.state_machine.update(
                    pose_result, state.hand_action,
                )
                self.visualizer.set_state_snapshot(snapshot)

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
            # 报告 7.4:SAFE_STOP 态下禁止通过 r 重新充气(必须退出重启)
            if self.state_machine is not None and self.state_machine.state == STATE_SAFE_STOP:
                logger.warning("SAFE_STOP 状态下不能通过 r 重置,请按 q 退出后重启")
                return
            self.action_recognizer.reset()
            self.visualizer._active_event = None  # noqa: SLF001
            self._last_state = None
            if self.state_machine is not None:
                self.state_machine.reset()
            logger.info("已重置动作识别状态与状态机")
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

    def _log_state_change(self, state: HandActionState) -> None:
        """仅在手部动作状态变化时打印日志,避免每帧刷屏。

        Args:
            state: 当前帧的手部动作状态。
        """
        prev = self._last_state
        if prev is None or prev.hand_action != state.hand_action:
            time_str = time.strftime("%H:%M:%S", time.localtime(state.timestamp))
            logger.info("状态变更: hand=%s", state.hand_action)
            print(f"[{time_str}] 状态变更: hand={state.hand_action}")
        self._last_state = state

    # ---------- 退出 / 清理 ----------
    def _signal_handler(self, signum, frame) -> None:  # noqa: ARG002
        """Ctrl+C 信号处理。"""
        logger.info("收到 Ctrl+C 信号,正在退出...")
        self._running = False

    def _cleanup(self) -> None:
        """释放所有资源。

        顺序:① 摄像头/Pose ② 广播 STOP_ALL + LIGHT_ALL_OFF(安全) ③ 关闭串口
        必须先发 STOP_ALL 再关串口,确保气泵停止后再断开连接。
        """
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
        # 安全:先广播 STOP_ALL 和 LIGHT_ALL_OFF,再关闭串口
        if self.pump_group is not None:
            try:
                self.pump_group.stop_all_best_effort()
            except Exception as e:  # noqa: BLE001
                logger.warning("STOP_ALL 异常: %s", e)
        if self.light_sender is not None:
            try:
                self.light_sender.send_all_off()
            except Exception as e:  # noqa: BLE001
                logger.warning("LIGHT_ALL_OFF 异常: %s", e)
        # 关闭串口
        if self.pump_group is not None:
            try:
                self.pump_group.close_all()
            except Exception as e:  # noqa: BLE001
                logger.warning("关闭泵组串口异常: %s", e)
        if self.light_sender is not None:
            try:
                self.light_sender.close()
            except Exception as e:  # noqa: BLE001
                logger.warning("关闭灯箱串口异常: %s", e)
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
