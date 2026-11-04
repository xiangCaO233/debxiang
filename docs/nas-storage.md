# NAS 软件源托管

公开软件源地址：`https://gitea.xiang233.top/assets/debxiang/`。包体、索引和公钥均由 NAS 承载，签名私钥仍保留在 debsrv 的受限状态目录，绝不能复制到公开目录。

## 存储和入口

- debsrv SSH：`ssh xiang@xiang233.top -p 22022`。
- NAS 挂载点：`/mnt/nas/public2`，来源 `//192.168.31.114/public2`。
- 专用公开目录：`/mnt/nas/public2/services/debxiang`。
- 复用现有 Gitea HTTPS 入口，通过只读容器挂载暴露文件，不开放额外端口，不需要匿名下载凭据。

Gitea 的用户服务 `/home/xiang/.config/systemd/user/gitea.service` 在既有挂载基础上增加以下参数；维护服务时应保留此绑定：

```text
-v /mnt/nas/public2/services/debxiang:/data/custom/public/assets/debxiang:ro
```

修改该服务需要 `systemctl --user daemon-reload` 和 `systemctl --user restart gitea.service`，重启期间 Gitea/LFS 会短暂不可用。无需修改 Gitea 的配置文件、用户权限或已有 LFS 数据。

## 自动发布

工作流先构建并安装验证软件包，生成原密钥签名的软件源，保留 GitHub Release 归档，然后运行 `scripts/nas_publish.py`。NAS 发布成功后，Pages 仅上传独立生成的迁移说明、公钥和清单；包体不会再进入 Pages artifact。

`DEBXIANG_NAS_ROOT` 和 `DEBXIANG_NAS_MOUNT` 必须同时配置。维护器和发布器均检查挂载点，NAS 掉线时明确失败，不向本机空挂载目录回退。只有非 NAS 模式继续使用 900 MiB 的 Pages 包池保护。

包体和 `by-hash` 索引按 SHA256 校验，同名不同内容必须失败，修改包内容需要提升打包修订。临时文件在目标目录写完、同步并校验后再替换，`InRelease` 在包体及索引全部可用后发布，`manifest.json` 最后作为完成标记。APT 使用内容寻址索引避免发布窗口中的索引大小或摘要不一致。

本机构建工作集沿用现有保留策略；NAS 不自动删除已发布的软件包和内容寻址索引，仍持有旧索引的客户端可以继续下载。后续清理历史包需要单独确定保留范围。恢复时应保留 NAS 软件源与 debsrv 的原签名密钥及状态目录。

## 验收

在 debsrv 的独立源码目录运行 `python3 -m unittest discover -s tests -v`。公开验证应使用干净的 Debian 13 容器下载公钥、核对既有指纹、执行 `apt update`，并通过 APT 实际下载包体，检查版本与 SHA256。不能用本地挂载文件可读代替公网下载验收。

旧 GitHub Pages 地址不会将 APT 的包下载自动转发到 NAS，用户必须按 README 更新一次软件源地址。

参考：[Gitea 自定义静态文件](https://docs.gitea.com/administration/customizing-gitea/#serving-custom-public-files)。
