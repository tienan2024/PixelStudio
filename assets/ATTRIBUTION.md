# 素材署名 / Asset Attribution

本目录包含项目生成素材，以及保留的早期第三方开源素材。

## 模块化房间与家具（当前素材）

- 2026-09-28 新增 `rooms/meeting-shell.png`（2172×724）和透明 `rooms/meeting-furniture.png`（2172×724），由 Codex 内置 imagegen 生成；会议桌、白板和椅子分别裁切为独立物品，原图保留。对应 `.prompt.txt` 保存完整提示词。
- 两位美少女现在固定作为 Codex / Kimi 的常驻伙伴，图像本身不表示真实子代理数量。

- 2026-09-27 通过 Codex 内置 imagegen 为本项目生成，原始 PNG 保留；当前场景未使用第三方家具图片。
- `rooms/studio-shell.png`、`rooms/lounge-shell.png`、`rooms/bedroom-shell.png`：分别为工作室、休闲厅和卧室建筑背景，原图均为 2172×724。
- 建筑背景包含墙面、窗户、地板和固定装饰；工作室、休闲厅的墙灯属于背景。可摆放家具、角色与猫由独立精灵层绘制。
- `rooms/furniture-atlas.png`：1254×1254 的透明图集，包含 16 种独立素材：书桌、显示器、座椅、服务器、绿植、沙发、咖啡机、鱼缸、床、床头柜、台灯、衣柜、书架、地毯、猫和街机。旧猫图块与裁切键保留兼容，当前房间不再摆放静态猫。
- 完整提示词：`rooms/studio-shell.prompt.txt`、`rooms/lounge-shell.prompt.txt`、`rooms/bedroom-shell.prompt.txt`、`rooms/furniture-atlas.prompt.txt`。提示词中的期望尺寸与最终原图尺寸可能不同，以 PNG 为准。
- `rooms/furniture-atlas.json` 记录原图裁切矩形；房间 JSON 记录摆放与交互。运行时使用 Tkinter 切帧、整数最近邻采样和视口裁切，素材源文件不被改写。
- 灯光、屏幕、蒸汽等互动效果由程序叠加；真实成员由独立角色层绘制，房间不内置虚构代理。

## 小猫橘子与照护物品

- 2026-09-28 通过 Codex 内置 imagegen 生成，以 `rooms/furniture-atlas.png` 为参考，沿用其中橘猫造型与木色、青绿色像素工作室风格；未引入第三方猫或照护物品图片。
- `cat-actions.png`：保留 2172×724 透明 RGBA 原图，共 12 帧，包含坐姿 1 帧、眨眼 1 帧、步行 4 帧、进食 2 帧、睡觉 2 帧、玩耍 2 帧。提示词为 `cat-actions.prompt.txt`，裁切矩形和整数采样比例为 `cat-actions.json`。
- `rooms/cat-care.png`：保留 2172×724 透明 RGBA 原图，包含六个独立图块：满粮碗、空粮碗、满水碗、空水碗、软猫窝、毛线球。提示词为 `rooms/cat-care.prompt.txt`，裁切矩形记录在 `rooms/furniture-atlas.json`。
- `cat-corner.json` 记录独立物品摆放：粮碗、水碗与毛线球在休闲厅，猫窝在卧室。只有动态小猫橘子在房间之间活动，原 `lounge-cat` / `bedroom-cat` 静态摆件已移除。
- 运行时由 Tk 切帧、整数采样及镜像，保留生成原图；喝水复用进食帧，摸摸复用待机帧。伙伴伸手、猫粮与水滴、爱心和睡眠提示由程序叠加。小猫心愿来自真实模型返回，与图像生成素材分开。

## PixelStudio 全景底图（历史素材）

- `pixel-world.png`：2026-09-27 通过 Codex 内置 imagegen 为本项目生成；实际请求型号为 `gpt-image-2`。
- 原始尺寸：2172×724；文件保留原始生成结果，当前模块化房间已不再加载此图。
- 完整提示词见 `pixel-world.prompt.txt`。
- 对应的 `../docs/pixel-world.png` 为旧版预览。实时成员及状态动画由程序单独绘制，生成底图不包含虚构代理。

## 像素美少女待机精灵

- `characters-idle.png`：2026-09-27 通过 Codex 内置 imagegen 为本项目生成，透明 RGBA 原图保留。
- 两套成年女性角色造型，每套四帧：中性、呼吸、眨眼、轻微重心变化。
- 提示词：`characters-idle.prompt.txt`；原图帧区域：`characters-idle.json`。
- 程序运行时按帧区域加载，并以整数最近邻采样制作世界人物和成员卡头像。

## 像素美少女动作精灵

- `characters-motion.png`：2026-09-28 通过 Codex 内置 imagegen，以 `characters-idle.png` 为角色参考生成；实际模型 `gpt-image-2`，原生 JSON `/v1/images/edits` 入口。保留 1774×887 透明 RGBA 原图。
- 两行分别为 Codex 与 Kimi 固定角色；每行四帧步行、两帧坐姿/点头、两帧发言/抬手。提示词和裁切区域见 `characters-motion.prompt.txt`、`characters-motion.json`。
- 运行时由 Tk 切帧、整数采样和镜像，入座/起立、呼吸和点头配合代码位移；白板、气泡与蒸汽由 `scene_meeting.py` 独立叠加。图片与场景动画均不表示真实消息文本或调用状态。

## Office worker sprites（历史素材）

- **作者**：emcee-flesher
- **来源**：OpenGameArt — https://opengameart.org/content/office-worker-sprites
- **许可证**：Creative Commons Attribution 4.0 International (CC-BY 4.0)
  https://creativecommons.org/licenses/by/4.0/
- **用途**：`frames/` 目录下的复古电脑（`pc_*`）与饮水机（`cooler_*`）动画帧，
  从原 sprite sheet 按 16×16 帧切分并放大至 32×32。
- **改动说明**：仅做帧切分与 2 倍最近邻放大，未修改像素内容。
  同包的职员素材未使用；当前角色采用上面的生成式待机精灵。
