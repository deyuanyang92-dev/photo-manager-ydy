"""tests/test_collab_peer_prompt_claim.py — 「新设备加入」弹窗权先到先得 (v0.56).

peer_join_review 是 CollabService 单例信号, 协作页 CollabView 与工作台侧栏
CollabPanel 可能同时连接: 一次 emit 触达两个 slot → 双弹窗且 Yes/No 互踩。
修复: 两个 slot 入口先 claim_peer_join_prompt, 只有第一个拿到 True 弹窗。
"""
from __future__ import annotations

from app.services.collab_service import CollabService


def test_claim_first_caller_wins_second_skips():
    svc = CollabService()
    assert svc.claim_peer_join_prompt("192.168.1.8", 5050) is True
    assert svc.claim_peer_join_prompt("192.168.1.8", 5050) is False, (
        "同一次加入的第二个界面不得再弹窗"
    )


def test_claim_is_per_peer():
    svc = CollabService()
    assert svc.claim_peer_join_prompt("192.168.1.8", 5050) is True
    assert svc.claim_peer_join_prompt("192.168.1.9", 5050) is True


def test_reemit_after_review_reset_allows_new_prompt():
    """peer 退出重进(_peer_review_prompted 被 discard → 重新 emit)时应可再弹."""
    svc = CollabService()
    assert svc.claim_peer_join_prompt("192.168.1.8", 5050) is True
    # 模拟服务端在重新 emit 前重置领取状态(见 _review_peer_after_enrich)
    svc._peer_review_prompt_claimed.discard("192.168.1.8:5050")
    assert svc.claim_peer_join_prompt("192.168.1.8", 5050) is True
