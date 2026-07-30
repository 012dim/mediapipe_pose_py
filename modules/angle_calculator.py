"""角度计算工具模块。

提供计算关节角度的函数,基于三点(起止点 + 顶点)计算夹角。
可用于肘关节(肩-肘-腕)等关节角度计算。
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
