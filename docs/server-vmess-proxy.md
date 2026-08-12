# 服务器 VMess 代理

服务器 `uk.sbbz.tech` 复用现有的 sing-box 二进制 `/usr/local/bin/bz`，仅运行隔离的 VMess 客户端代理。本文件不包含签到任务或签到部署。

## 运行结构

- 客户端配置：`/etc/bz/anyrouter-client.json`
- systemd 服务：`anyrouter-vmess-client.service`
- 本地代理：`http://127.0.0.1:20808`
- 节点选择：sing-box `urltest`，每 5 分钟探测 7 个 VMess 节点并自动选择可用节点

客户端只监听回环地址，不占用现有 `bz.service`、`xbz.service` 的公网端口，也不会代理天气推送或其它服务。

## 常用检查

```bash
systemctl status anyrouter-vmess-client.service
journalctl -u anyrouter-vmess-client.service -f
curl -L -x http://127.0.0.1:20808 https://anyrouter.top
```

节点凭据只保存在服务器 `/etc/bz/anyrouter-client.json`，不得提交到仓库或 GitHub Actions。更新节点时先备份该文件，再运行：

```bash
/usr/local/bin/bz check -c /etc/bz/anyrouter-client.json
systemctl restart anyrouter-vmess-client.service
```

## 失败处理

如果需要使用该代理的客户端出现网络超时，先检查客户端服务是否运行以及本地代理是否能返回 HTTP 状态码。sing-box 自动测速只负责选择节点；如果 7 个节点都不可用，相关客户端请求会失败。
