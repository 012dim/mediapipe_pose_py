"""pytest conftest.py:把项目根目录加入 sys.path,使测试可导入 config / modules。"""
import os
import sys

# conftest.py 所在目录即为项目根目录
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)
