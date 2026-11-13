# WorkBuddy 自动领积分（GitHub Actions）

不开机，每天自动完成两件事：

1. **Buddy 加油站签到** —— +100/天，连签第 7 天 +1000
2. **派猫猫旅行** —— 猫空闲就派出去，回来了就领积分

全部跑在 GitHub Actions 云端，和你的电脑无关。

---

## 一、结论先说

**可行，而且已在本机实测跑通。** 但不是抄一份现成脚本就能用，有三处别人没写清的坎：

| 坑 | 实际情况 | 本仓库怎么处理 |
|---|---|---|
| 凭证加密 | WorkBuddy 5.6+ 把 `accessToken` / `refreshToken` 做成了 AES-256-GCM 信封（`$wbEncrypted`），**密钥不落盘**，`cat` 出来是乱码 | `local/extract-token.js` 复刻官方 AAD 字节序列，从客户端 native binding 取密钥解密 |
| 境外 IP | 网上流传「GitHub Actions 海外 IP 会被风控」，因此劝人改用国内云函数 | **实测不成立**。用新加坡出口 IP 请求签到接口返回 HTTP 200、`code:0`，与国内直连一致 |
| token 过期 | accessToken 28 天到期，refreshToken 约 30 天 | 401 时自动用 refreshToken 续期，新 token 存进 Actions cache，下次接着用 |

## 二、实测到的接口（2026-11-13 验证）

所有请求都要带三个头：

```
Authorization: Bearer <accessToken>
X-User-Id: <uid>
X-Domain: www.workbuddy.cn
```

| 用途 | 方法 | 路径 |
|---|---|---|
| 查签到状态 | POST | `/v2/billing/meter/checkin-activity-status` |
| 执行签到 | POST | `/v2/billing/meter/daily-checkin` |
| 查猫的状态 | GET | `/v2/activity/growth/buddy/travel/status` |
| 派猫出门 | POST | `/v2/activity/growth/buddy/travel/depart`，body `{"location_id":1}` |
| 领取旅行奖励 | GET | `/v2/activity/growth/buddy/travel/claim` |
| 刷新 token | POST | `/v2/plugin/auth/token/refresh`，头 `X-Refresh-Token` |

域名 `www.workbuddy.cn` 和 `www.codebuddy.cn` 都通，用你 auth 文件里 `auth.domain` 那个就行。

几个必须知道的细节：

- 签到接口**幂等**：已签过返回 `code:10001`，不会重复发、不会扣分。
- **领取奖励用的是 GET，不是 POST**。这点很容易踩空。
- 猫的状态只有三个：`idle`（可派）/ `traveling`（在路上）/ `arrived`（已回来待领）。
  所以脚本每跑一次只是「看一眼、按状态做一个动作」，不需要常驻轮询。
- `depart` 的 body 只认 `location_id`。目前 id=1 是咖啡馆（3 小时）。
- 一天只能正常派一次，脚本靠 `daily_limit_reached` 字段自己跳过，不用你记。

## 三、部署（5 步）

### 1. 建仓库

在 GitHub 建一个仓库（**公开**或私有都可以），把本目录（`workbuddy-auto/`）推上去，注意保留 `.github/workflows/wb.yml`。

```bash
git init -b main
git add -A
git commit -m "WorkBuddy 自动领积分"
git remote add origin https://github.com/<你的用户名>/<仓库名>.git
git push -u origin main
```

### 2. 提取本机凭证

WorkBuddy 客户端先开着、登录着，然后：

```
双击 local\extract.bat
```

成功会生成 `local\tokens.json`（内含 accessToken，**别提交进仓库、别外发**）。

### 3. 推到 GitHub Secrets

本机装了 `gh` 且已登录的话：

```bash
cd local
python push-secrets.py <你的用户名>/<仓库名>
```

没装 `gh` 就手动填。仓库页 → Settings → Secrets and variables → Actions → New repository secret：

| 名称 | 值 |
|---|---|
| `WB_TOKEN` | tokens.json 里的 `access_token` |
| `WB_UID` | `uid` |
| `WB_REFRESH` | `refresh_token` |
| `WB_DOMAIN` | `www.workbuddy.cn` |

可选项：`SERVERCHAN_KEY`（Server酱）或 `PUSHPLUS_TOKEN`（PushPlus），失败时推微信。不填就不推。

### 4. 手动跑一次验证

仓库 → Actions → **WorkBuddy 自动领积分** → Run workflow。

日志里应该看到类似：

```
[签到] 今日已签到（连签 29 天），无需重复
[猫猫] 🐾 已派出，前往咖啡馆，约 3 小时后回来领取
```

第二次跑会变成 `🚶 猫还在路上，约 N 分钟后回来`，三小时后变成 `🎁 已领回咖啡馆的旅行奖励 +7 积分`。

### 5. 不用管了

之后每小时第 7 分自动跑一次（UTC，对应北京时间每小时 :07）。签到、派出、领取都由状态机自动判断。

## 四、几个必须知道的限制

- **不是永久免维护。** accessToken 28 天到期，脚本会用 refreshToken 自动续，续到 cache 里。
  但 refreshToken 本身约 30 天到期，且服务端可能轮换。**只要 cache 丢了或续期链断了，就得重新跑一次 `extract.bat` + 推 Secrets**。
  这是官方凭证机制决定的，绕不过去。实践上大概一两个月需要开一次机。
- **公开仓库的动作分钟数不限额，但 60 天无提交会被停用定时任务。** GitHub 的规则是：公开仓库连续 60 天没有任何提交活动，scheduled workflow 会被自动禁用。本工作流内置了「每月 1 号自动提交一次 `.keepalive`」来规避，所以不用你操心。私有仓库没有这条限制，但只有 2000 分钟/月免费额度（本任务月耗约 400–500 分钟，也够）。
- **定时任务会延迟。** GitHub 的 cron 在高峰期可能晚十几分钟到半小时，属于正常现象，不影响最终收益。
- **接口随时可能变。** 这套接口是逆向客户端得到的，官方改版就可能失效。脚本失败会按你配的通知渠道告警。
- **风险自担。** 这是调用你自己账号的官方接口领自己该领的奖励，等价于手动点一下。但自动化操作严格说在用户协议灰区，极端情况下存在被限流或回收积分的可能。别拿它去做邀请、刷量之类的事。

## 五、目录结构

```
workbuddy-auto/
├── .github/workflows/wb.yml   # 定时工作流，每小时跑一次
├── wb.py                      # 主逻辑：签到 + 猫猫状态机（纯标准库，零依赖）
├── README.md
└── local/                     # 只在你自己电脑上跑，别上传
    ├── extract.bat            # 一键提取凭证
    ├── extract-token.js       # 解密客户端加密信封
    ├── push-secrets.py        # 把凭证推到 GitHub Secrets
    └── tokens.json            # 提取结果（.gitignore 已排除）
```

## 六、排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `✗ 取不到解密密钥` | 客户端没运行，或版本变了 | 打开 WorkBuddy 登录后重跑 `extract.bat` |
| 日志报 `401` 连续多天 | token 全链路失效 | 重跑 `extract.bat` 并更新 Secrets |
| `签到状态查询失败` | 活动换期 | 打开客户端看一眼加油站是否还在，接口可能改了 |
| 猫一直 `arrived` 不领 | 脚本没跑或失败了 | 看 Actions 运行记录 |
