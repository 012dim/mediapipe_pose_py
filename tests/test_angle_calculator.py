"""angle_calculator 单元测试。

运行:
    pytest tests/test_angle_calculator.py -v
"""
import math

import pytest

from modules.angle_calculator import (
    calculate_angle,
    calculate_knee_angle,
    average_knee_angle,
)


class TestCalculateAngle:
    """calculate_angle 测试。"""

    def test_straight_line_180(self) -> None:
        """三点共线(直线)应返回 180 度。"""
        a = (0.0, 0.0)
        b = (0.5, 0.0)
        c = (1.0, 0.0)
        angle = calculate_angle(a, b, c)
        assert math.isclose(angle, 180.0, abs_tol=0.1)

    def test_right_angle_90(self) -> None:
        """直角应返回 90 度。"""
        a = (0.0, 0.0)
        b = (0.0, 1.0)
        c = (1.0, 1.0)
        angle = calculate_angle(a, b, c)
        assert math.isclose(angle, 90.0, abs_tol=0.1)

    def test_zero_length_vector(self) -> None:
        """顶点与端点重合时不应崩溃(应返回 0 或 180,不抛异常)。"""
        a = (0.5, 0.5)
        b = (0.5, 0.5)
        c = (1.0, 1.0)
        angle = calculate_angle(a, b, c)
        # 重合时 acos(0/eps) -> 0 度
        assert 0.0 <= angle <= 180.0

    def test_range_bound(self) -> None:
        """角度应始终在 [0, 180] 范围内。"""
        a = (0.3, 0.2)
        b = (0.5, 0.5)
        c = (0.7, 0.9)
        angle = calculate_angle(a, b, c)
        assert 0.0 <= angle <= 180.0


class TestKneeAngle:
    """膝关节角度测试。"""

    def test_straight_knee_stand(self) -> None:
        """站直时(髋膝踝共线垂直)膝关节角度接近 180。"""
        hip = (0.5, 0.2)
        knee = (0.5, 0.5)
        ankle = (0.5, 0.8)
        angle = calculate_knee_angle(hip, knee, ankle)
        assert math.isclose(angle, 180.0, abs_tol=0.1)

    def test_bent_knee_sit(self) -> None:
        """坐下时(膝弯曲)角度小于 130。"""
        # 髋在膝正上方,踝在膝右侧 -> 角度 90
        hip = (0.5, 0.4)
        knee = (0.5, 0.5)
        ankle = (0.6, 0.5)
        angle = calculate_knee_angle(hip, knee, ankle)
        assert math.isclose(angle, 90.0, abs_tol=0.1)
        assert angle < 130.0

    def test_average_knee(self) -> None:
        """左右膝关节平均值。"""
        # 左 180,右 90 -> 平均 135
        left_hip, left_knee, left_ankle = (0.5, 0.2), (0.5, 0.5), (0.5, 0.8)
        right_hip, right_knee, right_ankle = (0.5, 0.4), (0.5, 0.5), (0.6, 0.5)
        avg = average_knee_angle(
            left_hip, left_knee, left_ankle,
            right_hip, right_knee, right_ankle,
        )
        assert math.isclose(avg, 135.0, abs_tol=0.2)
