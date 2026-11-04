"""把签名软件源发布到 NAS，并将 Pages 缩减为迁移说明页。"""
import argparse
import hashlib
import html
import os
from pathlib import Path
import shutil
import tempfile
from urllib.parse import urlsplit
from urllib.parse import quote
from urllib.request import Request, urlopen


def validate_destination(root, mount):
    """只允许向真实 NAS 挂载点的专用子目录发布公开文件。"""
    root, mount = root.resolve(), mount.resolve()
    if not os.path.ismount(mount):
        raise ValueError(f"NAS mount is not available: {mount}")
    if root == mount or not root.is_relative_to(mount):
        raise ValueError("NAS destination must be a dedicated directory below the mount")
    return root


def file_digest(path):
    """流式计算大包摘要，避免把完整包体读入内存。"""
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").digest()


def copy_atomic(source, target, immutable=False):
    """同名包必须字节一致；更新先完成临时文件，再在目标目录原子替换。"""
    if target.exists():
        if target.stat().st_size == source.stat().st_size and file_digest(target) == file_digest(source):
            return
        if immutable:
            raise ValueError(f"Published NAS artifact differs: {target.name}; increase packaging revision")
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".upload-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as destination, source.open("rb") as incoming:
            shutil.copyfileobj(incoming, destination, length=1024 * 1024)
            destination.flush()
            os.fsync(destination.fileno())
        # CIFS 的替换发生在同一目录，公开 URL 不会看见半个包或半个索引。
        if file_digest(Path(temporary)) != file_digest(source):
            raise ValueError(f"NAS copy checksum mismatch: {source.name}")
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def publish_nas(site, root, mount):
    """先发布包体和内容寻址索引，最后提交客户端优先读取的 InRelease。"""
    root = validate_destination(root, mount)
    if not (site / "dists/trixie/InRelease").is_file():
        raise ValueError("Signed repository is missing InRelease")
    files = sorted(path for path in site.rglob("*") if path.is_file())
    if not any(path.suffix == ".deb" for path in files):
        raise ValueError("Signed repository has no packages")
    # 先完成所有不可变文件及冲突检查，再改公开索引；失败时旧源仍能工作。
    immutable = [path for path in files if path.suffix == ".deb" or "by-hash" in path.parts]
    for source in immutable:
        copy_atomic(source, root / source.relative_to(site), immutable=True)
    inrelease = site / "dists/trixie/InRelease"
    manifest = site / "manifest.json"
    for source in files:
        if source not in immutable and source not in (inrelease, manifest):
            copy_atomic(source, root / source.relative_to(site))
    copy_atomic(inrelease, root / inrelease.relative_to(site))
    # manifest 作为维护器的 NAS 已发布标记，只能在签名入口发布成功后提交。
    copy_atomic(manifest, root / "manifest.json")
    # 不自动删除旧包和 by-hash，兼容缓存的 APT 索引，并保留迁移后的历史下载。
    print(f"Published signed APT repository to NAS: {root}", flush=True)


def prepare_pages(site, pages, base_url):
    """生成独立的小型说明页，确保 Pages artifact 不再包含包体或失效索引。"""
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment or parsed.username:
        raise ValueError("Public NAS URL must be an HTTPS repository root")
    if site.resolve() == pages.resolve() or pages.resolve().is_relative_to(site.resolve()):
        raise ValueError("Pages output must be separate from the signed repository")
    base_url = base_url.rstrip("/") + "/"
    pages.mkdir(parents=True, exist_ok=False)
    for name in ("debxiang.asc", "manifest.json", "SHA256SUMS", "failures.json", ".nojekyll"):
        if (site / name).is_file():
            shutil.copy2(site / name, pages / name)
    command = ("deb [arch=amd64 signed-by=/etc/apt/keyrings/debxiang.gpg] "
               + base_url + " trixie main")
    (pages / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>debxiang 软件源已迁移</title>'
        '<h1>debxiang 软件源已迁移到 NAS</h1>'
        '<p>软件包、APT 索引和签名现由 NAS 托管。原签名密钥继续有效。</p>'
        '<p>请将 /etc/apt/sources.list.d/debxiang.list 替换为：</p><pre>'
        + html.escape(command) + '</pre><p>随后运行 <code>sudo apt update</code>。</p>'
        '<p><a href="' + html.escape(base_url + 'index.html', quote=True)
        + '">新软件源</a> · <a href="debxiang.asc">签名公钥</a></p>', encoding="utf-8")


def verify_public(site, base_url):
    """公网签名及公钥必须与本次发布一致，包体必须可匿名获取且长度匹配。"""
    for relative in ("dists/trixie/InRelease", "debxiang.asc"):
        expected = (site / relative).read_bytes()
        # 避免中间缓存把迁移前或上轮发布的签名误当作本次验收结果。
        url = base_url.rstrip("/") + "/" + relative + "?v=" + hashlib.sha256(expected).hexdigest()
        with urlopen(Request(url, headers={"Cache-Control": "no-cache"}), timeout=60) as response:
            if response.read(len(expected) + 1) != expected:
                raise ValueError(f"Public NAS metadata differs: {relative}")
    for package in sorted((site / "pool").rglob("*.deb")):
        relative = quote(package.relative_to(site).as_posix(), safe="/~")
        request = Request(base_url.rstrip("/") + "/" + relative, method="HEAD",
                          headers={"Cache-Control": "no-cache"})
        with urlopen(request, timeout=60) as response:
            if int(response.headers.get("Content-Length", "-1")) != package.stat().st_size:
                raise ValueError(f"Public NAS package size differs: {package.name}")
    print("Public NAS signatures and package downloads verified", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--site", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--mount", type=Path, required=True)
    parser.add_argument("--pages", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    publish_nas(args.site.resolve(), args.root, args.mount)
    verify_public(args.site.resolve(), args.base_url)
    prepare_pages(args.site.resolve(), args.pages.resolve(), args.base_url)
