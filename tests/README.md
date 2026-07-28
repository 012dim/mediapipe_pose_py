# 测试目录

本项目使用 [pytest](https://docs.pytest.org/) 进行单元测试。

## 运行测试

在项目根目录 `mediapipe_pose_py/` 下执行:

```bash
# 运行所有测试
pytest tests/ -v

# 运行单个测试文件
pytest tests/test_angle_calculator.py -v

# 运行单个测试类
pytest tests/test_action_recognizer.py::TestHandUp -v

# 显示覆盖率(需先安装 pytest-cov)
pytest tests/ --cov=modules --cov-report=term-missing
```

## 测试内容

| 测试文件 | 覆盖模块 | 说明 |
|---------|---------|------|
| `test_angle_calculator.py` | `modules/angle_calculator.py` | 三点夹角、膝关节角度、平均值计算 |
| `test_action_recognizer.py` | `modules/action_recognizer.py` | 6 种动作识别、冷却、重置逻辑 |

## 说明

- 测试不依赖摄像头和 MediaPipe 推理,使用 mock 的 `PoseResult` / `LandmarkPoint` 验证业务逻辑
- `conftest.py` 已配置好项目根目录到 `sys.path`,可直接 `import config` 与 `import modules`
- 视觉相关模块(`visualizer`、`camera`、`pose_detector`)因依赖硬件或图形界面,通过手动运行 `python main.py` 验证
