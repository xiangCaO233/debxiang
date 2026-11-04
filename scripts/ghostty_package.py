"""为 Ghostty 的离线构建准备 Zig 0.17 依赖缓存。"""
from pathlib import Path
import re
import shutil
import tempfile
from common import extract, run


def materialize_zig_cache(compiler, cache, environment):
    """解开 Zig 已验证的缓存归档，并再次核对包哈希后发布目录。"""
    # Zig 0.17 的无 --save fetch 只保存 p/<hash>.tar.gz；--system 却需要目录。
    # 固定本轮清单，避免校验时 Zig 新生成的缓存归档再次进入本轮遍历。
    archives = sorted(cache.glob("*.tar.gz"))
    for archive in archives:
        expected = archive.name.removesuffix(".tar.gz")
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.+-]*", expected):
            raise ValueError("Invalid Zig cache archive name")
        target = cache / expected

        def package_hash(directory):
            """使用同一编译器的包哈希算法校验目录，不以文件夹名称代替身份。"""
            return run(compiler, "fetch", directory, env=environment,
                       capture_output=True, text=True).stdout.strip()

        if target.exists():
            # 缓存只属于当前构建事务；出现不同内容时立即失败，不覆盖现场。
            if target.is_symlink() or not target.is_dir() or package_hash(target) != expected:
                raise ValueError(f"Existing Zig package hash differs: {expected}")
            continue
        with tempfile.TemporaryDirectory(prefix="zig-unpack-", dir=cache.parent) as temporary:
            # 继续使用统一安全解包入口，拒绝越界路径和逃逸符号链接。
            candidate = extract(archive, Path(temporary))
            while True:
                if candidate.is_symlink():
                    raise ValueError("Zig package root must not be a symlink")
                actual = package_hash(candidate)
                if actual == expected:
                    break
                entries = list(candidate.iterdir())
                # 部分归档还保留上游单根目录；只在完整包哈希匹配后采用内层。
                # 不修改 manifest 的 .hash，也不对混合目录猜测或忽略校验失败。
                if len(entries) != 1 or not entries[0].is_dir() or entries[0].is_symlink():
                    raise ValueError(f"Zig package hash differs: expected {expected}, received {actual}")
                candidate = entries[0]
            shutil.move(str(candidate), target)
        print(f"Materialized verified Zig package: {expected}", flush=True)
