# debxiang

免费 Debian APT 仓库：跟踪 **uv、Zig、Ghostty** 的上游稳定版，以及 **OpenAI 官方 ChatGPT Linux 客户端**。发布 `uv`、`zig-stable`、`zig`、`ghostty`、`chatgpt`。不依赖 Griffo，也不需要软件下载订阅；ChatGPT 登录和服务按 OpenAI 的账户规则使用。

目标为 **Debian 13 trixie / amd64**，在 Debian trixie 容器内打包和安装验证；Debian sid 通常可以安装，但这里只对 trixie 的安装验证作保证。Ghostty 的图形界面需要另行在桌面环境验证。

## 自动维护

- 新仓库的独立自托管 runner 位于 `xiang@xiang233.top:22022`，标签为 `self-hosted, Linux, X64, debxiang`。不使用 MusicMapMaker 的工作目录、凭据或 runner 注册信息。
- GitHub Actions 每小时第 23 分钟检查上游，也可手动触发。cron 是定时轮询，通常一小时内发现更新，GitHub 调度可能延迟；公共仓库长期无活动时可能禁用 schedule，需要重新启用。
- 版本与最近一次成功部署完全相同时跳过制品上传和 Pages 部署；首次部署及失败后的重试会正常发布。
- uv 查询 GitHub 最新稳定 release，并校验上游 SHA256。Zig 查询官方 download index，跳过 master；下载校验官方 SHA256。Ghostty 从稳定版本标签发现更新，使用官网源码包并校验 minisign 签名。
- ChatGPT 从 OpenAI 官方签名 APT 仓库发现更新：固定公钥主指纹，验证 InRelease，再校验 Packages 索引和 `.deb` 的 SHA256/大小。原字节镜像官方 `.deb`，保留厂商版本、许可、依赖和安装脚本。同版本上游 SHA256 变化会报错并保留旧包。
- Ghostty 从当前源码读取精确 Zig 版本，单独下载校验，先获取依赖缓存再使用 `--system` 编译。安装完整桌面资源，ELF 动态依赖由 `dpkg-shlibdeps` 生成。
- 每次新构建后在干净 `debian:trixie` 容器安装包并运行 CLI；Zig 额外编译并运行一个小程序。ChatGPT 验证依赖安装、包版本和可执行入口；GUI、登录及 Wayland/X11 需要桌面环境另行验收。
- 使用约 60% CPU；新构建前至少需要 6 GiB 空闲磁盘。Podman 镜像与上游临时源码不进入 Git。
- 有上游 SHA256 的下载保存在独立状态目录，跨任务恢复中断下载；复用时仍重新校验 SHA256，缓存最多保留约 1 GiB。
- 每个工具独立发现和验证更新；一个工具失败不会阻止其他工具发布，失败的工具保留旧版，Pages 部署后 workflow 明确报错。若没有任何已验证包则终止发布。索引失败不提交状态；部署失败时下一次任务会重试发布，已存在的 Release 制品不会覆盖。
- 包版本形如 `1.2.3-100~debxiang1~trixie`，同上游版本高于常见 Griffo 的 `-1` 修订。打包实现有变化且需要重发同一上游版本时，修改 `scripts/build.py` 的版本修订（例如 `100` -> `101`）。不要修改已发布包的内容而保留版本号。
- APT 站点通常每个包保留最近三个版本；ChatGPT 官方包约 500 MB，仅保留最新版。软件包总量限制在 900 MiB；完整历史保留在 GitHub Releases，避免超过 Pages 的容量限制。客户端若使用旧索引，需要先运行 `apt update`。上游 stable 版本低于机器已装版本时，APT 不会自动降级。

## Runner 接入（注册由仓库所有者完成）

远端已有 rootless Podman、Python 3.13、GnuPG、apt-ftparchive 和 GitHub CLI。准备脚本只在新的 `~/actions-runner-debxiang` 目录下载、校验官方 runner，并创建独立用户服务，不复制旧 runner 凭据、不自动注册。

```bash
ssh xiang@xiang233.top -p 22022
cd ~/debxiang
bash scripts/prepare-runner.sh
cd ~/actions-runner-debxiang
./config.sh --url https://github.com/xiangCaO233/debxiang \
  --token YOUR_SHORT_LIVED_REGISTRATION_TOKEN \
  --name debxiang-debian --labels debxiang --work _work
systemctl --user enable --now debxiang-runner.service
```

临时 token 从仓库 **Settings → Actions → Runners → New self-hosted runner** 获取。不要把 token 写入仓库。该机器已启用 user lingering，用户服务可以在 SSH 退出后保持运行。

仅维护默认 main 分支，不在 self-hosted runner 上运行 pull request 工作流。仓库 Pages 设置为 GitHub Actions 发布；工作流使用短期 `github.token`，无需 PAT secret。

## APT 签名与备份

首次构建自动在 `~/.local/state/debxiang/gnupg` 创建独立 Ed25519 签名私钥；目录权限 0700，私钥不进入 Git、不上传 Pages、不进入构建容器。公开密钥为站点根下 `debxiang.asc`。

**备份整个 `~/.local/state/debxiang`**，尤其是 gnupg、signing-key.txt、pool 和 manifest.json。换机必须恢复相同私钥和包文件，不能重新生成密钥冒充原仓库；丢失密钥需要明确进行密钥轮换，让客户端重新信任。已有同版本包在 GitHub 的校验值不匹配时会拒绝覆盖。

## 安装软件源

首次成功部署后，地址为 `https://xiangcao233.github.io/debxiang/`。先核对工作流输出的签名指纹，再添加：

```bash
curl -fsSL https://xiangcao233.github.io/debxiang/debxiang.asc -o /tmp/debxiang.asc
gpg --show-keys --with-fingerprint /tmp/debxiang.asc
sudo install -d -m 0755 /etc/apt/keyrings
sudo gpg --dearmor --yes -o /etc/apt/keyrings/debxiang.gpg /tmp/debxiang.asc
echo 'deb [arch=amd64 signed-by=/etc/apt/keyrings/debxiang.gpg] https://xiangcao233.github.io/debxiang/ trixie main' \
  | sudo tee /etc/apt/sources.list.d/debxiang.list
sudo apt update
sudo apt install uv zig zig-stable ghostty chatgpt
```

客户端启用前应禁用原 Griffo 收费源，以免 APT 继续优先选择它的更新版本。这个项目不会自动修改客户端软件源。

**ChatGPT 原包的行为**：官方 `postinst` 默认会额外添加 `/etc/apt/sources.list.d/chatgpt.sources` 和官方密钥。因此安装本仓库的镜像包后，客户端也会连接官方 OpenAI APT 源。如果需要只使用 debxiang，在首次安装前按官方安装脚本支持的方式预置 `/etc/default/chatgpt` 的 `repo_add_once="false"`。本项目保留原包，不修改这个行为。

## 本地验证

```bash
python3 -m unittest discover -s tests -v
python3 scripts/discover.py
python3 scripts/maintain.py --state "$HOME/.local/state/debxiang" --site /tmp/debxiang-site
```

签名 APT 索引在 `dists/trixie/`，软件包在 `pool/main/`，构建来源在 `manifest.json`，公开制品还发布到仓库 GitHub Releases。完整维护需 Podman、GnuPG、apt-ftparchive、Python 3.12+；发布需 gh。

上游链接：[uv](https://github.com/astral-sh/uv)、[Zig](https://ziglang.org/download/)、[Ghostty](https://ghostty.org/docs/install/build)、[ChatGPT Linux 官方说明](https://learn.chatgpt.com/docs/linux/linux-app)。重新打包的开源工具附上游版权到 `/usr/share/doc/<package>/`；ChatGPT 原包保留厂商许可。仓库内维护脚本采用 MIT 许可。
