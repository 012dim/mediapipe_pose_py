"""角度计算工具模块。

提供计算关节角度的函数,基于三点(起止点 + 顶点)计算夹角。
主要用于膝关节(髋-膝-踝)、肘关节(肩-肘-腕)等关节角度计算。
"""
import math
from typing import Tuple

import numpy as np


def calculate_angle(
    a: Tuple[float, float],
    b: Tuple[float, float],
    c: Tuple[float, float],
) -> float:
    """计算由三点 a-b-c 在顶点 b 处形成的关节角度。

    例如:计算膝关节角度时,a=髋,b=膝,c=踝。
    角度范围为 [0, 180] 度。直线为 180°,直角为 90°。

    Args:
        a: 端点 1 的坐标 (x, y)。
        b: 关节顶点坐标 (x, y)。
        c: 端点 2 的坐标 (x, y)。

    Returns:
        float: 顶点 b 处的角度,单位为度,范围 [0, 180]。
    """
    a_arr = np.array(a, dtype=np.float64)
    b_arr = np.array(b, dtype=np.float64)
    c_arr = np.array(c, dtype=np.float64)

    ba = a_arr - b_arr
    bc = c_arr - b_arr

    ba_norm = np.linalg.norm(ba)
    bc_norm = np.linalg.norm(bc)
    # 加 1e-8 避免除零
    denominator = ba_norm * bc_norm + 1e-8
    cosine = float(np.dot(ba, bc) / denominator)
    cosine = max(-1.0, min(1.0, cosine))
    angle = math.degrees(math.acos(cosine))
    return angle


def calculate_knee_angle(
    hip: Tuple[float, float],
    knee: Tuple[float, float],
    ankle: Tuple[float, float],
) -> float:
    """计算膝关节角度(髋-膝-踝)。

    Args:
        hip: 髋关节坐标 (x, y)。
        knee: 膝关节坐标 (x, y)。
        ankle: 踝关节坐标 (x, y)。

    Returns:
        float: 膝关节角度(度)。
    """
    return calculate_angle(hip, knee, ankle)


def average_knee_angle(
    left_hip: Tuple[float, float],
    left_knee: Tuple[float, float],
    left_ankle: Tuple[float, float],
    right_hip: Tuple[float, float],
    right_knee: Tuple[float, float],
    right_ankle: Tuple[float, float],
) -> float:
    """计算左右膝关节角度的平均值。

    Args:
        left_hip: 左髋坐标。
        left_knee: 左膝坐标。
        left_ankle: 左踝坐标。
        right_hip: 右髋坐标。
        right_knee: 右膝坐标。
        right_ankle: 右踝坐标。

    Returns:
        float: 平均膝关节角度(度)。
    """
    left_angle = calculate_angle(left_hip, left_knee, left_ankle)
    right_angle = calculate_angle(right_hip, right_knee, right_ankle)
    return (left_angle + right_angle) / 2.0
