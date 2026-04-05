# 打地鼠（Whack-a-Mole）— MIT App Inventor 项目

一个用 **MIT App Inventor 2** 构建的完整打地鼠小游戏，支持直接导入并运行于 Android 设备。

---

## 文件说明

| 文件 | 说明 |
|---|---|
| `WhackAMole.aia` | App Inventor 项目文件，直接导入即可使用 |

---

## 导入与运行

1. 打开 [MIT App Inventor](https://ai2.appinventor.mit.edu/)（需要 Google 账号）。
2. 点击菜单 **Projects → Import project (.aia) from my computer**。
3. 选择本目录下的 `WhackAMole.aia` 文件。
4. 项目加载后，点击 **Connect → AI Companion**（需手机安装 MIT AI2 Companion App），或点击 **Build → Android App (.apk)** 生成安装包。

---

## 游戏规则

- 点击「**开始游戏**」倒计时 30 秒开始。
- 地鼠随机从 9 个地洞（3×3 网格）中探出头，点击地鼠得 1 分。
- 时间结束后弹出得分对话框，可选择「**再玩一次**」或「**退出**」。
- 最高分自动保存，下次进入游戏时显示。

---

## 项目结构（.aia 内部）

```
youngandroidproject/
  project.properties          # 项目元数据
src/appinventor/ai_user/WhackAMole/
  Screen1.scm                 # 界面组件定义（JSON）
  Screen1.bky                 # Blockly 逻辑块（XML）
assets/
  mole_hidden.png             # 空洞图片（80×80）
  mole_show.png               # 地鼠图片（80×80）
```

---

## 组件清单

### 可视组件

| 组件 | 名称 | 说明 |
|---|---|---|
| Label | LabelScore | 显示当前得分 |
| Label | LabelTime | 显示剩余时间（黄色） |
| Label | LabelHighScore | 显示历史最高分 |
| Button | ButtonStart | 开始/重新开始游戏 |
| Canvas | Canvas1 | 312×312 游戏区域（绿色） |
| ImageSprite × 9 | ImageSprite1–9 | 3×3 地洞格，88×88 像素 |

### 非可视组件

| 组件 | 名称 | 说明 |
|---|---|---|
| Clock | Clock1 | 控制地鼠出现（800ms 间隔） |
| Clock | Clock2 | 倒计时（1000ms 间隔） |
| Notifier | Notifier1 | 游戏结束弹窗 |
| TinyDB | TinyDB1 | 持久化存储最高分 |

---

## 逻辑说明

### 核心变量
| 变量 | 类型 | 说明 |
|---|---|---|
| `score` | 数字 | 当前得分 |
| `timeLeft` | 数字 | 剩余秒数（初始 30） |
| `currentMole` | 数字 | 当前显示地鼠的编号（0 = 无） |
| `gameRunning` | 布尔 | 游戏是否进行中 |
| `highScore` | 数字 | 历史最高分 |

### 核心过程
| 过程 | 说明 |
|---|---|
| `StartGame()` | 重置所有状态，启动两个 Clock |
| `HideMoleSprite(moleNum)` | 将指定编号的 Sprite 图片恢复为地洞 |
| `ShowMoleSprite(moleNum)` | 将指定编号的 Sprite 图片改为地鼠 |
| `UpdateHighScore()` | 若当前得分超过最高分则更新并持久化 |

---

## 自定义建议

- **难度调整**：在 App Inventor 中修改 `Clock1` 的 `TimerInterval`（毫秒），数值越小地鼠出现越快。
- **游戏时长**：修改 `StartGame` 过程中 `timeLeft` 的初始值（默认 30 秒）。
- **换图片**：在 App Inventor 的 Media 面板上传自己的 `mole_hidden.png` / `mole_show.png`，替换内置的简单图形。
- **添加音效**：
  1. 在 Designer 添加 `Sound` 组件，上传音效文件。
  2. 在每个 `ImageSprite.Touched` 的命中逻辑末尾加上 `Sound1.Play`。
