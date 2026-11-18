"""仅2026-11-18流式指纹实现与前版语义等价；未知解释器不沿用旧身份。"""

import hashlib

PREVIOUS_NORMALIZER = "9ee1f39d70b324fd4040d6a63a706ba977c155094464e8359baf850fcf82f08b"
STREAMING_NORMALIZER = "1247edb74d302e827a9db6fd5c6871f30bbfd7520ce89a2ca0e8a190238a1571"


def normalizer_revision(path):
    """只折叠已逐字节验证的实现修订；后续任何源代码变化仍产生新语义身份。"""
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    return PREVIOUS_NORMALIZER if actual == STREAMING_NORMALIZER else actual


def compatible_normalizer(archived, current):
    """归档源码不执行；只有明确审核的旧→新精确摘要对可按同一规则重放。"""
    return (
        hashlib.sha256(archived.encode()).hexdigest() == PREVIOUS_NORMALIZER
        and hashlib.sha256(current).hexdigest() == STREAMING_NORMALIZER
    )
