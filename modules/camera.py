"""摄像头模块:封装 OpenCV VideoCapture 的打开、读取、切换和释放。

提供统一的摄像头接口,支持按 c 键切换不同摄像头 ID。
"""
import logging
from typing import Optional, Tuple

import cv2

logger = logging.getLogger(__name__)


class Camera:
    """摄像头封装类,负责管理 VideoCapture 的生命周期。

    Attributes:
        camera_id: 当前摄像头设备 ID。
        width: 采集分辨率宽度。
        height: 采集分辨率高度。
        fps: 目标采集帧率。
    """

    def __init__(
        self,
        camera_id: int,
        width: int = 640,
        height: int = 480,
        fps: int = 30,
    ) -> None:
        """初始化摄像头配置参数。

        Args:
            camera_id: 摄像头设备 ID(0=默认)。
            width: 采集宽度,默认 640。
            height: 采集高度,默认 480。
            fps: 目标帧率,默认 30。
        """
        self.camera_id: int = camera_id
        self.width: int = width
        self.height: int = height
        self.fps: int = fps
        self.cap: Optional[cv2.VideoCapture] = None

    def open(self) -> bool:
        """打开摄像头并设置分辨率、帧率。

        Returns:
            bool: 成功打开返回 True,失败返回 False。
        """
        try:
            self.cap = cv2.VideoCapture(self.camera_id, cv2.CAP_DSHOW)
            if not self.cap.isOpened():
                # 报告 7.3:先 release() 失败的 VideoCapture 避免资源泄漏
                # (旧版直接覆盖 self.cap,失败的实例未释放会占用底层设备句柄)
                self.cap.release()
                # 退回默认 API 再试一次(部分系统不支持 CAP_DSHOW)
                self.cap = cv2.VideoCapture(self.camera_id)
                if not self.cap.isOpened():
                    logger.error("无法打开摄像头 %d,请检查设备或修改 config.py 中的 CAMERA_ID", self.camera_id)
                    return False
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            self.cap.set(cv2.CAP_PROP_FPS, self.fps)
            actual_w = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            logger.info(
                "摄像头 %d 已打开,实际分辨率 %dx%d,目标 %d FPS",
                self.camera_id, actual_w, actual_h, self.fps,
            )
            return True
        except Exception as e:  # noqa: BLE001
            logger.error("摄像头初始化异常: %s", e)
            self.cap = None
            return False

    def read(self) -> Tuple[bool, Optional["object"]]:
        """从摄像头读取一帧画面。

        Returns:
            Tuple[bool, Optional[frame]]: 第一项为是否成功,
                第二项为 BGR 图像帧(失败时为 None)。
        """
        if self.cap is None:
            return False, None
        try:
            return self.cap.read()
        except Exception as e:  # noqa: BLE001
            logger.warning("读取摄像头帧失败: %s", e)
            return False, None

    def switch(self, new_id: int) -> bool:
        """切换到另一个摄像头。

        Args:
            new_id: 新的摄像头 ID。

        Returns:
            bool: 切换并成功打开返回 True。
        """
        logger.info("切换摄像头: %d -> %d", self.camera_id, new_id)
        self.release()
        self.camera_id = new_id
        return self.open()

    def release(self) -> None:
        """释放摄像头资源。"""
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception as e:  # noqa: BLE001
                logger.warning("释放摄像头异常: %s", e)
            finally:
                self.cap = None
                logger.info("摄像头 %d 已释放", self.camera_id)
