# -*- coding: utf-8 -*-
"""验证 confirm 误判修复：聊天内容里的"接受/加入"等词，在无弹窗视觉特征时不应触发自动空格。"""
from panel_detector import PanelDetector, Screen


def fresh_world():
    det = PanelDetector()
    det.world.screen = Screen.IN_WORLD
    return det


def reset_confirm(det):
    c = det.world.confirm
    c.value = False
    c.confidence = 0.0
    c._expire = 0.0
    c._on = c._off = 0


# 场景1：多人聊天，阿颜说"他会接受抱抱吗"，全屏 OCR、无弹窗视觉特征 → 不应触发
det = fresh_world()
det.world.dialog_visual_score = 0.0
det.world.dialog_edge_suspect = False
det.ingest_ocr([{"text": "他会接受抱抱吗", "confidence": 0.95}], scope="full")
r1 = det.world.confirm.value
print(f"场景1 全屏OCR含'接受'但无视觉特征: confirm={r1}（期望 False）")

# 场景2：同样的话，但弹窗区域边缘疑似（真弹窗的视觉特征）→ 应触发
det = fresh_world()
det.world.dialog_visual_score = 0.0
det.world.dialog_edge_suspect = True
det.ingest_ocr([{"text": "他会接受抱抱吗", "confidence": 0.95}], scope="full")
r2 = det.world.confirm.value
print(f"场景2 全屏OCR含'接受'且边缘疑似:   confirm={r2}（期望 True）")

# 场景3：弹窗加急 OCR（scope=dialog，本就是视觉疑似后才跑）→ 应触发
det = fresh_world()
det.world.dialog_visual_score = 0.0
det.world.dialog_edge_suspect = False
det.ingest_ocr([{"text": "是否接受邀请", "confidence": 0.95}], scope="dialog")
r3 = det.world.confirm.value
print(f"场景3 弹窗加急OCR(dialog)含'接受': confirm={r3}（期望 True）")

# 场景4：多人聊天含"加入"，无视觉特征 → 不应触发
det = fresh_world()
det.world.dialog_visual_score = 0.0
det.world.dialog_edge_suspect = False
det.ingest_ocr([{"text": "等下他会加入我们吗", "confidence": 0.95}], scope="full")
r4 = det.world.confirm.value
print(f"场景4 全屏OCR含'加入'但无视觉特征: confirm={r4}（期望 False）")

ok = (r1 is False and r2 is True and r3 is True and r4 is False)
print("\n结果:", "全部通过" if ok else "有失败，需检查")
