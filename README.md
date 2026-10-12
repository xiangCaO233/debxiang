# debxiang

免费的 Debian APT 软件源，提供 uv、Zig、Ghostty、[Codex CLI](https://github.com/openai/codex)、[Clash Verge Rev](https://github.com/clash-verge-rev/clash-verge-rev)，以及 [OpenAI 官方 ChatGPT Linux 客户端](https://learn.chatgpt.com/docs/linux/linux-app)。自动跟踪上游稳定版，无需软件下载订阅。

支持 **Debian 13（trixie）、amd64 / x86_64**。

| 软件包 | 用途 |
| --- | --- |
| `uv` | Python 项目和包管理，包含 `uv`、`uvx` |
| `zig` | 安装最新稳定版 Zig，自动依赖 `zig-stable` |
| `zig-stable` | Zig 编译器和标准库 |
| `ghostty` | Ghostty 桌面终端 |
| `codex-cli` | OpenAI Codex 终端编程助手，命令为 `codex` |
| `clash-verge` | Clash Verge Rev，GitHub 最新正式 Release 的原始 DEB |
| `chatgpt` | 官方 ChatGPT Linux 客户端，保留原始安装包 |

## 添加软件源

如果之前使用 Griffo，请先禁用其软件源，避免 APT 继续从那里下载。

安装所需工具：

```bash
sudo apt update
sudo apt install ca-certificates curl gnupg
```

下载仓库公钥并核对指纹：

```bash
curl -fsSL https://xiangcao233.github.io/debxiang/debxiang.asc -o /tmp/debxiang.asc
gpg --show-keys --with-fingerprint /tmp/debxiang.asc
```

公钥指纹应为：

```text
99CF 8537 1E76 2BA9 606A  112C B8F9 EC9D E4E6 67BC
```

添加签名密钥和软件源：

```bash
sudo install -d -m 0755 /etc/apt/keyrings
sudo gpg --dearmor --yes -o /etc/apt/keyrings/debxiang.gpg /tmp/debxiang.asc
sudo chmod 0644 /etc/apt/keyrings/debxiang.gpg
printf '%s\n' 'deb [arch=amd64 signed-by=/etc/apt/keyrings/debxiang.gpg] https://xiangcao233.github.io/debxiang/ trixie main' \
  | sudo tee /etc/apt/sources.list.d/debxiang.list
sudo apt update
```

## 安装和更新

按需安装，也可以一次安装全部工具：

```bash
sudo apt install uv zig ghostty chatgpt codex-cli clash-verge
```

检查和安装后续更新：

```bash
sudo apt update
sudo apt install --only-upgrade uv zig zig-stable ghostty chatgpt codex-cli clash-verge
```

APT 不会自动将已安装的软件降级。如果索引过旧导致下载失败，先运行 `sudo apt update` 再重试。

Ghostty 和 ChatGPT 安装后可从应用菜单启动，也可以在终端运行 `ghostty` 或 `chatgpt`。

安装 Codex CLI：

```bash
sudo apt install codex-cli
codex
```

首次运行按提示登录。软件包保留上游随附工具和资源，更新通过 APT 获取；软件源里只保留 Codex CLI 最新版。

## Clash Verge Rev

```bash
sudo apt install clash-verge
```

安装后从应用菜单启动 Clash Verge Rev。包名为 `clash-verge`，直接提供 GitHub 最新正式 Release 的 amd64 DEB，不重新编译或修改原包；软件源里只保留最新版。

## ChatGPT 客户端

这里提供的是 OpenAI 官方 Linux 客户端原包。使用时需要登录 ChatGPT，账户与服务按 OpenAI 的规则使用。

官方安装包默认还会添加 OpenAI 自己的 APT 软件源。如果希望只使用 debxiang，请在首次安装前创建或编辑 `/etc/default/chatgpt`，将其中的设置改为：

```text
repo_add_once="false"
```

## 移除软件源

```bash
sudo rm /etc/apt/sources.list.d/debxiang.list
sudo rm /etc/apt/keyrings/debxiang.gpg
sudo apt update
```

移除软件源不会卸载已有软件。需要卸载时，运行 `sudo apt remove` 并指定软件包名。

[软件源站点](https://xiangcao233.github.io/debxiang/) · [软件包下载](https://github.com/xiangCaO233/debxiang/releases)
