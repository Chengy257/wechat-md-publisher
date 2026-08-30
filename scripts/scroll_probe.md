---
title: 滚动排版探针（测试后可删除）
author: Cy257
---

本文用于在微信草稿箱网页中实测横向滚动行为，共 5 个变体。请逐项目测：能否在元素**内部**横向滑动查看完整内容，而不是整页左右晃动。

**【表格A】probe-t1 · 推荐滚动写法**：滚动载体为 table 外层包裹层（overflow-x:auto + -webkit-overflow-scrolling:touch + max-width:100%），表头不换行，单元格默认换行。

<section class="probe-t1"><table>
<thead>
<tr><th>注释来源</th><th>特征类型</th><th>坐标判定</th><th>相位规则</th><th>典型问题</th><th>处理建议</th></tr>
</thead>
<tbody>
<tr><td>第一行示例来源标注</td><td>几何形位特征</td><td>三坐标测量机判定</td><td>等相位面采样规则</td><td>表头过宽被裁切</td><td>外层容器承载横向滚动</td></tr>
<tr><td>第二行示例来源标注</td><td>运动学特征</td><td>激光跟踪仪判定</td><td>相位差补偿规则</td><td>单元格内容被截断</td><td>限制容器最大宽度</td></tr>
<tr><td>第三行示例来源标注</td><td>热力学特征</td><td>红外热像仪判定</td><td>相位漂移修正规则</td><td>整页被撑宽横滑</td><td>允许单元格内自然换行</td></tr>
<tr><td>第四行示例来源标注</td><td>动力学特征</td><td>高速摄影判定</td><td>相位同步触发规则</td><td>移动端滚动失效</td><td>补充惯性滚动属性</td></tr>
</tbody>
</table></section>

**【表格B】probe-t2 · 强制宽表写法**：同表格A，但所有单元格 td 强制 white-space:nowrap，表格必然横向溢出，用于验证包裹层滚动是否生效。

<section class="probe-t2"><table>
<thead>
<tr><th>注释来源</th><th>特征类型</th><th>坐标判定</th><th>相位规则</th><th>典型问题</th><th>处理建议</th></tr>
</thead>
<tbody>
<tr><td>第一行示例来源标注不换行</td><td>几何形位特征不换行</td><td>三坐标测量机判定不换行</td><td>等相位面采样规则不换行</td><td>表头过宽被裁切不换行</td><td>外层容器承载横向滚动不换行</td></tr>
<tr><td>第二行示例来源标注不换行</td><td>运动学特征不换行</td><td>激光跟踪仪判定不换行</td><td>相位差补偿规则不换行</td><td>单元格内容被截断不换行</td><td>限制容器最大宽度不换行</td></tr>
<tr><td>第三行示例来源标注不换行</td><td>热力学特征不换行</td><td>红外热像仪判定不换行</td><td>相位漂移修正规则不换行</td><td>整页被撑宽横滑不换行</td><td>允许单元格内自然换行不换行</td></tr>
<tr><td>第四行示例来源标注不换行</td><td>动力学特征不换行</td><td>高速摄影判定不换行</td><td>相位同步触发规则不换行</td><td>移动端滚动失效不换行</td><td>补充惯性滚动属性不换行</td></tr>
</tbody>
</table></section>

**【代码A】probe-c1 · 旧式写法**：滚动放在 pre 上（white-space:pre; overflow-x:auto），code 保持行内、自身不滚动（复现当前线上失效形态）。

<section class="probe-c1"><pre><code class="language-python">probe_long = "滚动载体写法探针：微信编辑器导入富文本时可能改写或剥离部分 CSS 属性，若滚动容器与内容层写法不当，长代码行会被强制折行或被截断，用户只能整页左右滑动而无法在代码块内部横向滚动查看完整内容"
probe_short_one = <span style="color:#008080">42</span>
probe_short_two = "short line"</code></pre></section>

**【代码B】probe-c2 · 推荐写法**：滚动载体为 code 本身（display:block; overflow-x:auto; white-space:pre + -webkit-overflow-scrolling:touch）。

<section class="probe-c2"><pre><code class="language-python">probe_long = "滚动载体写法探针：微信编辑器导入富文本时可能改写或剥离部分 CSS 属性，若滚动容器与内容层写法不当，长代码行会被强制折行或被截断，用户只能整页左右滑动而无法在代码块内部横向滚动查看完整内容"
probe_short_one = <span style="color:#008080">42</span>
probe_short_two = "short line"</code></pre></section>

**【代码C】probe-c3 · 降级写法**：code 上自动换行（display:block; white-space:pre-wrap; word-break:break-all），不出现横向滚动。

<section class="probe-c3"><pre><code class="language-python">probe_long = "滚动载体写法探针：微信编辑器导入富文本时可能改写或剥离部分 CSS 属性，若滚动容器与内容层写法不当，长代码行会被强制折行或被截断，用户只能整页左右滑动而无法在代码块内部横向滚动查看完整内容"
probe_short_one = <span style="color:#008080">42</span>
probe_short_two = "short line"</code></pre></section>

测试完成后请删除本篇草稿。
