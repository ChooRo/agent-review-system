from fastapi.testclient import TestClient

from app.main import app


def test_login_and_me() -> None:
    client = TestClient(app)
    names = {"operator": "张明", "supervisor": "李华", "admin": "陈启"}
    for username, display_name in names.items():
        response = client.post(
            "/api/v1/auth/login",
            json={"username": username, "password": "ChangeMe123!"},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["user"]["roles"][0]["code"] == username

        me = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {payload['access_token']}"},
        )
        assert me.status_code == 200
        assert me.json()["display_name"] == display_name

    roles = client.get(
        "/api/v1/auth/roles",
        headers={"Authorization": f"Bearer {payload['access_token']}"},
    )
    assert {role["code"] for role in roles.json()} == set(names)
