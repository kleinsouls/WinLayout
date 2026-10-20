# 上传到 GitHub 操作手册

以 WinLayout 为例。首次配置一次，以后每次同步只���要两条命令。

---

## 前置：检查环境

```bash
git --version          # 应输出 git version 2.x
git config --global user.name    # 应输出你的名字
git config --global user.email   # 应输出你的邮箱
```

**如果后两条是空的**，commit 会直接失败，先配上：

```bash
git config --global user.name "你的名字"
git config --global user.email "you@example.com"
```

> 邮箱可以用 GitHub 提供的 `noreply` 邮箱（Settings → Emails 里能看到），
> 这样公开仓库里不会暴露你的真实邮箱。

---

## 第一次上传

### 1. 在 GitHub 上建一个空仓库

打开 <https://github.com/new>

- **Repository name**：填 `WinLayout`
- **Public / Private**：按需选
- ⚠️ **不要勾 "Add a README file"** —— 你本地已经有提交了，勾了会冲突
- 也不要勾 `.gitignore`、`license`
- 点 **Create repository**

### 2. 关联远程仓库

```bash
cd WinLayout
git remote add origin https://github.com/你的用户名/WinLayout.git
```

### 3. 推送

```bash
git push -u origin main
```

`-u` 表示"建立追踪关系"，以后直接 `git push` 就行，不用再写 origin main。

---

## 认证：GitHub 不再接受账号密码

2021 年起 GitHub 关闭了密码推送。两种方案：

### 方案 A：Personal Access Token（推荐）

1. GitHub → 右上角头像 → **Settings**
2. 左侧最下面 → **Developer settings**
3. **Personal access tokens** → **Tokens (classic)** → **Generate new token (classic)**
4. Note 随便填个名字，**勾选 `repo` 权限**（这是必须的）
5. 生成后复制那串 `ghp_...` —— **只显示这一次，关掉页面就看不到了**

推送时：
- **Username**：你的 GitHub 用户名
- **Password**：粘贴那串 token

### 方案 B：GitHub CLI（一次配，长期免密）

```bash
gh auth login          # 选 GitHub.com → HTTPS → 浏览器授权
```

之后 `git push` 直接跑，不用再输任何东西。

---

## 以后每次同步（最常用）

改完代码后：

```bash
cd WinLayout
git add .
git commit -m "说明你这次改了什么"
git push
```

就这三行。`git add .` 是"把改动全部放进暂存区"，`git commit` 存一次快照，`git push` 上传。

### 提交信息怎么写

用一句话说清做了什么，不要写"更新"、"fix"这种没信息量的：

```bash
git commit -m "修复：副屏拔出后窗口跑到屏幕外"     # ✅
git commit -m "修改代码"                        # ❌
git commit -m "feat: 新增方案覆盖功能"            # ✅ 团队协作常用格式
```

---

## .gitignore：哪些文件不该上传

WinLayout 里的 `.gitignore`：

```gitignore
dist/          # 打包出的 exe，10MB，每次重打包都是二进制差异
build/         # 构建中间文件
*.spec         # PyInstaller 配置，跟着代码走就行
__pycache__/
*.pyc
*.json         # 本地配置（含你自己的窗口位置隐私）
```

**为什么要忽略 `dist/`**：exe 是构建产物，能从源码重新生成。
上传它会让仓库白白变大 10MB，而且每次重新打包都产生一堆无意义的二进制差异。

**先预览再确认**：

```bash
git add -A --dry-run     # 列出将被加入的文件，不实际执行
git status --ignored     # 看看哪些被忽略了
```

---

## 常见报错

| 报错 | 原因 | 解决 |
|---|---|---|
| `Please tell me who you are` | 没配身份 | 见文首第 2 条 |
| `remote origin already exists` | 重复添加了远程 | `git remote remove origin` 再重新 add |
| `Updates were rejected` | 远程有新提交 | 先 `git pull`，再 `git push` |
| `Authentication failed` | Token 无效或过期 | 重新生成 Token |
| `refusing to merge unrelated histories` | 本地和远程各自初始化过 | `git pull origin main --allow-unrelated-histories` |

---

## 日常好习惯

```bash
git status          # 看当前状态：哪些改了、哪些还没提交
git log --oneline   # 看提交历史
git diff            # 看具体改了什么（提交前必看）
```

**commit 要小而聚焦**：一次提交只做一件事。这样将来 `git revert` 能精确撤销某次改动，而不会连带干掉别的。

**不要提交密钥、密码、token**。一旦推到公开仓库，泄露的历史记录是删不干净的——
GitHub 有泄露密钥扫描，但密码不会自动帮你擦掉。