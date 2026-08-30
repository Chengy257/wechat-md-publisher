# 主题黄金基线样例

这是一段用于主题体系黄金基线比对的引言段落，包含**粗体**、*斜体*、***粗斜体***以及行内代码 `render_preset_css("default")` 的混排。

## 二级标题：排版元素

> 这是一段引用文字：主题引擎在构建期以 string.Template 完成 $token 替换，
> 排除 CSS 变量以保证微信编辑器的兼容性。

### 三级标题：列表

无序列表：

- 第一项：基础排版
- 第二项：**代码块与表格**
- 第三项：外链转脚注

有序列表：

1. 渲染 Markdown 为原始 HTML
2. 清洗并适配微信编辑器
3. 内联主题样式并输出正文

### 三级标题：表格与对齐

| 命令 | 参数 | 说明 |
|:--- | :---: | ---:|
| `render` | `--theme` | 选择内置主题预设 |
| `draft` | `--style` | 直接指定 CSS 文件 |
| `inspect` | 无 | 检查解析结果 |

## 二级标题：代码块

下面是一个 Python 围栏代码块，其中包含一条超过 100 字符的长行，用于验证代码块的横向滚动结构：

```python
def render_preset_css(name: str) -> str:
    palette = load_palette(name)  # fail-closed: 缺键即抛 ValueError，并附带主题名与缺失键名
    partials, palette_name = THEME_PRESETS[name]
    rendered = [BASE_TEMPLATE.substitute(palette)]
    for partial_name in partials:
        rendered.append(load_partial(partial_name).substitute(palette))
    return "\n".join(rendered)
```

结尾段落：样例覆盖 h1/h2/h3、引用、有序与无序列表、带对齐列的表格、含长行的围栏代码块、行内代码、将转为脚注的外部链接[主题引擎规范](https://example.com/golden-baseline)、粗体与斜体，且不含任何图片以保证测试完全离线。
