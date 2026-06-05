# -*- coding: utf-8 -*-
"""/nai 随机：给 nai_director 一个「自由发挥」的随机创作 nudge。

设计取向（贴合本插件「让内置系统自调控、减少显式指令」的理念）：
**不**在这里生成画面内容——那是 nai_director 的活，它会结合 today_state 的当日时间表 +
最近聊天上下文，自调控出此刻合理的场景。这里只提供一组**创作方向 nudge**，随机挑一条灌进
image_intent，交给 director 展开成完整 Danbooru 串。

selfie 变体强制带「自拍」字样，以触发 nai_director 模板里的自拍判定分支。

首版 nudge 池；扩充属文案打磨范畴（机制不变）。nai_draw 的 random_scene_description.py 实为
LLM 生成结果的归一/判重工具，不是场景池，故此处不直接移植，改用 director 自调控的等价做法。
"""

from __future__ import annotations

import random
from typing import Optional

# 普通随机：把画什么的决定权交给 director，只给一个发挥方向
RANDOM_SCENE_INTENTS = [
    "随便画一张钠此刻的日常画面，场景、情绪、动作你自己定",
    "突然想画钠了，挑一个能体现她当下心情的瞬间，细节你来定",
    "画一张有氛围感的钠的生活切片，光线和场景随你发挥",
    "画一张钠正在做某件日常小事的画面，自己选个有意思的角度",
    "想看钠现在的样子，结合她今天的状态画一张，场景你定",
    "画一张钠放松时的画面，地点和动作你随意安排",
]

# 随机自拍：必须含「自拍」，触发 nai_director 的 selfie 判定
RANDOM_SELFIE_INTENTS = [
    "钠突然想发张自拍，画一张她此刻的自拍，场景表情你定",
    "画一张钠的随手自拍，结合她现在可能在的地方和心情",
    "钠对着镜头自拍了一张，画面里她在做什么你来决定",
    "画一张钠的日常自拍，挑一个符合此刻时间的场景",
]


def pick_random_intent(selfie: bool = False, *, rng: Optional[random.Random] = None) -> str:
    """随机挑一条创作方向 nudge；selfie=True 时从自拍池挑。

    :param selfie: 是否走自拍构图（挑选含「自拍」的 nudge）
    :param rng: 可注入的随机源（测试用确定性 Random）；默认用全局 random
    """
    chooser = rng or random
    pool = RANDOM_SELFIE_INTENTS if selfie else RANDOM_SCENE_INTENTS
    return chooser.choice(pool)
