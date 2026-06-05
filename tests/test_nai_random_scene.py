# -*- coding: utf-8 -*-
"""/nai 随机 的随机意图 nudge 选择逻辑单测。"""

import random

from services import nai_random_scene


class TestPickRandomIntent:
    def test_scene_intent_from_scene_pool(self):
        intent = nai_random_scene.pick_random_intent(selfie=False, rng=random.Random(0))
        assert intent in nai_random_scene.RANDOM_SCENE_INTENTS

    def test_selfie_intent_from_selfie_pool(self):
        intent = nai_random_scene.pick_random_intent(selfie=True, rng=random.Random(0))
        assert intent in nai_random_scene.RANDOM_SELFIE_INTENTS

    def test_selfie_intents_all_contain_selfie_keyword(self):
        # 自拍池每条都必须含「自拍」，否则触发不了 nai_director 的 selfie 判定分支
        assert all("自拍" in intent for intent in nai_random_scene.RANDOM_SELFIE_INTENTS)

    def test_pools_are_nonempty(self):
        assert nai_random_scene.RANDOM_SCENE_INTENTS
        assert nai_random_scene.RANDOM_SELFIE_INTENTS

    def test_seeded_rng_is_deterministic(self):
        a = nai_random_scene.pick_random_intent(selfie=False, rng=random.Random(42))
        b = nai_random_scene.pick_random_intent(selfie=False, rng=random.Random(42))
        assert a == b

    def test_default_rng_returns_member(self):
        # 不传 rng 时也应返回池内成员
        assert nai_random_scene.pick_random_intent(selfie=False) in nai_random_scene.RANDOM_SCENE_INTENTS
        assert nai_random_scene.pick_random_intent(selfie=True) in nai_random_scene.RANDOM_SELFIE_INTENTS
