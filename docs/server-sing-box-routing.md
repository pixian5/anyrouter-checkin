# 服务器 sing-box 域名路由

`sf.sbbz.tech` 复用现有 sing-box 二进制 `/usr/local/bin/bz`。系统级 TUN 透明接管服务器流量，只把 `anyrouter.top` 域名路由到订阅生成的 VMess 节点，其余流量由 `direct` 出站。签到程序不读取代理环境变量，也不负责启动、选择或连接代理。

## 运行结构

- 客户端配置：`/etc/bz/anyrouter-client.json`
- systemd 服务：`anyrouter-vmess-client.service`
- TUN 接口：`singbox0`
- 路由规则：`domain_suffix: [anyrouter.top]`
- 路由出站：当前固定 `jp7`。`urltest` 组保留用于维护时探测节点，但不用于同一次签到，以避免 WAF cookies 因出口切换失效。
- 节点配置：只保存在服务器配置中，不提交仓库

该服务不修改现有 `bz.service` 和 `xbz.service` 的服务端配置。配置变更前的备份保存在服务器 `/etc/bz/anyrouter-client.json.before-*`。

## 常用检查

```bash
/usr/local/bin/bz check -c /etc/bz/anyrouter-client.json
systemctl status anyrouter-vmess-client.service
ip address show singbox0
curl -L https://anyrouter.top/api/user/self
journalctl -u anyrouter-vmess-client.service -f
```

验证日志应同时出现 `inbound/tun[global-tun]` 和目标 VMess 出站。若显示 `outbound/direct[direct]`，说明域名规则未命中；若 VMess 已命中但 TLS 或 HTTP 失败，应在维护窗口测试订阅节点并更新固定出站，不能在同一次签到中途切换节点。

## 变更步骤

更新节点或路由前先备份配置，然后依次执行：

```bash
/usr/local/bin/bz check -c /etc/bz/anyrouter-client.json
systemctl restart anyrouter-vmess-client.service
systemctl is-active --quiet anyrouter-vmess-client.service
```

不要在 `anyrouter-checkin.service` 中设置 `HTTP_PROXY`、`CHECKIN_PROXY_URL` 或类似变量。路由职责只属于 sing-box。
