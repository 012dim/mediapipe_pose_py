"""camera 模块单元测试(v4.2.1)。

报告 7.3 P2 修复验证:
- Camera.open() CAP_DSHOW 失败时先 release() 再退回默认 API(避免资源泄漏)
- Application._switch_camera() 新 ID 失败时显式 switch(old_id) 切回旧摄像头
  (旧版只调 open(),此时 camera_id 已被 switch() 改成失败的新 ID,等于再次打开失败设备)

运行:
    pytest tests/test_camera.py -v
"""
import sys
import types

import pytest

import config


# ============ FakeVideoCapture ============

class FakeVideoCapture:
    """模拟 cv2.VideoCapture,按 (camera_id, backend) 预设 isOpened 结果。

    backend=None 表示使用默认 API(调用方未传第二参数)。
    opened_map: dict[(camera_id, backend_or_None)] -> bool
    instances: 类级列表,记录所有创建的实例,供测试断言资源是否被正确回收。
    """

    opened_map: dict = {}
    instances: list = []

    def __init__(self, camera_id, backend=None) -> None:
        self.camera_id = camera_id
        self.backend = backend
        self._opened = FakeVideoCapture.opened_map.get((camera_id, backend), False)
        self.released = False
        FakeVideoCapture.instances.append(self)

    def isOpened(self) -> bool:
        return self._opened

    def set(self, prop, value) -> bool:
        return True

    def get(self, prop):
        return 0

    def read(self):
        return False, None

    def release(self) -> None:
        self.released = True


@pytest.fixture
def fake_cv2(monkeypatch):
    """注入伪造的 cv2 模块,提供 CAP_DSHOW / CAP_PROP_* 常量与 VideoCapture。"""
    fake = types.ModuleType("cv2")
    fake.CAP_DSHOW = 700
    fake.CAP_PROP_FRAME_WIDTH = 3
    fake.CAP_PROP_FRAME_HEIGHT = 4
    fake.CAP_PROP_FPS = 5
    fake.VideoCapture = FakeVideoCapture

    monkeypatch.setitem(sys.modules, "cv2", fake)
    import modules.camera
    monkeypatch.setattr(modules.camera, "cv2", fake)

    # 每个测试重置状态
    FakeVideoCapture.opened_map = {}
    FakeVideoCapture.instances = []
    return fake


# ============ Camera.open() 测试 ============

class TestCameraOpen:
    """报告 7.3:CAP_DSHOW 失败时先 release() 再退回默认 API。"""

    def test_open_success_first_try(self, fake_cv2) -> None:
        """CAP_DSHOW 直接成功 → 不调用 release()。"""
        FakeVideoCapture.opened_map = {(0, fake_cv2.CAP_DSHOW): True}
        from modules.camera import Camera
        cam = Camera(0)

        assert cam.open() is True
        assert cam.cap is not None
        assert cam.cap.released is False

    def test_open_dshow_fail_fallback_default_api(self, fake_cv2) -> None:
        """报告 7.3:CAP_DSHOW 失败 → 先 release() 失败实例 → 默认 API 重试成功。

        旧版直接覆盖 self.cap,失败的 VideoCapture 实例未释放会占用底层设备句柄;
        新版显式 release() 后再用默认 API 重试。
        """
        FakeVideoCapture.opened_map = {
            (0, fake_cv2.CAP_DSHOW): False,
            (0, None): True,
        }
        from modules.camera import Camera
        cam = Camera(0)

        assert cam.open() is True

        # 关键断言:DSHOW 失败的实例被调用了 release()
        dshow_instances = [
            inst for inst in FakeVideoCapture.instances
            if inst.backend == fake_cv2.CAP_DSHOW
        ]
        assert len(dshow_instances) == 1
        assert dshow_instances[0].released is True

    def test_open_both_backends_fail(self, fake_cv2) -> None:
        """CAP_DSHOW 和默认 API 都失败 → 返回 False。"""
        FakeVideoCapture.opened_map = {
            (0, fake_cv2.CAP_DSHOW): False,
            (0, None): False,
        }
        from modules.camera import Camera
        cam = Camera(0)

        assert cam.open() is False


# ============ Camera.switch() 测试 ============

class TestCameraSwitch:
    """Camera.switch() 切换逻辑(配合 _switch_camera 使用)。"""

    def test_switch_success(self, fake_cv2) -> None:
        """新 ID 打开成功 → camera_id 更新,返回 True。"""
        FakeVideoCapture.opened_map = {(0, fake_cv2.CAP_DSHOW): True}
        from modules.camera import Camera
        cam = Camera(0)
        cam.open()

        FakeVideoCapture.opened_map = {(1, fake_cv2.CAP_DSHOW): True}
        assert cam.switch(1) is True
        assert cam.camera_id == 1

    def test_switch_fail_keeps_new_id(self, fake_cv2) -> None:
        """新 ID 打开失败 → camera_id 仍是新 ID(switch 已改)。

        这是 _switch_camera 需要显式切回旧 ID 的根本原因。
        """
        FakeVideoCapture.opened_map = {(0, fake_cv2.CAP_DSHOW): True}
        from modules.camera import Camera
        cam = Camera(0)
        cam.open()

        FakeVideoCapture.opened_map = {(1, fake_cv2.CAP_DSHOW): False, (1, None): False}
        assert cam.switch(1) is False
        assert cam.camera_id == 1


# ============ Application._switch_camera() 测试(报告 7.3 核心) ============

class TestApplicationSwitchCameraFallback:
    """报告 7.3:新 ID 失败时显式 switch(old_id) 切回旧摄像头。

    旧版失败时只调 self.camera.open(),但此时 camera_id 已被 switch()
    改成失败的新 ID,open() 等于再次打开失败设备。
    """

    def test_switch_success_no_fallback(self, fake_cv2, monkeypatch) -> None:
        """新 ID 打开成功 → camera_id 变为新 ID,无需回退。"""
        FakeVideoCapture.opened_map = {(0, fake_cv2.CAP_DSHOW): True}
        from modules.camera import Camera
        cam = Camera(0)
        cam.open()

        monkeypatch.setattr(config, "AVAILABLE_CAMERA_IDS", [0, 1, 2])
        FakeVideoCapture.opened_map = {(1, fake_cv2.CAP_DSHOW): True}

        from main import Application
        from types import SimpleNamespace
        app = SimpleNamespace(camera=cam)
        Application._switch_camera(app)

        assert cam.camera_id == 1

    def test_switch_fail_falls_back_to_old_id(self, fake_cv2, monkeypatch) -> None:
        """新 ID 失败 → 显式 switch(old_id) 恢复(旧版只调 open() 实际仍是新 ID)。"""
        FakeVideoCapture.opened_map = {(0, fake_cv2.CAP_DSHOW): True}
        from modules.camera import Camera
        cam = Camera(0)
        cam.open()

        monkeypatch.setattr(config, "AVAILABLE_CAMERA_IDS", [0, 1, 2])
        FakeVideoCapture.opened_map = {
            (1, fake_cv2.CAP_DSHOW): False,
            (1, None): False,
            (0, fake_cv2.CAP_DSHOW): True,
        }

        from main import Application
        from types import SimpleNamespace
        app = SimpleNamespace(camera=cam)
        Application._switch_camera(app)

        assert cam.camera_id == 0

    def test_switch_both_fail_stays_at_old_id(self, fake_cv2, monkeypatch) -> None:
        """新 ID 和旧 ID 都失败 → 记录错误日志,camera_id 停在 old_id(已尝试切回)。"""
        FakeVideoCapture.opened_map = {(0, fake_cv2.CAP_DSHOW): True}
        from modules.camera import Camera
        cam = Camera(0)
        cam.open()

        monkeypatch.setattr(config, "AVAILABLE_CAMERA_IDS", [0, 1, 2])
        FakeVideoCapture.opened_map = {
            (1, fake_cv2.CAP_DSHOW): False, (1, None): False,
            (0, fake_cv2.CAP_DSHOW): False, (0, None): False,
        }

        from main import Application
        from types import SimpleNamespace
        app = SimpleNamespace(camera=cam)
        Application._switch_camera(app)

        assert cam.camera_id == 0

    def test_empty_ids_no_op(self, fake_cv2, monkeypatch) -> None:
        """AVAILABLE_CAMERA_IDS 为空 → 不操作,camera_id 不变。"""
        FakeVideoCapture.opened_map = {(0, fake_cv2.CAP_DSHOW): True}
        from modules.camera import Camera
        cam = Camera(0)
        cam.open()

        monkeypatch.setattr(config, "AVAILABLE_CAMERA_IDS", [])

        from main import Application
        from types import SimpleNamespace
        app = SimpleNamespace(camera=cam)
        Application._switch_camera(app)

        assert cam.camera_id == 0
