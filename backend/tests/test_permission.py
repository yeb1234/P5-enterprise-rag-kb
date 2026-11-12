"""权限判定纯函数用例：越权红线靠这层守住，必须可单测、无需数据库。"""
from app.core.constants import Action, Role, Sensitivity
from app.services.permission import Resource, Subject, decide

def subject(**kw) -> Subject:
    params = dict(user_id="u1", tenant_id="t1", role=Role.DEPT_USER.value,
                  department_id="d1", grants=())
    params.update(kw)
    return Subject(**params)


def resource(**kw) -> Resource:
    params = dict(tenant_id="t1", visibility="dept", sensitivity="internal", department_id="d1")
    params.update(kw)
    return Resource(**params)


def test_tenant_mismatch_always_denied():
    assert not decide(subject(), Action.READ.value, resource(tenant_id="t2")).allowed
    # 平台管理员是唯一允许跨租户的角色
    assert decide(subject(role=Role.PLATFORM_ADMIN.value), Action.READ.value,
                  resource(tenant_id="t2")).allowed


def test_explicit_deny_wins_over_everything():
    deny = {"id": "g1", "tenant_id": "t1", "subject_type": "user", "subject_id": "u1",
            "resource_type": "document", "resource_id": None, "action": "read", "effect": "deny"}
    s = subject(role=Role.TENANT_ADMIN.value, grants=(deny,))
    result = decide(s, Action.READ.value, resource())
    assert not result.allowed and result.reason == "explicit-deny"


def test_viewer_cannot_write():
    assert not decide(subject(role=Role.VIEWER.value), Action.UPLOAD.value, resource()).allowed
    assert decide(subject(role=Role.VIEWER.value), Action.READ.value, resource()).allowed


def test_sensitivity_ceiling_and_grant_override():
    secret = resource(sensitivity=Sensitivity.CONFIDENTIAL.value)
    assert not decide(subject(), Action.READ.value, secret).allowed
    allow = {"id": "g2", "tenant_id": "t1", "subject_type": "user", "subject_id": "u1",
             "resource_type": "document", "resource_id": None, "action": "read", "effect": "allow"}
    assert decide(subject(grants=(allow,)), Action.READ.value, secret).allowed
    assert decide(subject(role=Role.TENANT_ADMIN.value), Action.READ.value, secret).allowed


def test_cross_department_denied_but_public_allowed():
    other = resource(department_id="d2")
    assert not decide(subject(), Action.READ.value, other).allowed
    public = resource(department_id="d2", visibility="public")
    assert decide(subject(), Action.READ.value, public).allowed


def test_department_admin_covers_subtree():
    s = subject(role=Role.DEPT_ADMIN.value, department_path="/d1/d3/")
    assert decide(s, Action.READ.value, resource(department_id="d3")).allowed


def test_fine_grained_hook_is_disabled_by_default():
    chunk = resource(department_id="d1", scope="chunk",
                     acl={"allowed_users": ["someone-else"]})
    assert decide(subject(), Action.READ.value, chunk).allowed
