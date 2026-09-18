# 把这个项目发布到 GitHub 的步骤

本文件手把手说明如何把当前文件夹（`multi-agent-collab-benchmark/`）变成 GitHub 上的一个公开仓库，方便别人 fork、修改、复用。

---

## 0. 上传前先自查：别把密钥传上去

`.env` 里是你的真实 API Key（DeepSeek / LangSmith），**一旦推到公开仓库，任何人都能刷你的额度**。

本仓库已经做了两层防护：

- `.gitignore` 里已忽略 `.env`、`__pycache__/`、`reports/`、结果 JSON 等。
- 仓库里只有 `.env.example`（模板，占位符），不含真实密钥。

**上传前务必确认**：`git status` 里不应该出现 `.env`。如果 `.env` 已被 git 追踪（说明之前误加过），用下面命令把它从追踪里移除（文件仍留在本地）：

```bash
git rm --cached .env
```

---

## 1. 前置准备

- 安装 [Git](https://git-scm.com/downloads)（Windows 可选 Git Bash）。
- 注册 [GitHub](https://github.com) 账号。
- 建议配置身份信息（首次提交需要）：

```bash
git config --global user.name "你的名字"
git config --global user.email "你的邮箱@example.com"
```

---

## 2. 在本地把文件夹变成 git 仓库

打开终端，进入本文件夹：

```bash
cd multi-agent-collab-benchmark
git init
```

> 如果 PowerShell 里 `cd` 后需要切盘符，用 `cd /d E:\agent-research\multi-agent-collab-benchmark`；Git Bash 里用 `cd /e/agent-research/multi-agent-collab-benchmark`。

---

## 3. 首次提交

```bash
# 把 .env.example 复制成 .env（本地运行用，不会被 git 跟踪）
cp .env.example .env        # Windows CMD 用：copy .env.example .env

# 添加所有文件
git add .

# 看一眼将要提交的内容，确认没有 .env、没有敏感信息
git status

# 提交
git commit -m "init: 多智能体协作 benchmark 与评测套件"
```

`git status` 里应该只能看到这些文件：`README.md`、`GITHUB_SETUP.md`、`LICENSE`、`requirements.txt`、`.env.example`、`.gitignore`、`src/*`、`data/MultiAgentCollabBench.json`。**如果出现了 `.env`，立即回退**（见第 0 节）。

---

## 4. 在 GitHub 上创建远程仓库

两种方式任选：

### 方式 A：网页创建（推荐新手）

1. 登录 GitHub → 右上角 `+` → **New repository**。
2. `Repository name` 填 `multi-agent-collab-benchmark`。
3. 选 **Public**（公开，别人才能看/复用）；要私密就选 Private。
4. **不要**勾选 "Add a README / .gitignore / license"（本地已经有了，勾了会冲突）。
5. 点 **Create repository**。

创建后会跳到一个页面，复制「push an existing repository」那一栏的两条命令（形如 `git remote add origin ...` 和 `git branch -M main` + `git push`）。

### 方式 B：用命令行创建（需要 GitHub CLI）

```bash
gh auth login                 # 首次登录
gh repo create multi-agent-collab-benchmark --public --source=. --push
```

---

## 5. 关联远程仓库并推送

把方式 A 里复制的命令粘过来（替换成你自己的用户名）：

```bash
git remote add origin https://github.com/你的用户名/multi-agent-collab-benchmark.git
git branch -M main
git push -u origin main
```

推送时浏览器可能会弹出 GitHub 登录授权，完成即可。

推送成功后，打开 `https://github.com/你的用户名/multi-agent-collab-benchmark` 就能看到仓库了。

---

## 6. 之后的日常更新

改完代码后，三步同步到 GitHub：

```bash
git add .
git commit -m "描述这次改了什么"
git push
```

---

## 7. 别人怎么复用 / 提修改（协作）

- **fork + clone**：别人点仓库右上角 Fork，再 `git clone` 自己 fork 的地址即可。
- **提 PR**：别人改完推到自己的 fork，点 "Pull Request" 提交修改，你在网页上 review 后合并。
- 一句话说明放进 README，别人就知道怎么跑（本仓库 README 已写好快速开始）。

---

## 8. 安全提醒（重要）

- **密钥已泄露**怎么办：立即去 DeepSeek / LangSmith 后台吊销旧 Key、换新 Key，再改 `.env`。已经推过历史记录的话，建议 `git filter-repo` 清历史，或直接删仓库重建。
- **LICENSE 里的占位符**：`LICENSE` 文件里 `Copyright (c) 2026 <你的名字>` 请改成你的真实姓名/昵称。
- 定期 `git status` 确认 `.env`、结果 JSON、`reports/` 都没被误提交。

---

## 附：最小命令速查

```bash
git init                                  # 初始化
git add . && git commit -m "init"         # 提交
git remote add origin <仓库地址>           # 关联远程
git branch -M main                        # 主分支命名
git push -u origin main                   # 推送
git status                                # 查看状态
git log --oneline                         # 查看提交历史
```
