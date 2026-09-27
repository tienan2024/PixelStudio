# 素材署名 / Asset Attribution

本目录包含项目生成素材，以及保留的早期第三方开源素材。

## 模块化房间与家具（当前素材）

- 2026-09-27 通过 Codex 内置 imagegen 为本项目生成，原始 PNG 保留；当前场景未使用第三方家具图片。
- `rooms/studio-shell.png`、`rooms/lounge-shell.png`、`rooms/bedroom-shell.png`：分别为工作室、休闲厅和卧室建筑背景，原图均为 2172×724。
- 建筑背景包含墙面、窗户、地板和固定装饰；工作室、休闲厅的墙灯属于背景。可摆放家具、角色与猫由独立精灵层绘制。
- `rooms/furniture-atlas.png`：1254×1254 的透明图集，包含 16 种独立素材：书桌、显示器、座椅、服务器、绿植、沙发、咖啡机、鱼缸、床、床头柜、台灯、衣柜、书架、地毯、猫和街机。
- 完整提示词：`rooms/studio-shell.prompt.txt`、`rooms/lounge-shell.prompt.txt`、`rooms/bedroom-shell.prompt.txt`、`rooms/furniture-atlas.prompt.txt`。提示词中的期望尺寸与最终原图尺寸可能不同，以 PNG 为准。
- `rooms/furniture-atlas.json` 记录原图裁切矩形；房间 JSON 记录摆放与交互。运行时使用 Tkinter 切帧、整数最近邻采样和视口裁切，素材源文件不被改写。
- 灯光、屏幕、蒸汽等互动效果由程序叠加；真实成员由独立角色层绘制，房间不内置虚构代理。

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

## Office worker sprites（历史素材）

- **作者**：emcee-flesher
- **来源**：OpenGameArt — https://opengameart.org/content/office-worker-sprites
- **许可证**：Creative Commons Attribution 4.0 International (CC-BY 4.0)
  https://creativecommons.org/licenses/by/4.0/
- **用途**：`frames/` 目录下的复古电脑（`pc_*`）与饮水机（`cooler_*`）动画帧，
  从原 sprite sheet 按 16×16 帧切分并放大至 32×32。
- **改动说明**：仅做帧切分与 2 倍最近邻放大，未修改像素内容。
  同包的职员素材未使用；当前角色采用上面的生成式待机精灵。
