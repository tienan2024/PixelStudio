# 素材署名 / Asset Attribution

本目录包含项目生成素材，以及保留的早期第三方开源素材。

## PixelStudio 全景底图

- `pixel-world.png`：2026-09-27 通过 Codex 内置 imagegen 为本项目生成；实际请求型号为 `gpt-image-2`。
- 原始尺寸：2172×724；文件保留原始生成结果，运行时整数采样与视口裁切。
- 完整提示词见 `pixel-world.prompt.txt`。
- 实时成员及状态动画由程序单独绘制，生成底图不包含虚构代理。

## Office worker sprites

- **作者**：emcee-flesher
- **来源**：OpenGameArt — https://opengameart.org/content/office-worker-sprites
- **许可证**：Creative Commons Attribution 4.0 International (CC-BY 4.0)
  https://creativecommons.org/licenses/by/4.0/
- **用途**：`frames/` 目录下的复古电脑（`pc_*`）与饮水机（`cooler_*`）动画帧，
  从原 sprite sheet 按 16×16 帧切分并放大至 32×32。
- **改动说明**：仅做帧切分与 2 倍最近邻放大，未修改像素内容。
  同包的职员素材未使用（办公室中的角色为项目自绘）。
